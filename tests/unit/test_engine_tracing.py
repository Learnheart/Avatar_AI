"""The spans the engine leaves for one turn: one per step, with what went through it."""
import pytest

from backend.config import AppConfig
from backend.core.contracts import KnowledgeError, LLMError
from backend.core.conversation.engine import ConversationEngine
from backend.models import Passage
from backend.utils.telemetry import tracer
from tests.unit.test_engine import FakeKnowledge, FakeLLM, FakeSTT, FakeTTS, _collect

QUESTION = "Lãi suất tiết kiệm là bao nhiêu?"


def _by_name(spans) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for span in spans.get_finished_spans():
        grouped.setdefault(span.name, []).append(span)
    return grouped


async def _turn(engine: ConversationEngine):
    """Run one turn inside a `turn` span, as the orchestrator does."""
    with tracer.start_as_current_span("turn") as turn:
        await _collect(engine)
    return turn


def _engine(config: AppConfig, **parts) -> ConversationEngine:
    return ConversationEngine(
        config,
        stt=parts.get("stt", FakeSTT(QUESTION)),
        llm=parts.get("llm", FakeLLM(["Năm phần trăm. ", "Kỳ hạn sáu tháng."])),
        tts=parts.get("tts", FakeTTS()),
        knowledge=parts.get("knowledge"),
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
