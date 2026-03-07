"""
Phase 4B — Kiểm thử tích hợp WebSocket endpoint.

Test FastAPI app với httpx TestClient:
  - /health endpoint
  - /ws WebSocket connection + message flow

Chạy: cd /mnt/hdd/projects/avatar_AI && source .venv/bin/activate && python -m pytest tests/test_ws_integration.py -v
"""
import asyncio
import base64
import struct
import sys
import os
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_silence_pcm(duration_sec=0.05, sample_rate=16000):
    """Generate silent PCM Int16 bytes."""
    n_samples = int(sample_rate * duration_sec)
    return struct.pack(f"<{n_samples}h", *([0] * n_samples))


# ── Mock Vision module to avoid camera dependency ────────────────────────────

class MockVisionModule:
    """Replaces VisionModule to avoid needing a camera."""
    def __init__(self, config):
        self._thread = MagicMock()
        self._thread.is_alive.return_value = True
        self._tracking_queue = asyncio.Queue(maxsize=2)

    async def start(self):
        pass

    async def stop(self):
        pass

    async def get_tracking(self):
        # Block forever (tracking loop will timeout)
        await asyncio.sleep(100)

    async def get_snapshot(self):
        return None


# ── Tests ────────────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_health_returns_ok(self):
        with patch("backend.orchestrator.VisionModule", MockVisionModule):
            # Need to reimport to apply mock
            import importlib
            import backend.orchestrator
            importlib.reload(backend.orchestrator)
            import backend.main
            importlib.reload(backend.main)

            client = TestClient(backend.main.app)
            resp = client.get("/health")

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "avatar_state" in data
            assert "uptime_seconds" in data
            assert "camera_connected" in data

            # Cleanup
            importlib.reload(backend.orchestrator)
            importlib.reload(backend.main)


class TestWebSocketEndpoint:
    def test_ws_connect_receives_connected_message(self):
        with patch("backend.orchestrator.VisionModule", MockVisionModule):
            import importlib
            import backend.orchestrator
            importlib.reload(backend.orchestrator)
            import backend.main
            importlib.reload(backend.main)

            client = TestClient(backend.main.app)
            with client.websocket_connect("/ws") as ws:
                msg = ws.receive_json()
                assert msg["type"] == "connected"
                assert "session_id" in msg
                assert "config" in msg
                assert msg["config"]["tracking_fps"] == 30
                assert msg["config"]["audio_format"] == "pcm_16khz_mono"

            importlib.reload(backend.orchestrator)
            importlib.reload(backend.main)

    def test_ws_send_audio_chunk(self):
        with patch("backend.orchestrator.VisionModule", MockVisionModule):
            import importlib
            import backend.orchestrator
            importlib.reload(backend.orchestrator)
            import backend.main
            importlib.reload(backend.main)

            client = TestClient(backend.main.app)
            with client.websocket_connect("/ws") as ws:
                # Receive connected message first
                msg = ws.receive_json()
                assert msg["type"] == "connected"

                # Send audio chunk
                silence = make_silence_pcm(duration_sec=0.05)
                b64 = base64.b64encode(silence).decode()
                ws.send_json({
                    "type": "audio_chunk",
                    "audio_data": b64,
                })

                # Should not crash the server
                # (No response expected for silence input)

            importlib.reload(backend.orchestrator)
            importlib.reload(backend.main)

    def test_ws_protocol_field_names(self):
        """Verify WS protocol uses correct field names per CODING_RULES.md."""
        with patch("backend.orchestrator.VisionModule", MockVisionModule):
            import importlib
            import backend.orchestrator
            importlib.reload(backend.orchestrator)
            import backend.main
            importlib.reload(backend.main)

            client = TestClient(backend.main.app)
            with client.websocket_connect("/ws") as ws:
                msg = ws.receive_json()

                # connected message format
                assert msg["type"] == "connected"
                assert isinstance(msg["session_id"], str)
                assert isinstance(msg["config"], dict)

            importlib.reload(backend.orchestrator)
            importlib.reload(backend.main)


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
