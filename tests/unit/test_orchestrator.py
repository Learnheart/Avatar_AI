import asyncio
import json
import logging
from typing import AsyncIterator

import pytest

from backend.config import AppConfig
from backend.core.contracts import STTError
from backend.core.conversation.history import chat_messages
from backend.models import (
    AudioEvent,
    AvatarState,
    ChatTurn,
    DoneEvent,
    TrackingResult,
    TranscriptEvent,
)
from backend.core.orchestrator import Orchestrator
from backend.core.orchestrator.state import can_transition
from backend.utils.logging import JsonFormatter
from tests.unit.fakes import (
    END,
    SILENCE,
    START,
    START_END,
    TTS_RATE,
    FakeClock,
    FakeMemory,
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
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def orch(config, clock) -> AsyncIterator[Orchestrator]:
    orchestrator = build_orchestrator(config, clock)
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
        {"type": "conversation", "conversation_id": orch._conversation.id},
    ]
    assert second.sent[0]["type"] == "connected"


async def test_connect_sends_the_current_state_right_after_connected(orch, ws):
    handshake = [m for m in ws.sent if m["type"] != "tracking_update"]

    assert [m["type"] for m in handshake] == ["connected", "state", "conversation"]
    assert handshake[1] == {"type": "state", "state": "idle"}
    assert handshake[2] == {"type": "conversation", "conversation_id": None}


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


# ── Conversation ──────────────────────────────────────────────────────────

RESET = json.dumps({"type": "conversation_reset"})
ONE_SECOND = b"\x01\x00" * TTS_RATE  # of TTS audio

FIRST_TURN = ChatTurn("xin chào", ("Xin chào.",), False, None)


def _conversation_ids(ws: FakeWebSocket) -> list:
    """The `conversation` messages after the handshake."""
    return [m["conversation_id"] for m in ws.of_type("conversation")][1:]


async def _complete_turn(orch: Orchestrator, ws: FakeWebSocket) -> None:
    """Say one utterance and let the avatar answer it to the end."""
    finals = sum(m["is_final"] for m in ws.of_type("speech_output"))
    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: sum(m["is_final"] for m in ws.of_type("speech_output")) > finals)
    await orch.handle_client_message(json.dumps({"type": "playback_ended"}))
    await _reach(orch, AvatarState.IDLE)
    await wait_until(lambda: orch._turn_task.done())


async def _thinking_after_the_transcript(orch: Orchestrator, ws: FakeWebSocket) -> None:
    """Start a turn that hangs in `thinking` once its transcript is known."""
    orch._engine.gate, orch._engine.gate_after = asyncio.Event(), TranscriptEvent
    heard = len(ws.of_type("transcript"))
    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: len(ws.of_type("transcript")) > heard)


async def _closed(orch: Orchestrator) -> None:
    await wait_until(lambda: orch._conversation is None)


def _three_sentences() -> list:
    return [
        TranscriptEvent("lãi suất?", 0.7),
        AudioEvent("Một.", ONE_SECOND, TTS_RATE),
        AudioEvent("Hai.", ONE_SECOND, TTS_RATE),
        AudioEvent("Ba.", ONE_SECOND, TTS_RATE),
        DoneEvent(),
    ]


# Boundary

async def test_the_first_utterance_opens_a_conversation_and_tells_the_client(orch, ws):
    assert orch._conversation is None

    await _complete_turn(orch, ws)

    conversation = orch._conversation
    assert (conversation.user_id, conversation.history) == (None, (FIRST_TURN,))
    assert _conversation_ids(ws) == [conversation.id]
    # The client knows the conversation before anything else of its first turn: right
    # after `listening`, which belongs to the utterance, and before `thinking`.
    after_handshake = ws.turn_messages()[2:]
    assert after_handshake[:3] == [
        {"type": "state", "state": "listening"},
        {"type": "conversation", "conversation_id": conversation.id},
        {"type": "state", "state": "thinking"},
    ]


async def test_a_second_turn_sees_the_first(orch, ws):
    await _complete_turn(orch, ws)
    await _complete_turn(orch, ws)

    assert orch._engine.histories == [(), (FIRST_TURN,)]
    assert _conversation_ids(ws) == [orch._conversation.id]  # opened once
    assert len(orch._conversation.history) == 2


async def test_the_history_is_capped_at_memory_history_max_turns(orch, ws, config):
    config.MEMORY_HISTORY_MAX_TURNS = 2

    for _ in range(4):
        await _complete_turn(orch, ws)

    assert [len(history) for history in orch._engine.histories] == [0, 1, 2, 2]


async def test_the_conversation_closes_after_the_absence_timeout(orch, ws, clock, config):
    orch._camera.connected = True  # and no frame: nobody is seen
    await _complete_turn(orch, ws)
    opened = orch._conversation.id

    clock.advance(config.CONVERSATION_ABSENCE_SEC - 1)
    await asyncio.sleep(0.05)
    assert orch._conversation is not None  # a short absence

    clock.advance(1)
    await _closed(orch)
    assert _conversation_ids(ws) == [opened, None]


async def test_absence_does_not_count_while_the_camera_is_disconnected(orch, ws, clock, config):
    await _complete_turn(orch, ws)  # FakeCamera: not connected

    clock.advance(config.CONVERSATION_ABSENCE_SEC + 1)
    await asyncio.sleep(0.05)

    assert orch._conversation is not None


async def test_a_person_in_front_of_the_camera_keeps_the_conversation(orch, ws, clock, config):
    orch._camera.connected = True
    orch._camera.frame = object()
    await wait_until(lambda: orch._present)
    await _complete_turn(orch, ws)

    clock.advance(config.CONVERSATION_ABSENCE_SEC + 1)
    await asyncio.sleep(0.05)

    assert orch._conversation is not None


async def test_the_conversation_closes_after_the_idle_timeout(orch, ws, clock, config):
    await _complete_turn(orch, ws)
    opened = orch._conversation.id

    clock.advance(config.CONVERSATION_IDLE_SEC - 1)
    await asyncio.sleep(0.05)
    assert orch._conversation is not None

    clock.advance(1)
    await _closed(orch)
    assert _conversation_ids(ws) == [opened, None]


async def test_idle_counts_from_the_end_of_the_last_recorded_turn(orch, ws, clock, config):
    await _complete_turn(orch, ws)  # opens the conversation
    clock.advance(config.CONVERSATION_IDLE_SEC - 1)
    await _complete_turn(orch, ws)

    # Longer than the idle timeout since the opening, shorter since the last turn.
    clock.advance(config.CONVERSATION_IDLE_SEC - 1)
    await asyncio.sleep(0.05)
    assert orch._conversation is not None

    clock.advance(1)
    await _closed(orch)


async def test_absence_counts_from_the_last_turn_of_a_person_the_camera_does_not_see(
    orch, ws, clock, config
):
    orch._camera.connected = True  # and nobody is ever seen
    await _complete_turn(orch, ws)
    clock.advance(config.CONVERSATION_ABSENCE_SEC - 1)
    await _complete_turn(orch, ws)  # the person, out of the camera's view, speaks again

    # Longer than the absence timeout since a face was missing, shorter since the turn.
    clock.advance(config.CONVERSATION_ABSENCE_SEC - 1)
    await asyncio.sleep(0.05)
    assert orch._conversation is not None

    clock.advance(1)
    await _closed(orch)


async def test_a_turn_going_idle_is_recorded_before_the_conversation_can_close(
    orch, clock, config
):
    """The state is `idle` a moment before the turn is recorded (its `state` message is
    still being sent). A close decided in that moment would drop the turn."""

    class HoldingWebSocket(FakeWebSocket):
        hold: asyncio.Event | None = None
        holding = False

        async def send_json(self, message: dict) -> None:
            if self.hold is not None and message["type"] == "tracking_update":
                self.holding = True
                await self.hold.wait()
            await super().send_json(message)

    ws = HoldingWebSocket()
    await orch.handle_connect(ws)
    orch._engine.events = [TranscriptEvent("xin chào"), DoneEvent()]  # nothing to speak
    await _thinking_after_the_transcript(orch, ws)
    conversation = orch._conversation
    clock.advance(config.CONVERSATION_IDLE_SEC + 1)  # a long turn

    ws.hold = asyncio.Event()  # a tracking message is on the wire ...
    await wait_until(lambda: ws.holding)
    orch._engine.gate.set()  # ... when the turn ends
    await _reach(orch, AvatarState.IDLE)
    assert not orch._turn_task.done()
    ws.hold.set()
    await wait_until(lambda: orch._turn_task.done())
    await asyncio.sleep(0.05)

    assert orch._conversation is conversation
    assert conversation.history == (ChatTurn("xin chào", (), False, None),)


async def test_the_conversation_closes_with_no_client_connected(orch, ws, clock, config):
    await _complete_turn(orch, ws)
    await orch.handle_disconnect(ws)

    clock.advance(config.CONVERSATION_IDLE_SEC)

    await _closed(orch)


async def test_a_person_leaving_while_no_client_is_connected_closes_the_conversation(
    orch, ws, clock, config
):
    orch._camera.connected = True
    orch._camera.frame = object()
    await wait_until(lambda: orch._present)
    await _complete_turn(orch, ws)
    await orch.handle_disconnect(ws)

    orch._tracker.result = TrackingResult(present=False)  # the person walks away
    orch._camera.frame = object()
    await wait_until(lambda: not orch._present)
    clock.advance(config.CONVERSATION_ABSENCE_SEC - 1)
    await asyncio.sleep(0.05)
    assert orch._conversation is not None

    clock.advance(1)
    await _closed(orch)


async def test_the_conversation_is_not_closed_while_a_turn_runs(orch, ws, clock, config):
    orch._camera.connected = True
    orch._engine.gate = asyncio.Event()  # the turn hangs in `thinking`
    await _send(orch, chunk(START), chunk(END))
    await _reach(orch, AvatarState.THINKING)

    clock.advance(config.CONVERSATION_IDLE_SEC + 1)
    await asyncio.sleep(0.05)

    assert orch._conversation is not None
    assert _conversation_ids(ws) == [orch._conversation.id]


async def test_the_conversation_is_not_closed_while_an_utterance_is_heard(orch, ws, clock, config):
    await _complete_turn(orch, ws)
    await _send(orch, chunk(START))
    await _reach(orch, AvatarState.LISTENING)

    clock.advance(config.CONVERSATION_IDLE_SEC + 1)
    await asyncio.sleep(0.05)

    assert orch._conversation is not None


async def test_the_next_utterance_after_a_close_opens_a_new_conversation(orch, ws, clock, config):
    await _complete_turn(orch, ws)
    first = orch._conversation.id
    clock.advance(config.CONVERSATION_IDLE_SEC)
    await _closed(orch)

    await _complete_turn(orch, ws)

    assert orch._engine.histories == [(), ()]
    assert _conversation_ids(ws) == [first, None, orch._conversation.id]
    assert orch._conversation.id != first


async def test_a_reconnect_of_the_same_user_keeps_the_conversation(orch, ws):
    await _complete_turn(orch, ws)
    opened = orch._conversation.id

    second = FakeWebSocket()
    await orch.handle_connect(second)
    await _complete_turn(orch, second)

    assert second.of_type("conversation") == [{"type": "conversation", "conversation_id": opened}]
    assert orch._engine.histories == [(), (FIRST_TURN,)]


async def test_a_change_of_user_closes_the_conversation(orch, ws):
    await _complete_turn(orch, ws)  # a guest
    guest_conversation = orch._conversation.id

    second = FakeWebSocket()
    await orch.handle_connect(second, user_id="user-7")

    assert orch._conversation is None
    # The new client is told that no conversation is open.
    assert second.of_type("conversation") == [{"type": "conversation", "conversation_id": None}]

    await _complete_turn(orch, second)
    # The turn of the new user sees none of the previous user's turns.
    assert orch._engine.histories == [(), ()]
    assert orch._conversation.user_id == "user-7"
    assert orch._conversation.id != guest_conversation


async def test_the_history_given_to_the_engine_is_the_one_of_the_turn_s_user(orch):
    first = FakeWebSocket()
    await orch.handle_connect(first, user_id="user-7")
    await _complete_turn(orch, first)

    again = FakeWebSocket()
    await orch.handle_connect(again, user_id="user-7")
    await _complete_turn(orch, again)
    other = FakeWebSocket()
    await orch.handle_connect(other, user_id="user-8")
    await _complete_turn(orch, other)

    assert orch._engine.histories == [(), (FIRST_TURN,), ()]


async def test_the_engine_is_told_who_the_turn_s_user_is(orch):
    guest = FakeWebSocket()
    await orch.handle_connect(guest)
    await _complete_turn(orch, guest)
    known = FakeWebSocket()
    await orch.handle_connect(known, user_id="user-7")
    await _complete_turn(orch, known)

    assert orch._engine.user_ids == [None, "user-7"]


async def test_an_empty_transcript_leaves_the_conversation_open_and_unrecorded(orch, ws):
    orch._engine.events = [TranscriptEvent(""), DoneEvent()]

    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: _states(ws) == ["listening", "thinking", "idle"])
    await wait_until(lambda: orch._turn_task.done())

    assert orch._conversation is not None
    assert orch._conversation.history == ()
    assert ws.of_type("transcript") == []


async def _noise(orch: Orchestrator) -> None:
    """An utterance in which nothing is understood, run to its end."""
    orch._engine.events = [TranscriptEvent(""), DoneEvent()]
    turns = len(orch._engine.utterances)
    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: len(orch._engine.utterances) > turns and orch._turn_task.done())
    await _reach(orch, AvatarState.IDLE)


async def test_noise_does_not_postpone_the_idle_close(orch, ws, clock, config):
    await _complete_turn(orch, ws)

    clock.advance(config.CONVERSATION_IDLE_SEC - 1)
    await _noise(orch)
    assert orch._conversation is not None
    clock.advance(1)

    await _closed(orch)


async def test_noise_does_not_postpone_the_absence_close(orch, ws, clock, config):
    orch._camera.connected = True  # and nobody is seen
    await _complete_turn(orch, ws)

    clock.advance(config.CONVERSATION_ABSENCE_SEC - 1)
    await _noise(orch)
    assert orch._conversation is not None
    clock.advance(1)

    await _closed(orch)


async def test_opening_and_closing_are_logged_with_the_id_and_the_reason(orch, ws, clock, config):
    with _LogCapture() as log:
        await _complete_turn(orch, ws)
        opened = orch._conversation.id
        clock.advance(config.CONVERSATION_IDLE_SEC)
        await _closed(orch)

    lines = {r.msg["msg"]: r.msg for r in log.records if isinstance(r.msg, dict)}
    assert lines["Conversation opened"]["conversation_id"] == opened
    assert lines["Conversation closed"]["conversation_id"] == opened
    assert lines["Conversation closed"]["reason"] == "idle"


# Recording a turn

async def test_the_transcript_is_sent_once_per_turn_with_the_text(orch, ws):
    await _complete_turn(orch, ws)

    assert ws.of_type("transcript") == [{"type": "transcript", "text": "xin chào"}]
    turn = ws.turn_messages()
    assert turn.index(ws.of_type("transcript")[0]) < turn.index(ws.of_type("speech_output")[0])


async def test_a_completed_turn_records_every_sentence_and_the_confidence(orch, ws):
    orch._engine.events = _three_sentences()

    await _complete_turn(orch, ws)

    assert orch._conversation.history == (
        ChatTurn("lãi suất?", ("Một.", "Hai.", "Ba."), False, 0.7),
    )


async def test_a_barge_in_while_speaking_records_what_had_started_to_play(orch, ws, clock, config):
    config.PLAYBACK_END_TIMEOUT_SEC = 30
    orch._engine.events = _three_sentences()  # one second each, all sent at once
    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: any(m["is_final"] for m in ws.of_type("speech_output")))

    clock.advance(0.5)  # the first sentence is playing
    await _send(orch, chunk(START))
    await _reach(orch, AvatarState.LISTENING)

    assert orch._conversation.history == (ChatTurn("lãi suất?", ("Một.",), True, 0.7),)


async def test_a_barge_in_during_the_second_sentence_records_two(orch, ws, clock, config):
    config.PLAYBACK_END_TIMEOUT_SEC = 30
    orch._engine.events = _three_sentences()
    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: any(m["is_final"] for m in ws.of_type("speech_output")))

    clock.advance(1.5)
    await _send(orch, chunk(START))
    await _reach(orch, AvatarState.LISTENING)

    assert orch._conversation.history[0].sentences == ("Một.", "Hai.")


async def test_a_barge_in_while_thinking_records_the_utterance_with_an_empty_answer(orch, ws):
    await _thinking_after_the_transcript(orch, ws)

    await _send(orch, chunk(START))
    await _reach(orch, AvatarState.LISTENING)

    assert orch._conversation.history == (ChatTurn("xin chào", (), True, None),)


async def test_a_barge_in_before_the_transcript_records_nothing(orch, ws):
    orch._engine.gate = asyncio.Event()  # the turn hangs before its transcript
    await _send(orch, chunk(START), chunk(END))
    await _reach(orch, AvatarState.THINKING)
    await wait_until(lambda: len(orch._engine.utterances) == 1)

    await _send(orch, chunk(START))
    await _reach(orch, AvatarState.LISTENING)

    assert orch._conversation.history == ()


async def test_two_utterances_cut_by_a_barge_in_reach_the_llm_as_one_user_message(orch, ws, config):
    await _thinking_after_the_transcript(orch, ws)
    await _send(orch, chunk(START))  # the person goes on after a pause
    await _reach(orch, AvatarState.LISTENING)

    orch._engine.gate = None
    await _send(orch, chunk(END))
    await wait_until(lambda: len(orch._engine.histories) == 2)

    history = orch._engine.histories[1]
    assert history == (ChatTurn("xin chào", (), True, None),)
    messages = chat_messages(history, "về lãi suất", system_prompt=config.SYSTEM_PROMPT)
    assert messages[1:] == [("user", "xin chào\nvề lãi suất")]


async def test_a_timeout_after_the_transcript_records_the_utterance_alone(orch, ws, config):
    config.TURN_TIMEOUT_SEC = 0.05
    await _thinking_after_the_transcript(orch, ws)

    await wait_until(lambda: ws.of_type("error") != [])
    await _reach(orch, AvatarState.IDLE)
    await wait_until(lambda: orch._turn_task.done())

    assert ws.of_type("error")[0]["code"] == "turn_timeout"
    assert orch._conversation.history == (ChatTurn("xin chào", (), False, None),)


async def test_a_timeout_before_the_transcript_records_nothing(orch, ws, config):
    config.TURN_TIMEOUT_SEC = 0.05
    orch._engine.gate = asyncio.Event()

    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: ws.of_type("error") != [])
    await wait_until(lambda: orch._turn_task.done())

    assert orch._conversation.history == ()


async def test_a_failure_after_the_transcript_records_the_utterance_alone(orch, ws):
    orch._engine.error = RuntimeError("llm exploded")  # raised right after the transcript

    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: ws.of_type("error") != [])
    await wait_until(lambda: orch._turn_task.done())

    assert orch._conversation.history == (ChatTurn("xin chào", (), False, None),)


def _speak_three_sentences_then(orch: Orchestrator, ending) -> None:
    """The engine sends three sentences of one second at once, then awaits `ending()`."""
    async def respond(pcm16, sample_rate, history=(), user_id=None):
        for event in _three_sentences()[:-1]:
            yield event
        await ending()

    orch._engine.respond = respond


async def _three_sentences_sent(ws: FakeWebSocket) -> None:
    await wait_until(lambda: len(ws.of_type("speech_output")) == 3)


async def test_a_timeout_while_speaking_records_what_had_started_to_play(orch, ws, clock, config):
    config.TURN_TIMEOUT_SEC = 0.3
    _speak_three_sentences_then(orch, asyncio.Event().wait)  # the engine hangs

    await _send(orch, chunk(START), chunk(END))
    await _three_sentences_sent(ws)
    clock.advance(0.5)  # the first sentence is playing when the turn times out
    await wait_until(lambda: ws.of_type("error") != [])
    await wait_until(lambda: orch._turn_task.done())

    assert ws.of_type("error")[0]["code"] == "turn_timeout"
    assert orch._conversation.history == (ChatTurn("lãi suất?", ("Một.",), False, 0.7),)


async def test_a_failure_while_speaking_records_what_had_started_to_play(orch, ws, clock):
    fail = asyncio.Event()

    async def ending() -> None:
        await fail.wait()
        raise RuntimeError("tts exploded")

    _speak_three_sentences_then(orch, ending)

    await _send(orch, chunk(START), chunk(END))
    await _three_sentences_sent(ws)
    clock.advance(1.5)  # the second sentence is playing when the turn fails
    fail.set()
    await wait_until(lambda: ws.of_type("error") != [])
    await wait_until(lambda: orch._turn_task.done())

    assert ws.of_type("error")[0]["code"] == "turn_failed"
    assert orch._conversation.history == (ChatTurn("lãi suất?", ("Một.", "Hai."), False, 0.7),)


async def test_a_failure_before_the_transcript_records_nothing(orch, ws):
    async def broken(pcm16, sample_rate, history=()):
        raise STTError("stt down")
        yield  # an async generator, like the engine's

    orch._engine.respond = broken

    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: ws.of_type("error") != [])
    await wait_until(lambda: orch._turn_task.done())

    assert orch._conversation.history == ()


async def test_an_llm_failure_records_the_utterance_and_not_the_fallback_text(orch, ws, config):
    orch._engine.events = [
        TranscriptEvent("xin chào"),
        AudioEvent(config.LLM_FALLBACK_TEXT, b"\x01\x00" * 480, TTS_RATE, fallback=True),
        DoneEvent(),
    ]

    await _complete_turn(orch, ws)

    assert orch._conversation.history == (ChatTurn("xin chào", (), False, None),)
    # The apology is still spoken and shown.
    assert ws.of_type("speech_output")[0]["text_content"] == config.LLM_FALLBACK_TEXT


async def test_a_sentence_whose_tts_failed_is_not_in_the_recorded_answer(orch, ws):
    orch._engine.events = [
        TranscriptEvent("xin chào"),
        AudioEvent("Câu không có tiếng.", b"", TTS_RATE),
        AudioEvent("Câu hai.", b"\x01\x00" * 480, TTS_RATE),
        DoneEvent(),
    ]

    await _complete_turn(orch, ws)

    assert orch._conversation.history[0].sentences == ("Câu hai.",)


async def test_a_disconnect_records_the_running_turn_as_interrupted(orch, ws):
    await _thinking_after_the_transcript(orch, ws)
    conversation = orch._conversation

    await orch.handle_disconnect(ws)

    assert orch._conversation is conversation  # a lost socket does not end the session
    assert conversation.history == (ChatTurn("xin chào", (), True, None),)


# Reset

async def test_reset_while_idle_closes_the_conversation_and_the_next_turn_starts_anew(orch, ws):
    await _complete_turn(orch, ws)
    first = orch._conversation.id

    await orch.handle_client_message(RESET)

    assert orch._conversation is None
    assert ws.of_type("interrupt", "error") == []

    await _complete_turn(orch, ws)
    assert orch._engine.histories == [(), ()]
    assert _conversation_ids(ws) == [first, None, orch._conversation.id]
    assert orch._conversation.id != first


async def test_reset_during_a_turn_interrupts_it_records_it_then_closes(orch, ws, clock, config):
    config.PLAYBACK_END_TIMEOUT_SEC = 30
    orch._engine.events = _three_sentences()
    await _send(orch, chunk(START), chunk(END))
    await wait_until(lambda: any(m["is_final"] for m in ws.of_type("speech_output")))
    conversation = orch._conversation
    clock.advance(0.5)

    await orch.handle_client_message(RESET)

    assert orch.state == AvatarState.IDLE
    assert orch._conversation is None
    # Recorded as interrupted, with what was spoken, in the conversation being closed.
    assert conversation.history == (ChatTurn("lãi suất?", ("Một.",), True, 0.7),)
    assert ws.turn_messages()[-3:] == [
        {"type": "interrupt"},
        {"type": "state", "state": "idle"},
        {"type": "conversation", "conversation_id": None},
    ]
    assert ws.of_type("error") == []


async def test_reset_while_thinking_interrupts_and_closes(orch, ws):
    await _thinking_after_the_transcript(orch, ws)
    conversation = orch._conversation

    await orch.handle_client_message(RESET)

    assert orch.state == AvatarState.IDLE
    assert orch._engine.closed == 1
    assert conversation.history == (ChatTurn("xin chào", (), True, None),)
    assert [m["type"] for m in ws.turn_messages()[-3:]] == ["interrupt", "state", "conversation"]


async def test_reset_while_listening_drops_the_utterance_and_closes(orch, ws):
    await _complete_turn(orch, ws)
    await _send(orch, chunk(START, 7), chunk(SILENCE, 7))
    await _reach(orch, AvatarState.LISTENING)

    await orch.handle_client_message(RESET)

    assert orch.state == AvatarState.IDLE
    assert orch._conversation is None
    assert ws.of_type("interrupt") == []  # nothing was playing

    await _send(orch, chunk(START_END, 5))
    await _reach(orch, AvatarState.SPEAKING)
    assert orch._engine.utterances[-1] == chunk(START_END, 5)
    assert orch._engine.histories[-1] == ()


async def test_reset_with_no_conversation_open_changes_nothing_and_sends_no_error(orch, ws):
    before = ws.turn_messages()

    await orch.handle_client_message(RESET)

    assert orch.state == AvatarState.IDLE
    assert ws.turn_messages() == before
    assert orch._vad.resets == 1  # the one of the connect


# ── The memory is told, in the background, for a logged-in user only ──────

@pytest.fixture
def memory() -> FakeMemory:
    return FakeMemory()


@pytest.fixture
async def remembering(config, clock, memory) -> AsyncIterator[Orchestrator]:
    """An orchestrator wired to a memory, with a logged-in user connected."""
    orchestrator = build_orchestrator(config, clock, memory)
    await orchestrator.start()
    yield orchestrator
    await orchestrator.stop()


async def _connect(orch: Orchestrator, user_id=None) -> FakeWebSocket:
    websocket = FakeWebSocket()
    await orch.handle_connect(websocket, user_id=user_id)
    return websocket


async def _told(orch: Orchestrator) -> None:
    """Everything the memory was told has run."""
    await wait_until(lambda: not orch._memory_jobs)


async def test_a_logged_in_user_s_turn_is_handed_to_the_memory(remembering, memory):
    ws = await _connect(remembering, "user-7")

    await _complete_turn(remembering, ws)
    await _told(remembering)

    conversation = remembering._conversation
    assert memory.calls == [
        ("record_turn", "user-7", conversation.id, ChatTurn("xin chào", ("Xin chào.",)))
    ]


async def test_the_turn_does_not_wait_for_the_memory(remembering, memory):
    memory.gate = asyncio.Event()  # the memory hangs
    ws = await _connect(remembering, "user-7")

    await _complete_turn(remembering, ws)
    await _complete_turn(remembering, ws)

    assert remembering.state == AvatarState.IDLE
    assert memory.calls == []
    memory.gate.set()
    await _told(remembering)
    assert [call[0] for call in memory.calls] == ["record_turn", "record_turn"]


async def test_a_failing_memory_is_logged_and_costs_no_turn(remembering, memory, caplog):
    memory.error = RuntimeError("database is gone")
    ws = await _connect(remembering, "user-7")

    with caplog.at_level(logging.ERROR, logger="orchestrator"):
        await _complete_turn(remembering, ws)
        await _told(remembering)
        await _complete_turn(remembering, ws)
        await _told(remembering)

    assert ws.of_type("error") == []
    assert len(remembering._conversation.history) == 2
    failures = [r.msg for r in caplog.records if r.msg.get("msg") == "Memory call failed"]
    assert [failure["call"] for failure in failures] == ["memory-record_turn"] * 2


@pytest.mark.parametrize("reason", ["idle", "absence", "reset", "user_changed"])
async def test_a_logged_in_user_s_closed_conversation_is_handed_to_the_memory(
    remembering, memory, clock, config, reason
):
    ws = await _connect(remembering, "user-7")
    if reason == "absence":
        remembering._camera.connected = True  # and no frame: nobody is seen
    await _complete_turn(remembering, ws)
    conversation = remembering._conversation

    if reason == "idle":
        clock.advance(config.CONVERSATION_IDLE_SEC + 1)
        await _closed(remembering)
    elif reason == "absence":
        assert config.CONVERSATION_ABSENCE_SEC < config.CONVERSATION_IDLE_SEC
        clock.advance(config.CONVERSATION_ABSENCE_SEC + 1)
        await _closed(remembering)
    elif reason == "reset":
        await remembering.handle_client_message(RESET)
    else:
        await _connect(remembering, "user-8")
    await _told(remembering)

    assert [call[:3] for call in memory.calls] == [
        ("record_turn", "user-7", conversation.id),
        ("close_conversation", "user-7", conversation.id),
    ]


async def test_the_close_is_read_after_the_interrupted_turn_was_written(
    remembering, memory, config
):
    """A reset during a turn: the turn is recorded as interrupted and the conversation
    closed in one go, and the memory hears of them in that order, however slow it is."""
    memory.gate = asyncio.Event()
    ws = await _connect(remembering, "user-7")
    await _thinking_after_the_transcript(remembering, ws)
    conversation = remembering._conversation

    await remembering.handle_client_message(RESET)
    assert memory.calls == []  # nothing was awaited
    memory.gate.set()
    await _told(remembering)

    assert memory.calls == [
        ("record_turn", "user-7", conversation.id, ChatTurn("xin chào", (), True, None)),
        ("close_conversation", "user-7", conversation.id),
    ]


async def test_a_failed_record_does_not_stop_the_close(remembering, memory):
    memory.error = RuntimeError("database is gone")
    ws = await _connect(remembering, "user-7")
    await _complete_turn(remembering, ws)

    await remembering.handle_client_message(RESET)
    await _told(remembering)

    assert [call[0] for call in memory.calls] == ["record_turn", "close_conversation"]
    assert remembering._memory_last == {}


async def test_closing_a_guest_s_conversation_tells_the_memory_nothing(
    remembering, memory, clock, config
):
    ws = await _connect(remembering)  # a guest

    await _complete_turn(remembering, ws)
    clock.advance(config.CONVERSATION_IDLE_SEC + 1)
    await _closed(remembering)
    await _complete_turn(remembering, ws)
    await remembering.handle_client_message(RESET)

    assert remembering._memory_jobs == set()
    assert memory.calls == []


async def test_a_turn_in_which_nothing_was_understood_is_not_handed_to_the_memory(
    remembering, memory
):
    await _connect(remembering, "user-7")
    remembering._engine.events = [TranscriptEvent(""), DoneEvent()]

    await _send(remembering, chunk(START), chunk(END))
    await _reach(remembering, AvatarState.IDLE)
    await wait_until(lambda: not remembering._turn_running())

    assert remembering._memory_jobs == set()
    assert memory.calls == []


async def test_stopping_waits_for_the_turns_and_drops_a_conversation_being_read(config, clock):
    memory = FakeMemory()
    orch = build_orchestrator(config, clock, memory)
    await orch.start()
    ws = await _connect(orch, "user-7")
    await _complete_turn(orch, ws)
    await _told(orch)
    memory.gate = asyncio.Event()  # from now on the memory hangs
    await orch.handle_client_message(RESET)  # the close is being read
    await _complete_turn(orch, ws)  # a turn of the next conversation is being written
    asyncio.get_running_loop().call_later(0.05, memory.gate.set)

    await orch.stop()

    assert orch._memory_jobs == set()
    assert memory.cancelled == ["close_conversation"]
    assert [call[0] for call in memory.calls] == ["record_turn", "record_turn"]


async def test_stopping_does_not_wait_longer_than_the_memory_timeout(config, clock, caplog):
    """A memory call that hangs (a database that answers no more) does not hang the
    shutdown: past MEMORY_TIMEOUT_SEC it is abandoned, and the log says which."""
    config.MEMORY_TIMEOUT_SEC = 0.05
    memory = FakeMemory()
    memory.gate = asyncio.Event()  # never set: every call hangs
    orch = build_orchestrator(config, clock, memory)
    await orch.start()
    ws = await _connect(orch, "user-7")
    await _complete_turn(orch, ws)

    with caplog.at_level(logging.WARNING, logger="orchestrator"):
        await asyncio.wait_for(orch.stop(), timeout=2.0)

    (abandoned,) = [r.msg for r in caplog.records if r.msg.get("msg") == "Memory calls abandoned"]
    assert abandoned["calls"] == ["memory-record_turn"]
    assert memory.calls == []
