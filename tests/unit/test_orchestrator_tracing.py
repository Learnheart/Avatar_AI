"""The trace of a turn as the orchestrator records it, and the perception measurements."""
import asyncio
import time
from typing import AsyncIterator

import pytest

from backend.config import AppConfig
from backend.core.orchestrator import Orchestrator
from backend.models import AvatarState, TrackingResult
from tests.unit.fakes import (
    END,
    START,
    FakeWebSocket,
    audio_message,
    build_orchestrator,
    chunk,
    wait_until,
)
from tests.unit.test_orchestrator import _LogCapture


@pytest.fixture
def config() -> AppConfig:
    return AppConfig(GROQ_API_KEY="test-key", TRACKING_FPS=200, PLAYBACK_END_TIMEOUT_SEC=0.05)


@pytest.fixture
async def orch(config) -> AsyncIterator[Orchestrator]:
    orchestrator = build_orchestrator(config)
    await orchestrator.start()
    yield orchestrator
    await orchestrator.stop()


@pytest.fixture
async def session_id(orch) -> str:
    return await orch.handle_connect(FakeWebSocket())


async def _say(orch: Orchestrator, *markers: int) -> None:
    for marker in markers:
        await orch.handle_client_message(audio_message(chunk(marker)))


async def _turn_done(orch: Orchestrator) -> None:
    await wait_until(lambda: orch._turn_task is not None and orch._turn_task.done())


def _named(spans, name: str) -> list:
    return [span for span in spans.get_finished_spans() if span.name == name]


async def _finished_turn(spans):
    await wait_until(lambda: _named(spans, "turn"))
    return _named(spans, "turn")[0]


async def test_a_turn_is_one_trace_starting_when_the_person_started_to_speak(
    orch, session_id, spans
):
    before = time.time_ns()
    await _say(orch, START, END)
    turn = await _finished_turn(spans)

    listen, playback = _named(spans, "listen")[0], _named(spans, "playback")[0]
    assert turn.parent is None
    assert before <= turn.start_time == listen.start_time <= listen.end_time
    assert {listen.context.trace_id, playback.context.trace_id} == {turn.context.trace_id}
    assert listen.parent.span_id == playback.parent.span_id == turn.context.span_id
    assert listen.attributes["utterance.ms"] > 0
    assert turn.attributes["session.id"] == session_id
    assert turn.attributes["turn.outcome"] == "completed"
    assert turn.attributes["turn.first_audio_ms"] >= 0
    assert turn.attributes["turn.audio_sec"] > 0
    assert playback.attributes["playback.ended_by"] == "timeout"  # no client to report it


async def test_the_turn_span_names_its_conversation_and_no_user_for_a_guest(
    orch, session_id, spans
):
    await _say(orch, START, END)
    turn = await _finished_turn(spans)

    assert turn.attributes["conversation.id"] == orch._conversation.id
    assert "user.id" not in turn.attributes


async def test_the_turn_span_names_the_user_of_the_conversation(orch, spans):
    await orch.handle_connect(FakeWebSocket(), user_id="user-7")

    await _say(orch, START, END)
    turn = await _finished_turn(spans)

    assert turn.attributes["user.id"] == "user-7"
    assert turn.attributes["conversation.id"] == orch._conversation.id


async def test_a_failed_turn_is_marked_failed(orch, session_id, spans):
    orch._engine.error = RuntimeError("boom")
    await _say(orch, START, END)

    turn = await _finished_turn(spans)

    assert turn.attributes["turn.outcome"] == "failed"
    assert not turn.status.is_ok


async def test_a_barge_in_ends_the_trace_of_the_cancelled_turn(orch, session_id, spans):
    orch._engine.gate = asyncio.Event()  # the turn hangs in `thinking`
    await _say(orch, START, END)
    await wait_until(lambda: orch.state == AvatarState.THINKING)

    await _say(orch, START)
    turn = await _finished_turn(spans)

    assert turn.attributes["turn.outcome"] == "cancelled"


async def test_speech_and_presence_are_logged_as_perception_events(orch, session_id):
    orch._camera.connected = True
    with _LogCapture() as logs:
        orch._camera.frame = object()
        await wait_until(lambda: orch._present is True)
        orch._tracker.result = TrackingResult(present=False)
        orch._camera.frame = object()
        await wait_until(lambda: orch._present is False)
        await _say(orch, START, END)
        await _turn_done(orch)

    events = [r.msg for r in logs.records if r.msg.get("msg") == "Perception event"]
    assert [e["event"] for e in events] == [
        "person_arrived", "person_left", "speech_start", "speech_end",
    ]
    assert events[2]["barge_in"] is False
    assert events[3]["utterance_ms"] > 0


async def test_the_perception_loops_are_measured(orch, session_id, metric_points):
    def count(name: str, **attributes) -> int:
        return sum(
            getattr(point, "count", None) or getattr(point, "value", 0)
            for point in metric_points(name)
            if all(point.attributes.get(k) == v for k, v in attributes.items())
        )

    vad_before = count("avatar.perception.duration", stage="vad")
    tracking_before = count("avatar.perception.duration", stage="tracking")
    frames_before = count("avatar.perception.frames", present=True)
    starts_before = count("avatar.perception.speech_events", event="speech_start")

    orch._camera.frame = object()
    await wait_until(lambda: orch._camera.frame is None)
    await _say(orch, START, END)
    await _turn_done(orch)
    await wait_until(lambda: count("avatar.perception.frames", present=True) > frames_before)

    assert count("avatar.perception.duration", stage="vad") == vad_before + 2
    assert count("avatar.perception.duration", stage="tracking") == tracking_before + 1
    assert count("avatar.perception.speech_events", event="speech_start") == starts_before + 1
