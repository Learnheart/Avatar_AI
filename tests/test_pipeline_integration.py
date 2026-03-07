"""
Phase 4B — Kiểm thử tích hợp pipeline backend.

Test modules làm việc với nhau: Orchestrator + VAD + Vision (mock) + GeminiClient (mock).
Không cần camera hay Gemini API key.

Chạy: cd /mnt/hdd/projects/avatar_AI && source .venv/bin/activate && python -m pytest tests/test_pipeline_integration.py -v
"""
import asyncio
import base64
import struct
import time
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import AppConfig
from backend.models import AvatarState, HeadRotation, TrackingResult, SessionState
from backend.orchestrator import Orchestrator, WSManager


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_silence_pcm(duration_sec=0.1, sample_rate=16000):
    """Generate silent PCM Int16 bytes."""
    n_samples = int(sample_rate * duration_sec)
    return struct.pack(f"<{n_samples}h", *([0] * n_samples))


def make_speech_pcm(duration_sec=0.1, sample_rate=16000, frequency=440, amplitude=20000):
    """Generate sine wave PCM Int16 bytes (simulates speech)."""
    import math
    n_samples = int(sample_rate * duration_sec)
    samples = []
    for i in range(n_samples):
        value = int(amplitude * math.sin(2 * math.pi * frequency * i / sample_rate))
        samples.append(value)
    return struct.pack(f"<{n_samples}h", *samples)


def make_config(**overrides):
    """Create test config with sensible defaults."""
    defaults = {
        "GEMINI_API_KEY": "test-key-not-real",
        "CAMERA_INDEX": 99,  # Non-existent camera
        "TRACKING_FPS": 10,
        "NO_FACE_TIMEOUT_SEC": 1.0,
        "GEMINI_IDLE_TIMEOUT_SEC": 5.0,
    }
    defaults.update(overrides)
    config = AppConfig()
    for k, v in defaults.items():
        setattr(config, k, v)
    return config


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def config():
    return make_config()


# ── Test: WSManager ──────────────────────────────────────────────────────────

class TestWSManager:
    @pytest.mark.asyncio
    async def test_connect_sends_connected_message(self):
        ws_mgr = WSManager()
        mock_ws = AsyncMock()
        session_id = await ws_mgr.connect(mock_ws)

        assert session_id.startswith("session-")
        assert ws_mgr.is_connected

        # Should have called accept() and send_json()
        mock_ws.accept.assert_called_once()
        mock_ws.send_json.assert_called_once()

        msg = mock_ws.send_json.call_args[0][0]
        assert msg["type"] == "connected"
        assert "session_id" in msg
        assert "config" in msg

    @pytest.mark.asyncio
    async def test_disconnect(self):
        ws_mgr = WSManager()
        mock_ws = AsyncMock()
        await ws_mgr.connect(mock_ws)
        assert ws_mgr.is_connected

        await ws_mgr.disconnect()
        assert not ws_mgr.is_connected

    @pytest.mark.asyncio
    async def test_send_when_disconnected_does_nothing(self):
        ws_mgr = WSManager()
        # Should not raise
        await ws_mgr.send({"type": "test"})

    @pytest.mark.asyncio
    async def test_send_error_disconnects(self):
        ws_mgr = WSManager()
        mock_ws = AsyncMock()
        mock_ws.send_json.side_effect = [None, Exception("Connection closed")]
        await ws_mgr.connect(mock_ws)

        await ws_mgr.send({"type": "error_trigger"})
        assert not ws_mgr.is_connected


# ── Test: State Machine ─────────────────────────────────────────────────────

class TestStateMachine:
    @pytest.mark.asyncio
    async def test_initial_state_is_idle(self, config):
        orch = Orchestrator(config)
        assert orch.session_state.avatar_state == AvatarState.IDLE

    @pytest.mark.asyncio
    async def test_transition_idle_to_tracking(self, config):
        orch = Orchestrator(config)
        mock_ws = AsyncMock()
        await orch.ws_manager.connect(mock_ws)

        await orch._transition(AvatarState.TRACKING)
        assert orch.session_state.avatar_state == AvatarState.TRACKING

    @pytest.mark.asyncio
    async def test_transition_tracking_to_listening(self, config):
        orch = Orchestrator(config)
        orch.session_state.avatar_state = AvatarState.TRACKING

        mock_ws = AsyncMock()
        await orch.ws_manager.connect(mock_ws)

        await orch._transition(AvatarState.LISTENING)
        assert orch.session_state.avatar_state == AvatarState.LISTENING

    @pytest.mark.asyncio
    async def test_transition_same_state_noop(self, config):
        orch = Orchestrator(config)
        orch.session_state.avatar_state = AvatarState.TRACKING
        mock_ws = AsyncMock()
        await orch.ws_manager.connect(mock_ws)

        # Reset call count after connect
        mock_ws.send_json.reset_mock()

        await orch._transition(AvatarState.TRACKING)
        # No WS message sent for same-state transition
        mock_ws.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_speaking_sets_is_speaking_flag(self, config):
        orch = Orchestrator(config)
        mock_ws = AsyncMock()
        await orch.ws_manager.connect(mock_ws)

        await orch._transition(AvatarState.SPEAKING)
        assert orch.session_state.is_speaking is True

        await orch._transition(AvatarState.TRACKING)
        assert orch.session_state.is_speaking is False


# ── Test: Audio Pipeline ────────────────────────────────────────────────────

class TestAudioPipeline:
    @pytest.mark.asyncio
    async def test_silence_does_not_trigger_speech(self, config):
        orch = Orchestrator(config)
        orch._vad.load()

        silence = make_silence_pcm(duration_sec=0.5)
        await orch._process_audio(silence)

        # Should stay IDLE
        assert orch.session_state.avatar_state == AvatarState.IDLE

    @pytest.mark.asyncio
    async def test_speaking_state_returns_early_no_double_vad(self, config):
        """ISS-09: Verify audio is not double-processed through VAD in SPEAKING state."""
        orch = Orchestrator(config)
        orch._vad.load()
        orch.session_state.avatar_state = AvatarState.SPEAKING

        # Spy on VAD.process_audio to count calls
        original_process = orch._vad.process_audio
        call_count = 0
        def counting_process(pcm_bytes):
            nonlocal call_count
            call_count += 1
            return original_process(pcm_bytes)
        orch._vad.process_audio = counting_process

        pcm = make_silence_pcm(duration_sec=0.1)
        await orch._process_audio(pcm)

        # Should only be called once (barge-in check), not twice
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_handle_client_message_audio_chunk(self, config):
        orch = Orchestrator(config)
        orch._vad.load()

        silence = make_silence_pcm(duration_sec=0.1)
        b64 = base64.b64encode(silence).decode()

        await orch.handle_client_message({
            "type": "audio_chunk",
            "audio_data": b64,
        })
        # Should not crash, should stay IDLE with silence
        assert orch.session_state.avatar_state == AvatarState.IDLE

    @pytest.mark.asyncio
    async def test_handle_client_message_unknown_type(self, config):
        orch = Orchestrator(config)
        # Should not crash
        await orch.handle_client_message({"type": "unknown_type"})


# ── Test: Tracking Loop (with mocked Vision) ────────────────────────────────

class TestTrackingLoop:
    @pytest.mark.asyncio
    async def test_face_detected_transitions_to_tracking(self, config):
        orch = Orchestrator(config)
        mock_ws = AsyncMock()
        await orch.ws_manager.connect(mock_ws)

        # Mock vision to return face-detected tracking result
        tracking = TrackingResult(
            rotation=HeadRotation(pitch=0.1, yaw=-0.2, roll=0.0),
            face_detected=True,
            face_count=1,
        )

        # Simulate _tracking_loop behavior
        orch.session_state.last_head_rotation = tracking.rotation
        orch.session_state.last_face_detected_at = time.time()
        if tracking.face_detected and orch.session_state.avatar_state == AvatarState.IDLE:
            await orch._transition(AvatarState.TRACKING)

        assert orch.session_state.avatar_state == AvatarState.TRACKING

    @pytest.mark.asyncio
    async def test_no_face_stays_idle(self, config):
        orch = Orchestrator(config)

        tracking = TrackingResult(
            rotation=HeadRotation(),
            face_detected=False,
            face_count=0,
        )

        # Simulate _tracking_loop behavior — no face, no transition
        if tracking.face_detected and orch.session_state.avatar_state == AvatarState.IDLE:
            await orch._transition(AvatarState.TRACKING)

        assert orch.session_state.avatar_state == AvatarState.IDLE


# ── Test: Timeout Monitor ───────────────────────────────────────────────────

class TestTimeoutMonitor:
    @pytest.mark.asyncio
    async def test_no_face_timeout_transitions_to_idle(self, config):
        config.NO_FACE_TIMEOUT_SEC = 0.1  # Very short for testing
        orch = Orchestrator(config)
        mock_ws = AsyncMock()
        await orch.ws_manager.connect(mock_ws)

        # Set to TRACKING with face detected a while ago
        orch.session_state.avatar_state = AvatarState.TRACKING
        orch.session_state.last_face_detected_at = time.time() - 1.0  # 1 second ago

        # Simulate one timeout monitor iteration
        now = time.time()
        state = orch.session_state.avatar_state
        if state == AvatarState.TRACKING:
            last_face = orch.session_state.last_face_detected_at
            if last_face and (now - last_face) > config.NO_FACE_TIMEOUT_SEC:
                await orch._transition(AvatarState.IDLE)

        assert orch.session_state.avatar_state == AvatarState.IDLE


# ── Test: Gemini Response Handling ───────────────────────────────────────────

class TestGeminiResponseHandling:
    @pytest.mark.asyncio
    async def test_audio_response_transitions_to_speaking(self, config):
        orch = Orchestrator(config)
        orch.session_state.avatar_state = AvatarState.LISTENING
        mock_ws = AsyncMock()
        await orch.ws_manager.connect(mock_ws)

        # Simulate Gemini audio response
        fake_audio = b"\x00\x01" * 100
        await orch._on_gemini_response("audio", fake_audio, "audio/pcm;rate=24000")

        assert orch.session_state.avatar_state == AvatarState.SPEAKING
        assert len(orch._audio_buffer) == 1

    @pytest.mark.asyncio
    async def test_text_response_accumulates(self, config):
        orch = Orchestrator(config)

        await orch._on_gemini_response("text", "Xin ", None)
        await orch._on_gemini_response("text", "chào!", None)

        assert orch._accumulated_text == "Xin chào!"

    @pytest.mark.asyncio
    async def test_turn_complete_transitions_to_tracking(self, config):
        orch = Orchestrator(config)
        orch.session_state.avatar_state = AvatarState.SPEAKING
        mock_ws = AsyncMock()
        await orch.ws_manager.connect(mock_ws)

        orch._accumulated_text = "Test"

        await orch._on_gemini_response("turn_complete", None, None)

        assert orch.session_state.avatar_state == AvatarState.TRACKING
        assert orch._accumulated_text == ""
        assert len(orch._audio_buffer) == 0

    @pytest.mark.asyncio
    async def test_turn_complete_sends_final_speech_output(self, config):
        orch = Orchestrator(config)
        orch.session_state.avatar_state = AvatarState.SPEAKING
        mock_ws = AsyncMock()
        await orch.ws_manager.connect(mock_ws)
        mock_ws.send_json.reset_mock()

        orch._accumulated_text = "Xin chào"

        await orch._on_gemini_response("turn_complete", None, None)

        # Should have sent speech_output with is_final=True + tracking_update
        calls = mock_ws.send_json.call_args_list
        speech_msgs = [c[0][0] for c in calls if c[0][0].get("type") == "speech_output"]
        assert any(m["is_final"] is True for m in speech_msgs)


# ── Test: Barge-in ───────────────────────────────────────────────────────────

class TestBargeIn:
    @pytest.mark.asyncio
    async def test_barge_in_sends_interrupt_and_clears_buffer(self, config):
        orch = Orchestrator(config)
        mock_ws = AsyncMock()
        await orch.ws_manager.connect(mock_ws)

        orch.session_state.avatar_state = AvatarState.SPEAKING
        orch._audio_buffer = [b"chunk1", b"chunk2"]

        await orch._handle_barge_in()

        assert orch.session_state.avatar_state == AvatarState.LISTENING
        assert len(orch._audio_buffer) == 0

        # Check interrupt message was sent
        calls = mock_ws.send_json.call_args_list
        interrupt_msgs = [c[0][0] for c in calls if c[0][0].get("type") == "interrupt"]
        assert len(interrupt_msgs) == 1


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
