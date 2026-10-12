"""The spans the engine leaves for one turn: one per step, with what went through it."""
import asyncio

import pytest

from backend.config import AppConfig
from backend.core.contracts import KnowledgeError, LLMError, MemoryServiceError
from backend.core.conversation.engine import ConversationEngine
from backend.models import ChatTurn, MemoryProfile, Passage
from backend.utils.telemetry import tracer
from tests.unit.test_engine import (
    PROFILE, FakeKnowledge, FakeLLM, FakeMemory, FakeSTT, FakeTTS, _collect,
)

QUESTION = "Lãi suất tiết kiệm là bao nhiêu?"


def _by_name(spans) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for span in spans.get_finished_spans():
        grouped.setdefault(span.name, []).append(span)
    return grouped


async def _turn(engine: ConversationEngine, history=(), user_id=None):
    """Run one turn inside a `turn` span, as the orchestrator does."""
    with tracer.start_as_current_span("turn") as turn:
        await _collect(engine, history, user_id)
    return turn


def _engine(config: AppConfig, **parts) -> ConversationEngine:
    return ConversationEngine(
        config,
        stt=parts.get("stt", FakeSTT(QUESTION)),
        llm=parts.get("llm", FakeLLM(["Năm phần trăm. ", "Kỳ hạn sáu tháng."])),
        tts=parts.get("tts", FakeTTS()),
        knowledge=parts.get("knowledge"),
        memory=parts.get("memory"),
    )


async def test_a_turn_leaves_one_span_per_step_under_the_turn(config, spans):
    knowledge = FakeKnowledge([Passage("Kỳ hạn sáu tháng: 5%.", "lai-suat.pdf", 0.8)])

    turn = await _turn(_engine(config, knowledge=knowledge))

    steps = _by_name(spans)
    assert {name: len(found) for name, found in steps.items()} == {
        "stt": 1, "knowledge": 1, "llm": 1, "tts": 2, "turn": 1,
    }
    turn_id = turn.get_span_context().span_id
    for name in ("stt", "knowledge", "llm", "tts"):
        assert all(span.parent.span_id == turn_id for span in steps[name]), name


async def test_each_step_records_what_went_through_it(config, spans):
    knowledge = FakeKnowledge([Passage("Kỳ hạn sáu tháng: 5%.", "lai-suat.pdf", 0.8)])

    await _turn(_engine(config, knowledge=knowledge))

    steps = _by_name(spans)
    stt, lookup, llm = steps["stt"][0], steps["knowledge"][0], steps["llm"][0]
    assert stt.attributes["stt.text"] == QUESTION
    assert stt.attributes["stt.audio_ms"] == 10  # 160 samples at 16 kHz

    assert lookup.attributes["knowledge.query"] == QUESTION
    assert lookup.attributes["knowledge.outcome"] == "hit"
    (passage,) = lookup.events
    assert dict(passage.attributes) == {
        "index": 1, "source": "lai-suat.pdf", "score": 0.8, "text": "Kỳ hạn sáu tháng: 5%.",
    }

    assert "Kỳ hạn sáu tháng: 5%." in llm.attributes["llm.prompt"]
    assert llm.attributes["llm.prompt"].endswith(f"Câu hỏi: {QUESTION}")
    assert llm.attributes["llm.system_prompt"] == config.SYSTEM_PROMPT
    assert llm.attributes["llm.answer"] == "Năm phần trăm. Kỳ hạn sáu tháng."
    assert llm.attributes["llm.first_token_ms"] >= 0

    assert [span.attributes["tts.text"] for span in steps["tts"]] == [
        "Năm phần trăm.", "Kỳ hạn sáu tháng.",
    ]
    # FakeTTS: two chunks of 4 samples per sentence, at 48 kHz.
    assert all(span.attributes["tts.audio_ms"] == round(8 / 48000 * 1000) for span in steps["tts"])
    assert all(span.attributes["tts.first_chunk_ms"] >= 0 for span in steps["tts"])


async def test_the_spans_carry_the_history_the_query_searched_and_the_stt_confidence(config, spans):
    history = (ChatTurn("Phí chuyển khoản?", ("Miễn phí.",)), ChatTurn("Còn thẻ?", ("Có phí.",)))
    stt = FakeSTT(QUESTION)
    stt.confidence, stt.no_speech_prob = 0.83, 0.02
    knowledge = FakeKnowledge([Passage("Kỳ hạn sáu tháng: 5%.", "lai-suat.pdf", 0.8)])

    await _turn(_engine(config, stt=stt, knowledge=knowledge), history)

    steps = _by_name(spans)
    assert steps["stt"][0].attributes["stt.confidence"] == 0.83
    assert steps["stt"][0].attributes["stt.no_speech_prob"] == 0.02
    assert steps["knowledge"][0].attributes["knowledge.query"] == f"Còn thẻ?\n{QUESTION}"
    assert steps["llm"][0].attributes["llm.history_turns"] == 2
    assert steps["llm"][0].attributes["llm.profile"] == ""


async def test_an_stt_without_confidence_leaves_the_attributes_out(config, spans):
    await _turn(_engine(config))

    (stt,) = _by_name(spans)["stt"]
    assert "stt.confidence" not in stt.attributes
    assert "stt.no_speech_prob" not in stt.attributes
    assert _by_name(spans)["llm"][0].attributes["llm.history_turns"] == 0


async def test_no_knowledge_wired_leaves_no_knowledge_span(config, spans):
    await _turn(_engine(config))

    assert "knowledge" not in _by_name(spans)


@pytest.mark.parametrize(
    ("setup", "outcome"),
    [
        (lambda k: setattr(k, "error", KnowledgeError("store is gone")), "error"),
        (lambda k: setattr(k, "hang", True), "timeout"),
        (lambda k: setattr(k, "passages", []), "empty"),
    ],
)
async def test_the_knowledge_span_says_how_the_lookup_ended(config, spans, setup, outcome):
    config.KNOWLEDGE_TIMEOUT_SEC = 0.05
    knowledge = FakeKnowledge([Passage("x", "a.txt", 0.9)])
    setup(knowledge)

    await _turn(_engine(config, knowledge=knowledge))

    (lookup,) = _by_name(spans)["knowledge"]
    assert lookup.attributes["knowledge.outcome"] == outcome
    assert lookup.attributes["knowledge.passages"] == 0
    assert lookup.status.is_ok == (outcome == "empty")


async def test_an_llm_error_marks_its_span_and_the_fallback_text_is_noted(config, spans):
    llm = FakeLLM(["Một nửa câu"])
    llm.error = LLMError("provider down")

    turn = await _turn(_engine(config, llm=llm))

    (span,) = _by_name(spans)["llm"]
    assert not span.status.is_ok
    assert "provider down" in span.status.description
    assert span.attributes["llm.answer"] == "Một nửa câu"
    assert "llm_fallback_text_spoken" in [event.name for event in turn.events]


async def test_a_tts_error_marks_the_span_of_its_sentence(config, spans):
    tts = FakeTTS()
    tts.fail_on = {"Năm phần trăm."}

    await _turn(_engine(config, tts=tts))

    failed, spoken = _by_name(spans)["tts"]
    assert not failed.status.is_ok and failed.attributes["tts.audio_ms"] == 0
    assert spoken.status.is_ok


# ── Memory ────────────────────────────────────────────────────────────────

async def test_the_memory_span_carries_the_items_and_the_llm_span_the_profile(config, spans):
    turn = await _turn(_engine(config, memory=FakeMemory()), user_id="user-7")

    steps = _by_name(spans)
    (read,) = steps["memory"]
    assert read.parent.span_id == turn.get_span_context().span_id
    assert read.attributes["memory.items"] == 2
    assert read.attributes["memory.outcome"] == "hit"
    assert read.status.is_ok
    assert steps["llm"][0].attributes["llm.profile"] == PROFILE.text


def _memory_fails(memory: FakeMemory) -> None:
    memory.error = MemoryServiceError("store down")


def _memory_hangs(memory: FakeMemory) -> None:
    memory.hang = True


def _nothing_remembered(memory: FakeMemory) -> None:
    memory.result = MemoryProfile()


@pytest.mark.parametrize(
    ("setup", "outcome", "ok"),
    [(_nothing_remembered, "empty", True), (_memory_fails, "error", False),
     (_memory_hangs, "timeout", False)],
)
async def test_the_memory_span_says_how_the_read_ended(config, spans, setup, outcome, ok):
    config.MEMORY_TIMEOUT_SEC = 0.05
    memory = FakeMemory()
    setup(memory)

    # Bounded here too: without the engine's own timeout a hanging read never ends.
    await asyncio.wait_for(_turn(_engine(config, memory=memory), user_id="user-7"), timeout=2)

    (read,) = _by_name(spans)["memory"]
    assert read.attributes["memory.outcome"] == outcome
    assert read.attributes["memory.items"] == 0
    assert read.status.is_ok is ok
    assert _by_name(spans)["llm"][0].attributes["llm.profile"] == ""


async def test_a_guest_or_no_memory_wired_leaves_no_memory_span(config, spans):
    await _turn(_engine(config, memory=FakeMemory()))
    await _turn(_engine(config), user_id="user-7")

    assert "memory" not in _by_name(spans)
