import asyncio
import json
import logging
from typing import AsyncIterator

import pytest

from backend.config import AppConfig
from backend.conversation.stt.base import STTError
from backend.models import AudioEvent, AvatarState, DoneEvent, TrackingResult, TranscriptEvent
from backend.orchestrator import Orchestrator
from backend.orchestrator.state import can_transition
from backend.utils.logging import JsonFormatter
from tests.unit.fakes import (
    END,
    SILENCE,
    START,
    START_END,
    TTS_RATE,
    FakeWebSocket,
    audio_message,
    build_orchestrator,
    chunk,
    wait_until,
)


@pytest.fixture
def config() -> AppConfig:
    return AppConfig(
        GROQ_API_KEY="test-key",
        TRACKING_FPS=200,
        VAD_PREROLL_MS=40,  # two 20 ms chunks
        PLAYBACK_END_TIMEOUT_SEC=0.05,
    )


@pytest.fixture
async def orch(config) -> AsyncIterator[Orchestrator]:
    orchestrator = build_orchestrator(config)
    await orchestrator.start()
    yield orchestrator
    await orchestrator.stop()


@pytest.fixture
async def ws(orch) -> FakeWebSocket:
    websocket = FakeWebSocket()
    await orch.handle_connect(websocket)
    return websocket


async def _send(orch: Orchestrator, *chunks: bytes) -> None:
    for pcm in chunks:
        await orch.handle_client_message(audio_message(pcm))


async def _reach(orch: Orchestrator, state: AvatarState) -> None:
    await wait_until(lambda: orch.state == state)


def _states(ws: FakeWebSocket) -> list[str]:
    """The state changes after the handshake (`connected` + the current `state`)."""
    return [m["state"] for m in ws.of_type("state")][1:]


class _LogCapture:
    """Collects the orchestrator's log records while a test runs."""

    def __init__(self) -> None:
        self.records: list[logging.LogRecord] = []
        self._handler = logging.Handler()
        self._handler.emit = self.records.append
        self._logger = logging.getLogger("avatar.orchestrator")

    def __enter__(self) -> "_LogCapture":
        self._logger.addHandler(self._handler)
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._logger.removeHandler(self._handler)

    def turn_failures(self) -> list[logging.LogRecord]:
        return [
            r for r in self.records if isinstance(r.msg, dict) and r.msg["msg"] == "Turn failed"
        ]


# ── Lifecycle ─────────────────────────────────────────────────────────────

async def test_start_loads_models_and_starts_the_camera(orch):
    assert orch._vad.loaded
    assert orch._engine.loaded
    assert orch._tracker.loaded
    assert orch._camera.started
    assert orch.state == AvatarState.IDLE


async def test_stop_releases_the_models_and_the_camera(config):
    orchestrator = build_orchestrator(config)
    await orchestrator.start()

    await orchestrator.stop()

    assert not orchestrator._engine.loaded
    assert not orchestrator._tracker.loaded
    assert not orchestrator._camera.started


async def test_stop_releases_everything_else_when_one_release_fails(config):
    orchestrator = build_orchestrator(config)
    await orchestrator.start()

    def broken_stop() -> None:
        raise RuntimeError("camera driver hung")

    orchestrator._camera.stop = broken_stop
    orchestrator._tracker.close = broken_stop

    await orchestrator.stop()

    assert not orchestrator._engine.loaded
    assert orchestrator._vad_executor._shutdown
    assert orchestrator._vision_executor._shutdown


# ── Happy path ────────────────────────────────────────────────────────────

async def test_full_turn_goes_idle_listening_thinking_speaking_idle(orch, ws):
    await _send(orch, chunk(START), chunk(SILENCE), chunk(END))
    await _reach(orch, AvatarState.SPEAKING)
    await wait_until(lambda: any(m["is_final"] for m in ws.of_type("speech_output")))
    await orch.handle_client_message(json.dumps({"type": "playback_ended"}))
    await _reach(orch, AvatarState.IDLE)

    assert _states(ws) == ["listening", "thinking", "speaking", "idle"]
    speech = ws.of_type("speech_output")
    assert [(m["text_content"], m["is_final"]) for m in speech] == [
        ("Xin chào.", False), ("", False), ("", True),
    ]
    assert all(m["audio_mime_type"] == f"audio/pcm;rate={TTS_RATE}" for m in speech)
    assert speech[0]["audio_data"] and speech[-1]["audio_data"] == ""
    # `speaking` is announced before the first audio message.
    turn = ws.turn_messages()
    assert turn.index({"type": "state", "state": "speaking"}) < turn.index(speech[0])


async def test_preroll_is_included_in_the_utterance(orch, ws):
    # Pre-roll holds 2 chunks: the oldest silence chunk falls out, the rest is kept.
    await _send(orch, chunk(SILENCE, 7), chunk(SILENCE, 8), chunk(START, 9))
    await _reach(orch, AvatarState.LISTENING)
    await _send(orch, chunk(SILENCE, 10), chunk(END, 11))
    await _reach(orch, AvatarState.SPEAKING)

    assert orch._engine.utterances == [
        chunk(SILENCE, 8) + chunk(START, 9) + chunk(SILENCE, 10) + chunk(END, 11)
    ]


async def test_utterance_is_capped_at_stt_max_utterance_sec(orch, ws, config):
    config.STT_MAX_UTTERANCE_SEC = 0.05  # 1600 bytes = 2.5 chunks

    await _send(orch, chunk(START), chunk(SILENCE), chunk(SILENCE), chunk(SILENCE), chunk(END))
    await _reach(orch, AvatarState.SPEAKING)

    assert len(orch._engine.utterances[0]) == 1600


async def test_odd_length_and_empty_chunks_are_dropped(orch, ws):
    odd = chunk(SILENCE, 9)[:-1]

    await _send(orch, odd, chunk(START, 1), odd, b"", chunk(END, 2))
    await _reach(orch, AvatarState.SPEAKING)

    assert orch._engine.utterances == [chunk(START, 1) + chunk(END, 2)]
    assert ws.of_type("error") == []


async def test_start_and_end_in_one_chunk_start_a_turn(orch, ws):
    await _send(orch, chunk(START_END))
    await _reach(orch, AvatarState.SPEAKING)

    assert _states(ws) == ["listening", "thinking", "speaking"]


async def test_empty_transcript_returns_to_idle_without_speech(orch, ws):
    orch._engine.events = [TranscriptEvent(""), DoneEvent()]

    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: _states(ws) == ["listening", "thinking", "idle"])

    assert ws.of_type("speech_output", "error") == []


async def test_text_only_answer_skips_speaking(orch, ws):
    orch._engine.events = [
        TranscriptEvent("xin chào"), AudioEvent("Xin chào.", b"", TTS_RATE), DoneEvent(),
    ]

    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: _states(ws) == ["listening", "thinking", "idle"])

    assert [(m["text_content"], m["audio_data"], m["is_final"])
            for m in ws.of_type("speech_output")] == [("Xin chào.", "", False), ("", "", True)]


# ── Barge-in ──────────────────────────────────────────────────────────────

async def test_barge_in_while_thinking_cancels_the_turn_and_sends_interrupt(orch, ws):
    orch._engine.gate = asyncio.Event()  # the turn hangs in `thinking`
    await _send(orch, chunk(START), chunk(END))
    await _reach(orch, AvatarState.THINKING)
    await wait_until(lambda: len(orch._engine.utterances) == 1)

    await _send(orch, chunk(START))
    await _reach(orch, AvatarState.LISTENING)

    assert orch._engine.closed == 1
    assert _states(ws) == ["listening", "thinking", "listening"]
    turn = ws.turn_messages()
    assert turn[-2:] == [{"type": "interrupt"}, {"type": "state", "state": "listening"}]
    assert ws.of_type("speech_output", "error") == []


async def test_barge_in_while_speaking_cancels_the_turn_and_sends_interrupt(orch, ws, config):
    config.PLAYBACK_END_TIMEOUT_SEC = 30  # stay in `speaking`: no playback_ended arrives
    await _send(orch, chunk(START), chunk(END))
    await _reach(orch, AvatarState.SPEAKING)

    await _send(orch, chunk(START))
    await _reach(orch, AvatarState.LISTENING)

    assert _states(ws) == ["listening", "thinking", "speaking", "listening"]
    assert ws.turn_messages()[-2:] == [
        {"type": "interrupt"}, {"type": "state", "state": "listening"},
    ]

    # The interrupted turn is gone: the next utterance runs a fresh turn.
    await _send(orch, chunk(END))
    await _reach(orch, AvatarState.SPEAKING)
    assert len(orch._engine.utterances) == 2


# ── Failures ──────────────────────────────────────────────────────────────

async def test_turn_timeout_sends_error_and_returns_to_idle(orch, ws, config):
    config.TURN_TIMEOUT_SEC = 0.05
    orch._engine.gate = asyncio.Event()

    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: ws.of_type("error") != [])
    await _reach(orch, AvatarState.IDLE)

    assert ws.of_type("error")[0]["code"] == "turn_timeout"
    assert orch._engine.closed == 1
    assert _states(ws) == ["listening", "thinking", "idle"]


async def test_engine_exception_sends_error_and_returns_to_idle(orch, ws):
    orch._engine.error = RuntimeError("stt exploded")

    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: ws.of_type("error") != [])
    await _reach(orch, AvatarState.IDLE)

    error = ws.of_type("error")[0]
    assert error["code"] == "turn_failed"
    assert error["message"]
    assert _states(ws) == ["listening", "thinking", "idle"]

    # The state machine is not wedged: a new turn still works.
    orch._engine.error = None
    await _send(orch, chunk(START), chunk(END))
    await _reach(orch, AvatarState.SPEAKING)


async def test_unexpected_turn_failure_is_logged_with_its_traceback(orch, ws):
    orch._engine.error = RuntimeError("stt exploded")

    with _LogCapture() as log:
        await _send(orch, chunk(START), chunk(END))
        await wait_until(lambda: ws.of_type("error") != [])

    (failed,) = log.turn_failures()
    assert failed.exc_info is not None
    assert failed.exc_info[0] is RuntimeError


async def test_provider_turn_failure_is_logged_without_a_traceback(orch, ws):
    orch._engine.error = STTError("Groq Whisper request failed: OpenAIError (status 403)")

    with _LogCapture() as log:
        await _send(orch, chunk(START), chunk(END))
        await wait_until(lambda: ws.of_type("error") != [])

    (failed,) = log.turn_failures()
    assert not failed.exc_info


async def test_provider_response_body_reaches_neither_the_client_nor_the_log(orch, ws):
    body = "<html>" + "<p>blocked by proxy</p>" * 1000
    try:
        raise STTError("Groq Whisper request failed: OpenAIError (status 403)") from OSError(body)
    except STTError as e:
        orch._engine.error = e
    with _LogCapture() as log:
        await _send(orch, chunk(START), chunk(END))
        await wait_until(lambda: ws.of_type("error") != [])

    assert "blocked by proxy" not in json.dumps(ws.sent)
    failed = log.turn_failures()
    assert len(failed) == 1
    logged = JsonFormatter().format(failed[0])
    assert "status 403" in logged
    assert "blocked by proxy" not in logged


async def test_failure_after_audio_was_sent_interrupts_playback_before_the_error(orch, ws):
    orch._engine.error = RuntimeError("tts exploded")
    orch._engine.error_after = AudioEvent

    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: ws.of_type("error") != [])
    await _reach(orch, AvatarState.IDLE)

    assert [m["type"] for m in ws.turn_messages()[-3:]] == ["interrupt", "error", "state"]
    assert _states(ws) == ["listening", "thinking", "speaking", "idle"]


async def test_failure_before_any_audio_sends_no_interrupt(orch, ws):
    orch._engine.error = RuntimeError("stt exploded")

    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: ws.of_type("error") != [])

    assert ws.of_type("interrupt") == []


# ── Playback end ──────────────────────────────────────────────────────────

async def test_playback_ended_ends_speaking(orch, ws, config):
    config.PLAYBACK_END_TIMEOUT_SEC = 30
    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: any(m["is_final"] for m in ws.of_type("speech_output")))
    assert orch.state == AvatarState.SPEAKING

    await orch.handle_client_message(json.dumps({"type": "playback_ended"}))
    await _reach(orch, AvatarState.IDLE)


async def test_playback_timeout_ends_speaking(orch, ws):
    # 20 ms of audio + 50 ms grace, and the client never answers.
    await _send(orch, chunk(START), chunk(END))
    await _reach(orch, AvatarState.SPEAKING)

    await _reach(orch, AvatarState.IDLE)

    assert _states(ws) == ["listening", "thinking", "speaking", "idle"]
    assert ws.of_type("error") == []


async def test_stale_playback_ended_does_not_end_the_next_turn(orch, ws, config):
    config.PLAYBACK_END_TIMEOUT_SEC = 30
    await orch.handle_client_message(json.dumps({"type": "playback_ended"}))  # while idle

    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: any(m["is_final"] for m in ws.of_type("speech_output")))
    await asyncio.sleep(0.05)

    assert orch.state == AvatarState.SPEAKING


async def test_playback_ended_of_the_previous_turn_does_not_end_the_next_one(orch, ws, config):
    config.PLAYBACK_END_TIMEOUT_SEC = 30

    def finals() -> int:
        return sum(m["is_final"] for m in ws.of_type("speech_output"))

    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: finals() == 1)
    await orch.handle_client_message(json.dumps({"type": "playback_ended"}))
    await _reach(orch, AvatarState.IDLE)

    # The second turn gets no playback_ended of its own.
    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: finals() == 2)
    await asyncio.sleep(0.05)

    assert orch.state == AvatarState.SPEAKING


# ── Disconnect ────────────────────────────────────────────────────────────

async def test_disconnect_cancels_the_turn_and_resets_state(orch, ws):
    orch._engine.gate = asyncio.Event()
    await _send(orch, chunk(START), chunk(END))
    await _reach(orch, AvatarState.THINKING)
    await wait_until(lambda: len(orch._engine.utterances) == 1)

    await orch.handle_disconnect(ws)

    assert orch.state == AvatarState.IDLE
    assert orch._engine.closed == 1
    assert not orch.ws_manager.is_connected
    assert orch._vad.resets == 2  # on connect and on disconnect


async def test_disconnect_while_listening_drops_the_utterance(orch, ws):
    await _send(orch, chunk(START), chunk(SILENCE))
    await _reach(orch, AvatarState.LISTENING)

    await orch.handle_disconnect(ws)
    assert orch.state == AvatarState.IDLE

    second = FakeWebSocket()
    await orch.handle_connect(second)
    await _send(orch, chunk(START_END, 5))
    await _reach(orch, AvatarState.SPEAKING)
    assert orch._engine.utterances == [chunk(START_END, 5)]


async def test_disconnect_of_a_replaced_socket_keeps_the_new_session(orch, ws):
    second = FakeWebSocket()
    await orch.handle_connect(second)
    assert ws.closed

    await orch.handle_disconnect(ws)

    assert orch.ws_manager.is_connected
    assert second.of_type("connected")


async def test_reconnect_resets_the_turn_without_telling_the_replaced_socket(orch, ws):
    orch._engine.gate = asyncio.Event()
    await _send(orch, chunk(START), chunk(END))
    await _reach(orch, AvatarState.THINKING)
    await wait_until(lambda: len(orch._engine.utterances) == 1)

    second = FakeWebSocket()
    await orch.handle_connect(second)

    assert ws.closed
    assert _states(ws) == ["listening", "thinking"]  # no `idle` for a client that is gone
    assert orch.state == AvatarState.IDLE
    assert orch._engine.closed == 1
    # The new client is told the state itself: the reset's `idle` reached no socket.
    assert [m for m in second.sent if m["type"] != "tracking_update"][1:] == [
        {"type": "state", "state": "idle"},
    ]
    assert second.sent[0]["type"] == "connected"


async def test_connect_sends_the_current_state_right_after_connected(orch, ws):
    handshake = [m for m in ws.sent if m["type"] != "tracking_update"]

    assert [m["type"] for m in handshake] == ["connected", "state"]
    assert handshake[1] == {"type": "state", "state": "idle"}


async def test_audio_of_the_replaced_socket_starts_no_turn_while_the_new_one_is_accepted(orch, ws):
    accepting = asyncio.Event()

    class SlowWebSocket(FakeWebSocket):
        async def accept(self) -> None:
            await accepting.wait()
            await super().accept()

    second = SlowWebSocket()
    connect = asyncio.create_task(orch.handle_connect(second))
    await wait_until(lambda: ws.closed)
    await _send(orch, chunk(START))  # still in flight from the replaced client
    await asyncio.sleep(0.02)
    accepting.set()
    await connect
    await asyncio.sleep(0.02)

    assert orch.state == AvatarState.IDLE
    assert _states(ws) == [] and _states(second) == []


async def test_disconnect_after_a_failed_send_still_cancels_the_turn(orch, ws):
    orch._engine.gate = asyncio.Event()
    await _send(orch, chunk(START), chunk(END))
    await _reach(orch, AvatarState.THINKING)
    await wait_until(lambda: len(orch._engine.utterances) == 1)

    ws.broken = True  # the tracking stream is the first to notice
    await wait_until(lambda: not orch.ws_manager.is_connected)
    assert ws.closed
    await orch.handle_disconnect(ws)

    assert orch.state == AvatarState.IDLE
    assert orch._engine.closed == 1


async def test_disconnect_during_a_barge_in_still_ends_idle(orch, ws):
    orch._engine.gate = asyncio.Event()
    orch._engine.close_gate = asyncio.Event()
    await _send(orch, chunk(START), chunk(END))
    await _reach(orch, AvatarState.THINKING)
    await wait_until(lambda: len(orch._engine.utterances) == 1)

    await _send(orch, chunk(START))  # barge-in: the audio worker waits for the turn to close
    await wait_until(lambda: orch._turn_task is None)
    disconnect = asyncio.create_task(orch.handle_disconnect(ws))
    await asyncio.sleep(0.02)
    orch._engine.close_gate.set()
    await disconnect
    await asyncio.sleep(0.02)  # let the audio worker finish the barge-in

    assert orch.state == AvatarState.IDLE
    assert orch._engine.closed == 1


async def test_audio_arriving_during_a_disconnect_is_dropped(orch, ws):
    orch._engine.gate = asyncio.Event()
    orch._engine.close_gate = asyncio.Event()
    await _send(orch, chunk(START), chunk(END))
    await _reach(orch, AvatarState.THINKING)
    await wait_until(lambda: len(orch._engine.utterances) == 1)

    disconnect = asyncio.create_task(orch.handle_disconnect(ws))
    await wait_until(lambda: orch._turn_task is None)  # the reset waits for the turn to close
    await _send(orch, chunk(START))
    await asyncio.sleep(0.02)
    orch._engine.close_gate.set()
    await disconnect
    await asyncio.sleep(0.02)  # let the audio worker handle the late chunk

    assert orch.state == AvatarState.IDLE


async def test_late_disconnect_of_a_replaced_socket_keeps_the_new_turn(orch, ws):
    second = FakeWebSocket()
    await orch.handle_connect(second)
    orch._engine.gate = asyncio.Event()
    await _send(orch, chunk(START), chunk(END))
    await _reach(orch, AvatarState.THINKING)
    await wait_until(lambda: len(orch._engine.utterances) == 1)

    await orch.handle_disconnect(ws)

    assert orch.state == AvatarState.THINKING
    assert orch._engine.closed == 0


# ── Tracking plane ────────────────────────────────────────────────────────

async def test_tracking_update_without_a_camera_reports_no_face(orch, ws):
    await wait_until(lambda: ws.of_type("tracking_update") != [])

    update = ws.of_type("tracking_update")[0]
    assert update["avatar_control"] == {
        "present": False,
        "head_rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
        "is_speaking": False,
    }
    assert isinstance(update["timestamp"], float)


async def test_tracking_update_carries_presence_and_rotation(orch, ws):
    orch._camera.connected = True
    orch._camera.frame = object()

    await wait_until(
        lambda: any(m["avatar_control"]["present"] for m in ws.of_type("tracking_update"))
    )

    control = ws.of_type("tracking_update")[-1]["avatar_control"]
    assert control["head_rotation"] == {"pitch": 0.1, "yaw": 0.2, "roll": 0.3}


async def test_tracking_keeps_running_during_a_turn_and_flags_speaking(orch, ws, config):
    config.PLAYBACK_END_TIMEOUT_SEC = 30
    await _send(orch, chunk(START), chunk(END))
    await _reach(orch, AvatarState.SPEAKING)

    await wait_until(
        lambda: any(m["avatar_control"]["is_speaking"] for m in ws.of_type("tracking_update"))
    )


async def test_head_holds_the_last_rotation_then_returns_to_neutral(orch, ws, config):
    config.NO_FACE_TIMEOUT_SEC = 0.3
    orch._camera.connected = True
    orch._camera.frame = object()
    await wait_until(
        lambda: any(m["avatar_control"]["present"] for m in ws.of_type("tracking_update"))
    )

    def last_control() -> dict:
        return ws.of_type("tracking_update")[-1]["avatar_control"]

    orch._camera.connected = False  # camera lost → no face
    await wait_until(lambda: not last_control()["present"])
    # Hold: the face is gone, but the last-known rotation is kept until the timeout.
    assert last_control()["head_rotation"] == {"pitch": 0.1, "yaw": 0.2, "roll": 0.3}

    await wait_until(
        lambda: last_control()["head_rotation"] == {"pitch": 0.0, "yaw": 0.0, "roll": 0.0}
    )
    assert not last_control()["present"]


async def test_face_without_a_pose_keeps_the_last_rotation(orch, ws, config):
    config.NO_FACE_TIMEOUT_SEC = 0.02
    orch._camera.connected = True
    orch._camera.frame = object()
    await wait_until(
        lambda: any(m["avatar_control"]["present"] for m in ws.of_type("tracking_update"))
    )

    orch._tracker.result = TrackingResult(present=True, head_rotation=None)  # solve rejected
    orch._camera.frame = object()
    await wait_until(lambda: orch._camera.frame is None)
    await asyncio.sleep(0.1)  # well past the no-face timeout
    sent = len(ws.of_type("tracking_update"))
    await wait_until(lambda: len(ws.of_type("tracking_update")) > sent)

    control = ws.of_type("tracking_update")[-1]["avatar_control"]
    assert control["present"]
    assert control["head_rotation"] == {"pitch": 0.1, "yaw": 0.2, "roll": 0.3}


# ── State rules ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("old", "new", "allowed"),
    [
        (AvatarState.IDLE, AvatarState.LISTENING, True),
        (AvatarState.LISTENING, AvatarState.THINKING, True),
        (AvatarState.THINKING, AvatarState.SPEAKING, True),
        (AvatarState.SPEAKING, AvatarState.IDLE, True),
        (AvatarState.THINKING, AvatarState.LISTENING, True),
        (AvatarState.SPEAKING, AvatarState.LISTENING, True),
        (AvatarState.IDLE, AvatarState.THINKING, False),
        (AvatarState.IDLE, AvatarState.SPEAKING, False),
        (AvatarState.LISTENING, AvatarState.SPEAKING, False),
        (AvatarState.SPEAKING, AvatarState.THINKING, False),
    ],
)
def test_transition_rules(old, new, allowed):
    assert can_transition(old, new) is allowed


async def test_illegal_transition_is_refused(orch, ws):
    await orch._transition(AvatarState.SPEAKING)

    assert orch.state == AvatarState.IDLE
    assert _states(ws) == []
