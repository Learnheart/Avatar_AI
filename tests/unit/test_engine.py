import asyncio
from typing import AsyncIterator

import pytest

from backend.config import AppConfig
from backend.core.conversation.engine import ConversationEngine, split_sentences
from backend.core.contracts import (
    LLM, STT, TTS, Knowledge, KnowledgeError, LLMError, Memory, MemoryServiceError, Persona,
    PersonaError, STTError, TTSError,
)
from backend.models import (
    AudioEvent, ChatTurn, DoneEvent, MemoryProfile, Passage, Transcript, TranscriptEvent,
)

RATE = 48000


class FakeSTT(STT):
    def __init__(self, text: str = "Lãi suất tiết kiệm là bao nhiêu?") -> None:
        self.text = text
        self.delay = 0.0
        self.confidence: float | None = None
        self.no_speech_prob: float | None = None
        self.calls: list[tuple[bytes, int]] = []

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> Transcript:
        self.calls.append((pcm16, sample_rate))
        await asyncio.sleep(self.delay)
        return Transcript(
            text=self.text,
            language="vi",
            confidence=self.confidence,
            no_speech_prob=self.no_speech_prob,
        )


class FakeLLM(LLM):
    """Yields scripted deltas; `hold_after` pauses the stream until `release` is set."""

    def __init__(self, deltas: list[str]) -> None:
        self.deltas = deltas
        self.error: Exception | None = None
        self.hold_after: int | None = None
        self.release = asyncio.Event()
        self.calls: list[str] = []
        self.histories: list[tuple] = []
        self.profiles: list[str] = []
        self.personas: list[str] = []
        self.closed = False

    async def complete(self, system, user) -> str:
        return "".join(self.deltas)

    async def stream_answer(
        self, user_text, history=(), profile="", persona=""
    ) -> AsyncIterator[str]:
        self.calls.append(user_text)
        self.histories.append(tuple(history))
        self.profiles.append(profile)
        self.personas.append(persona)
        try:
            for index, delta in enumerate(self.deltas):
                if index == self.hold_after:
                    await self.release.wait()
                yield delta
            if self.error:
                raise self.error
        finally:
            self.closed = True


class FakeTTS(TTS):
    """Two chunks per sentence; sentences listed in `fail_on` raise TTSError.
    `hang` blocks before the first chunk, `stall` between the two chunks."""

    def __init__(self) -> None:
        self.loaded = False
        self.fail_on: set[str] = set()
        self.hang = False
        self.stall = False
        self.spoken: list[str] = []
        self.closed = 0

    def load(self) -> None:
        self.loaded = True

    def close(self) -> None:
        self.loaded = False

    @property
    def sample_rate(self) -> int:
        return RATE

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        self.spoken.append(text)
        try:
            if text in self.fail_on:
                raise TTSError("synthesis failed")
            if self.hang:
                await asyncio.Event().wait()
            yield b"\x01\x00" * 4
            if self.stall:
                await asyncio.Event().wait()
            yield b"\x02\x00" * 4
        finally:
            self.closed += 1


def _engine(config: AppConfig, stt: FakeSTT, llm: FakeLLM, tts: FakeTTS) -> ConversationEngine:
    return ConversationEngine(config, stt=stt, llm=llm, tts=tts)


async def _collect(engine: ConversationEngine, history=(), user_id=None) -> list:
    return [
        event async for event in engine.respond(b"\x01\x00" * 160, 16000, history, user_id)
    ]


def _texts(events: list) -> list[str]:
    return [e.text for e in events if isinstance(e, AudioEvent) and e.text]


# ── split_sentences ───────────────────────────────────────────────────────

def test_split_sentences_on_vietnamese_text():
    sentences, tail = split_sentences(
        "Xin chào quý khách! Lãi suất hiện tại là 5,5% một năm. Quý khách cần gì thêm"
    )

    assert sentences == ["Xin chào quý khách!", "Lãi suất hiện tại là 5,5% một năm."]
    assert tail == "Quý khách cần gì thêm"


def test_split_sentences_handles_every_terminator_and_newline():
    sentences, tail = split_sentences("Một. Hai? Ba! Bốn… Năm\nSáu")

    assert sentences == ["Một.", "Hai?", "Ba!", "Bốn…", "Năm"]
    assert tail == "Sáu"


def test_split_sentences_keeps_numbers_in_one_piece():
    sentences, tail = split_sentences("Số dư là 1.000.000 đồng, lãi 3.5%. ")

    assert sentences == ["Số dư là 1.000.000 đồng, lãi 3.5%."]
    assert tail == ""


def test_split_sentences_waits_for_the_character_after_a_terminator():
    # "1." may still become "1.000": no cut until whitespace follows.
    assert split_sentences("Số dư là 1.") == ([], "Số dư là 1.")
    assert split_sentences("") == ([], "")


# ── respond ───────────────────────────────────────────────────────────────

async def test_full_turn_emits_transcript_audio_per_sentence_then_done(config):
    stt, tts = FakeSTT(), FakeTTS()
    llm = FakeLLM(["Xin ", "chào. ", "Lãi suất là ", "5%."])
    engine = _engine(config, stt, llm, tts)

    events = await _collect(engine)

    assert events[0] == TranscriptEvent("Lãi suất tiết kiệm là bao nhiêu?")
    assert isinstance(events[-1], DoneEvent)
    audio = events[1:-1]
    assert [(e.text, e.sample_rate) for e in audio] == [
        ("Xin chào.", RATE), ("", RATE), ("Lãi suất là 5%.", RATE), ("", RATE),
    ]
    assert all(e.pcm for e in audio)
    assert stt.calls == [(b"\x01\x00" * 160, 16000)]
    assert llm.calls == ["Lãi suất tiết kiệm là bao nhiêu?"]


async def test_tts_starts_on_sentence_one_before_the_llm_finishes(config):
    llm = FakeLLM(["Xin chào. ", "Câu thứ hai."])
    llm.hold_after = 1  # the LLM stalls after the first sentence
    engine = _engine(config, FakeSTT(), llm, FakeTTS())

    stream = engine.respond(b"\x01\x00" * 160, 16000)
    assert isinstance(await anext(stream), TranscriptEvent)
    first_audio = await asyncio.wait_for(anext(stream), timeout=1)

    assert first_audio.text == "Xin chào."
    assert not llm.release.is_set()

    llm.release.set()
    rest = [event async for event in stream]
    assert _texts(rest) == ["Câu thứ hai."]
    assert isinstance(rest[-1], DoneEvent)


async def test_empty_transcript_makes_no_llm_call(config):
    llm, tts = FakeLLM(["Không được gọi."]), FakeTTS()
    engine = _engine(config, FakeSTT(text=""), llm, tts)

    events = await _collect(engine)

    assert events == [TranscriptEvent(""), DoneEvent()]
    assert llm.calls == []
    assert tts.spoken == []


async def test_llm_error_speaks_the_fallback_text(config):
    llm, tts = FakeLLM([]), FakeTTS()
    llm.error = LLMError("provider down")
    engine = _engine(config, FakeSTT(), llm, tts)

    events = await _collect(engine)

    assert _texts(events) == [config.LLM_FALLBACK_TEXT]
    assert isinstance(events[-1], DoneEvent)


async def test_empty_llm_answer_speaks_the_fallback_text(config):
    tts = FakeTTS()
    engine = _engine(config, FakeSTT(), FakeLLM(["", "  \n"]), tts)

    await _collect(engine)

    assert tts.spoken == [config.LLM_FALLBACK_TEXT]


async def test_llm_timeout_speaks_the_fallback_text(config):
    config.LLM_TIMEOUT_SEC = 0.05
    llm, tts = FakeLLM(["Không bao giờ tới."]), FakeTTS()
    llm.hold_after = 0
    engine = _engine(config, FakeSTT(), llm, tts)

    await _collect(engine)

    assert tts.spoken == [config.LLM_FALLBACK_TEXT]
    assert llm.closed


async def test_tts_error_gives_a_text_only_event_and_the_turn_continues(config):
    tts = FakeTTS()
    tts.fail_on = {"Câu một."}
    engine = _engine(config, FakeSTT(), FakeLLM(["Câu một. ", "Câu hai."]), tts)

    events = await _collect(engine)

    audio = [e for e in events if isinstance(e, AudioEvent)]
    assert audio[0] == AudioEvent("Câu một.", b"", RATE)
    assert [e.text for e in audio[1:]] == ["Câu hai.", ""]
    assert all(e.pcm for e in audio[1:])
    assert isinstance(events[-1], DoneEvent)


async def test_tts_timeout_gives_a_text_only_event(config):
    config.TTS_TIMEOUT_SEC = 0.05
    tts = FakeTTS()
    tts.hang = True
    engine = _engine(config, FakeSTT(), FakeLLM(["Câu một."]), tts)

    events = await _collect(engine)

    assert events[1:] == [AudioEvent("Câu một.", b"", RATE), DoneEvent()]
    assert tts.closed == 1


async def test_stt_timeout_raises_stt_error(config):
    config.STT_TIMEOUT_SEC = 0.05
    stt = FakeSTT()
    stt.delay = 5
    engine = _engine(config, stt, FakeLLM([]), FakeTTS())

    with pytest.raises(STTError, match="timed out"):
        await _collect(engine)


async def test_cancellation_stops_the_llm_stream_and_the_tts_worker(config):
    llm, tts = FakeLLM(["Xin chào. ", "Câu thứ hai."]), FakeTTS()
    llm.hold_after = 1  # the LLM is still streaming
    tts.stall = True    # and sentence one is still being synthesized
    engine = _engine(config, FakeSTT(), llm, tts)
    received: list = []

    async def consume() -> None:
        async for event in engine.respond(b"\x01\x00" * 160, 16000):
            received.append(event)

    task = asyncio.create_task(consume())
    while len(received) < 2:  # transcript + first chunk of sentence one
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(5):  # let the abandoned generator finish closing
        await asyncio.sleep(0)

    assert llm.closed
    assert tts.closed == 1
    assert tts.spoken == ["Xin chào."]


# ── History, confidence, fallback mark ────────────────────────────────────

HISTORY = (
    ChatTurn("Lãi suất kỳ hạn sáu tháng?", ("Năm phần trăm.",)),
    ChatTurn("Còn phí chuyển khoản?", ("Miễn phí.",)),
)


async def test_the_llm_receives_the_history_and_an_empty_profile(config):
    llm = FakeLLM(["Sáu phần trăm."])
    engine = _engine(config, FakeSTT("còn mười hai tháng?"), llm, FakeTTS())

    await _collect(engine, HISTORY)

    assert llm.calls == ["còn mười hai tháng?"]
    assert llm.histories == [HISTORY]
    assert llm.profiles == [""]
    assert llm.personas == [""]


async def test_without_history_the_llm_is_asked_as_before(config):
    llm = FakeLLM(["Xin chào."])

    await _collect(_engine(config, FakeSTT(), llm, FakeTTS()))

    assert llm.histories == [()]


async def test_the_transcript_event_carries_the_confidence(config):
    stt = FakeSTT()
    stt.confidence = 0.83

    events = await _collect(_engine(config, stt, FakeLLM(["Xin chào."]), FakeTTS()))

    assert events[0] == TranscriptEvent("Lãi suất tiết kiệm là bao nhiêu?", 0.83)


async def test_the_fallback_sentence_is_marked_and_a_real_answer_is_not(config):
    failing = FakeLLM([])
    failing.error = LLMError("provider down")

    apology = await _collect(_engine(config, FakeSTT(), failing, FakeTTS()))
    answer = await _collect(_engine(config, FakeSTT(), FakeLLM(["Xin chào."]), FakeTTS()))

    assert [e.fallback for e in apology if isinstance(e, AudioEvent)] == [True, True]
    assert [e.fallback for e in answer if isinstance(e, AudioEvent)] == [False, False]


async def test_a_fallback_sentence_whose_tts_failed_is_still_marked(config):
    failing, tts = FakeLLM([]), FakeTTS()
    failing.error = LLMError("provider down")
    tts.fail_on = {config.LLM_FALLBACK_TEXT}

    events = await _collect(_engine(config, FakeSTT(), failing, tts))

    assert events[1] == AudioEvent(config.LLM_FALLBACK_TEXT, b"", RATE, True)


def test_load_loads_the_tts_model(config):
    tts = FakeTTS()

    _engine(config, FakeSTT(), FakeLLM([]), tts).load()

    assert tts.loaded


async def test_unexpected_llm_exception_speaks_the_fallback_text(config):
    llm, tts = FakeLLM([]), FakeTTS()
    llm.error = RuntimeError("sdk bug")  # not an LLMError
    engine = _engine(config, FakeSTT(), llm, tts)

    events = await asyncio.wait_for(_collect(engine), timeout=1)  # must not hang

    assert _texts(events) == [config.LLM_FALLBACK_TEXT]
    assert isinstance(events[-1], DoneEvent)


async def test_unexpected_llm_exception_after_a_sentence_ends_the_turn(config):
    llm, tts = FakeLLM(["Câu một. "]), FakeTTS()
    llm.error = RuntimeError("stream broke")
    engine = _engine(config, FakeSTT(), llm, tts)

    events = await asyncio.wait_for(_collect(engine), timeout=1)

    assert tts.spoken == ["Câu một."]
    assert isinstance(events[-1], DoneEvent)


def test_close_closes_the_tts_model(config):
    tts = FakeTTS()
    engine = _engine(config, FakeSTT(), FakeLLM([]), tts)
    engine.load()

    engine.close()

    assert not tts.loaded


# ── Knowledge lookup ──────────────────────────────────────────────────────

QUESTION = "Lãi suất tiết kiệm là bao nhiêu?"


class FakeKnowledge(Knowledge):
    def __init__(self, passages: list[Passage]) -> None:
        self.passages = passages
        self.error: Exception | None = None
        self.hang = False
        self.queries: list[str] = []

    async def search(self, query: str) -> list[Passage]:
        self.queries.append(query)
        if self.hang:
            await asyncio.Event().wait()
        if self.error:
            raise self.error
        return self.passages


def _engine_with(config: AppConfig, knowledge: FakeKnowledge) -> tuple[ConversationEngine, FakeLLM]:
    llm = FakeLLM(["Năm phần trăm."])
    engine = ConversationEngine(
        config, stt=FakeSTT(QUESTION), llm=llm, tts=FakeTTS(), knowledge=knowledge
    )
    return engine, llm


async def test_passages_are_given_to_the_llm_with_the_question(config):
    knowledge = FakeKnowledge([
        Passage("Kỳ hạn sáu tháng: 5%.", "lai-suat.pdf", 0.8),
        Passage("Kỳ hạn mười hai tháng: 6%.", "bieu-phi.docx", 0.6),
    ])
    engine, llm = _engine_with(config, knowledge)

    events = await _collect(engine)

    (prompt,) = llm.calls
    assert knowledge.queries == [QUESTION]
    assert prompt == config.KNOWLEDGE_PROMPT.format(
        passages=(
            "[1] (tài liệu: lai-suat.pdf)\nKỳ hạn sáu tháng: 5%.\n\n"
            "[2] (tài liệu: bieu-phi.docx)\nKỳ hạn mười hai tháng: 6%."
        ),
        question=QUESTION,
    )
    assert prompt.endswith(f"Câu hỏi: {QUESTION}")
    assert events[0] == TranscriptEvent(QUESTION)  # the transcript stays the bare question
    assert _texts(events) == ["Năm phần trăm."]


async def test_a_passage_with_braces_does_not_break_the_prompt(config):
    engine, llm = _engine_with(config, FakeKnowledge([Passage("{lãi} {0}", "a.txt", 0.9)]))

    await _collect(engine)

    assert "{lãi} {0}" in llm.calls[0]


async def test_no_passage_sends_the_bare_question(config):
    engine, llm = _engine_with(config, FakeKnowledge([]))

    await _collect(engine)

    assert llm.calls == [QUESTION]


async def test_a_failing_lookup_sends_the_bare_question(config):
    knowledge = FakeKnowledge([Passage("x", "a.txt", 0.9)])
    knowledge.error = KnowledgeError("store is gone")
    engine, llm = _engine_with(config, knowledge)

    events = await _collect(engine)

    assert llm.calls == [QUESTION]
    assert isinstance(events[-1], DoneEvent)


async def test_a_slow_lookup_is_abandoned_after_its_timeout(config):
    config.KNOWLEDGE_TIMEOUT_SEC = 0.05
    knowledge = FakeKnowledge([Passage("x", "a.txt", 0.9)])
    knowledge.hang = True
    engine, llm = _engine_with(config, knowledge)

    events = await asyncio.wait_for(_collect(engine), timeout=2)

    assert llm.calls == [QUESTION]
    assert _texts(events) == ["Năm phần trăm."]


async def test_an_empty_transcript_makes_no_lookup(config):
    knowledge = FakeKnowledge([Passage("x", "a.txt", 0.9)])
    engine = ConversationEngine(
        config, stt=FakeSTT("  "), llm=FakeLLM(["x."]), tts=FakeTTS(), knowledge=knowledge
    )

    await _collect(engine)

    assert knowledge.queries == []


async def test_the_search_uses_the_previous_utterance_and_the_llm_is_asked_the_current_one(config):
    knowledge = FakeKnowledge([Passage("Kỳ hạn mười hai tháng: 6%.", "lai-suat.pdf", 0.8)])
    engine, llm = _engine_with(config, knowledge)

    await _collect(engine, HISTORY)

    assert knowledge.queries == [f"Còn phí chuyển khoản?\n{QUESTION}"]
    # Only the current utterance is wrapped; the history reaches the LLM as it was.
    assert llm.calls == [config.KNOWLEDGE_PROMPT.format(
        passages="[1] (tài liệu: lai-suat.pdf)\nKỳ hạn mười hai tháng: 6%.", question=QUESTION,
    )]
    assert llm.histories == [HISTORY]


async def test_the_search_query_follows_knowledge_query_history_turns(config):
    config.KNOWLEDGE_QUERY_HISTORY_TURNS = 2
    engine, _ = _engine_with(config, FakeKnowledge([]))

    await _collect(engine, HISTORY)

    assert engine._knowledge.queries == [
        f"Lãi suất kỳ hạn sáu tháng?\nCòn phí chuyển khoản?\n{QUESTION}"
    ]


async def test_with_zero_previous_turns_the_current_utterance_is_searched_alone(config):
    config.KNOWLEDGE_QUERY_HISTORY_TURNS = 0
    engine, _ = _engine_with(config, FakeKnowledge([]))

    await _collect(engine, HISTORY)

    assert engine._knowledge.queries == [QUESTION]


# ── Memory: the profile of the user ───────────────────────────────────────

PROFILE = MemoryProfile("Những điều đã biết:\n- Tên là Linh.\n- Gọi là anh.", items=2)


class FakeMemory(Memory):
    def __init__(self, profile: MemoryProfile = PROFILE) -> None:
        self.result = profile
        self.error: Exception | None = None
        self.hang = False
        self.user_ids: list[str] = []

    async def profile(self, user_id: str) -> MemoryProfile:
        self.user_ids.append(user_id)
        if self.hang:
            await asyncio.Event().wait()
        if self.error:
            raise self.error
        return self.result

    async def record_turn(self, user_id, conversation_id, turn) -> None:
        raise AssertionError("The engine only reads the profile")

    async def close_conversation(self, user_id, conversation_id) -> None:
        raise AssertionError("The engine only reads the profile")


def _engine_remembering(
    config: AppConfig, memory: FakeMemory, knowledge: FakeKnowledge | None = None
) -> tuple[ConversationEngine, FakeLLM]:
    llm = FakeLLM(["Chào anh Linh."])
    engine = ConversationEngine(
        config, stt=FakeSTT(QUESTION), llm=llm, tts=FakeTTS(), knowledge=knowledge, memory=memory
    )
    return engine, llm


async def test_the_llm_receives_the_profile_of_the_turn_s_user(config):
    memory = FakeMemory()
    engine, llm = _engine_remembering(config, memory)

    await _collect(engine, HISTORY, user_id="user-7")

    assert memory.user_ids == ["user-7"]
    assert llm.profiles == [PROFILE.text]
    assert llm.calls == [QUESTION]
    assert llm.histories == [HISTORY]


async def test_the_profile_goes_with_the_knowledge_passages(config):
    knowledge = FakeKnowledge([Passage("Kỳ hạn sáu tháng: 5%.", "lai-suat.pdf", 0.8)])
    engine, llm = _engine_remembering(config, FakeMemory(), knowledge)

    await _collect(engine, user_id="user-7")

    assert llm.profiles == [PROFILE.text]
    assert "Kỳ hạn sáu tháng: 5%." in llm.calls[0]


async def test_a_guest_makes_no_memory_read(config):
    memory = FakeMemory()
    engine, llm = _engine_remembering(config, memory)

    events = await _collect(engine)

    assert memory.user_ids == []
    assert llm.profiles == [""]
    assert _texts(events) == ["Chào anh Linh."]


async def test_a_user_with_nothing_remembered_is_answered_without_profile(config):
    engine, llm = _engine_remembering(config, FakeMemory(MemoryProfile()))

    await _collect(engine, user_id="user-7")

    assert llm.profiles == [""]


async def test_a_failing_memory_does_not_cost_the_turn(config):
    memory = FakeMemory()
    memory.error = MemoryServiceError("store down")
    engine, llm = _engine_remembering(config, memory)

    events = await _collect(engine, user_id="user-7")

    assert llm.profiles == [""]
    assert _texts(events) == ["Chào anh Linh."]
    assert isinstance(events[-1], DoneEvent)


async def test_a_slow_memory_is_abandoned_after_its_timeout(config):
    config.MEMORY_TIMEOUT_SEC = 0.05
    memory = FakeMemory()
    memory.hang = True
    engine, llm = _engine_remembering(config, memory)

    # Bounded here too: without the engine's own timeout the turn would never end.
    events = await asyncio.wait_for(_collect(engine, user_id="user-7"), timeout=2)

    assert llm.profiles == [""]
    assert _texts(events) == ["Chào anh Linh."]


async def test_an_empty_transcript_makes_no_memory_read(config):
    memory = FakeMemory()
    engine = ConversationEngine(
        config, stt=FakeSTT(text=""), llm=FakeLLM([]), tts=FakeTTS(), memory=memory
    )

    await _collect(engine, user_id="user-7")

    assert memory.user_ids == []


# ── Persona: the personality the user gave the avatar ─────────────────────

PERSONA = "Tính cách của trợ lý:\n- Tên của trợ lý: Mai\n- Cách xưng hô: xưng em, gọi anh"


class FakePersona(Persona):
    def __init__(self, text: str = PERSONA) -> None:
        self.result = text
        self.error: Exception | None = None
        self.hang = False
        self.user_ids: list[str] = []

    async def prompt(self, user_id: str) -> str:
        self.user_ids.append(user_id)
        if self.hang:
            await asyncio.Event().wait()
        if self.error:
            raise self.error
        return self.result


def _engine_in_character(
    config: AppConfig, persona: FakePersona, memory: FakeMemory | None = None
) -> tuple[ConversationEngine, FakeLLM]:
    llm = FakeLLM(["Em là Mai ạ."])
    engine = ConversationEngine(
        config, stt=FakeSTT(QUESTION), llm=llm, tts=FakeTTS(), memory=memory, persona=persona
    )
    return engine, llm


async def test_the_llm_receives_the_persona_of_the_turn_s_user(config):
    persona = FakePersona()
    engine, llm = _engine_in_character(config, persona)

    await _collect(engine, HISTORY, user_id="user-7")

    assert persona.user_ids == ["user-7"]
    assert llm.personas == [PERSONA]
    assert llm.profiles == [""]
    assert llm.calls == [QUESTION]
    assert llm.histories == [HISTORY]


async def test_the_persona_goes_with_the_profile(config):
    engine, llm = _engine_in_character(config, FakePersona(), FakeMemory())

    await _collect(engine, user_id="user-7")

    assert llm.personas == [PERSONA]
    assert llm.profiles == [PROFILE.text]


async def test_a_guest_makes_no_persona_read(config):
    persona = FakePersona()
    engine, llm = _engine_in_character(config, persona)

    events = await _collect(engine)

    assert persona.user_ids == []
    assert llm.personas == [""]
    assert _texts(events) == ["Em là Mai ạ."]


async def test_a_user_without_persona_is_answered_in_the_default_character(config):
    engine, llm = _engine_in_character(config, FakePersona(""))

    await _collect(engine, user_id="user-7")

    assert llm.personas == [""]


async def test_a_failing_persona_does_not_cost_the_turn(config):
    persona = FakePersona()
    persona.error = PersonaError("store down")
    engine, llm = _engine_in_character(config, persona, FakeMemory())

    events = await _collect(engine, user_id="user-7")

    assert llm.personas == [""]
    assert llm.profiles == [PROFILE.text]
    assert _texts(events) == ["Em là Mai ạ."]
    assert isinstance(events[-1], DoneEvent)


async def test_a_slow_persona_is_abandoned_after_its_timeout(config):
    config.PERSONA_TIMEOUT_SEC = 0.05
    persona = FakePersona()
    persona.hang = True
    engine, llm = _engine_in_character(config, persona)

    # Bounded here too: without the engine's own timeout the turn would never end.
    events = await asyncio.wait_for(_collect(engine, user_id="user-7"), timeout=2)

    assert llm.personas == [""]
    assert _texts(events) == ["Em là Mai ạ."]


async def test_an_empty_transcript_makes_no_persona_read(config):
    persona = FakePersona()
    engine = ConversationEngine(
        config, stt=FakeSTT(text=""), llm=FakeLLM([]), tts=FakeTTS(), persona=persona
    )

    await _collect(engine, user_id="user-7")

    assert persona.user_ids == []
