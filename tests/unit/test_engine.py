import asyncio
from typing import AsyncIterator

import pytest

from backend.config import AppConfig
from backend.core.conversation.engine import (
    ConversationEngine, SpokenSymbols, split_sentences, spoken_words, without_emoji, without_invented,
)
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

PERSONA = "- Tên của trợ lý: Mai\n- Cách xưng hô: xưng em, gọi anh"


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


# ── Emoji: written by the model, never spoken ─────────────────────────────

@pytest.mark.parametrize(
    ("written", "spoken"),
    [
        ("Trời đẹp quá nhỉ 😊", "Trời đẹp quá nhỉ"),
        ("Trời đẹp quá nhỉ 😊 Anh có kế hoạch gì chưa?", "Trời đẹp quá nhỉ Anh có kế hoạch gì chưa?"),
        ("Em nhớ rõ nè chị! 💕", "Em nhớ rõ nè chị!"),
        ("Nghe đáng yêu ghê ha 😊?", "Nghe đáng yêu ghê ha?"),
        ("Tuyệt😊vời", "Tuyệt vời"),
        ("🎉 Chúc mừng anh, xong rồi ✅.", "Chúc mừng anh, xong rồi."),
        ("Chào anh 👋🏽, em là Bông 👩‍💻 ở 🇻🇳 đây.", "Chào anh, em là Bông ở đây."),
        ("Bấm phím 1️⃣ rồi chờ ⏰ ❤️ ⭐ nhé.", "Bấm phím 1 rồi chờ nhé."),
        ("😊💕🎉", ""),
    ],
)
def test_without_emoji_removes_them_and_leaves_the_sentence_clean(written, spoken):
    assert without_emoji(written) == spoken


@pytest.mark.parametrize(
    "text",
    [
        "Lãi suất kỳ hạn sáu tháng là 5,5% một năm.",
        "Dạ, em chào chị Linh ạ! Chị cần gì không?",
        "Số 1.000.000 đồng — tức là một triệu (đã gồm thuế); gọi 1900 1234…",
        "Ngữ điệu: à á ả ã ạ, ư ứ ừ ử ữ ự, đ Đ.",
        "  hai  khoảng trắng  ",
        "",
    ],
)
def test_without_emoji_returns_a_text_without_emoji_as_given(text):
    assert without_emoji(text) == text


@pytest.mark.parametrize(
    ("written", "spoken"),
    [
        ("3 ➕ 2 ➡ 5", "3 cộng 2, 5"),
        ("3➕2", "3 cộng 2"),
        ("10 ➖ 4, 6 ✖ 2, 6 ✕ 2 và 8 ➗ 2.", "10 trừ 4, 6 nhân 2, 6 nhân 2 và 8 chia 2."),
        ("3 ➕️ 2 ➡️ 5", "3 cộng 2, 5"),                    # with the variation selector
        ("Chọn Cài đặt → Bảo mật ➡ Đổi mật khẩu.", "Chọn Cài đặt, Bảo mật, Đổi mật khẩu."),
        ("➡ Bấm vào đây ➡", "Bấm vào đây"),                # no pause at an end
        ("Xong rồi ➡.", "Xong rồi."),                      # nor before another mark
    ],
)
def test_a_symbol_with_a_spoken_form_is_said_in_words(config, written, spoken):
    assert without_emoji(written, SpokenSymbols(config.SPOKEN_SYMBOLS)) == spoken


@pytest.mark.parametrize(
    ("written", "spoken"),
    [
        ("5 ★ sao", "5 sao"),                              # not "5 sao sao"
        ("▶️ Bắt đầu", "Bắt đầu"),
        ("Bấm ▶️ để nghe, ⏸️ để dừng ‼️", "Bấm để nghe, để dừng"),
        ("Bấm phím 1️⃣ hoặc #️⃣.", "Bấm phím 1 hoặc."),      # a keycap keeps its digit
        ("Trời đẹp quá nhỉ 😊", "Trời đẹp quá nhỉ"),
    ],
)
def test_a_symbol_without_a_spoken_form_is_still_removed(config, written, spoken):
    assert without_emoji(written, SpokenSymbols(config.SPOKEN_SYMBOLS)) == spoken


def test_a_text_without_symbols_is_returned_as_given_with_the_spoken_table(config):
    text = "Số 1.000.000 đồng — tức là 2 x 3 = 6, đúng không?"

    assert without_emoji(text, SpokenSymbols(config.SPOKEN_SYMBOLS)) == text


async def test_the_symbols_of_the_answer_are_spoken_in_words(config):
    llm = FakeLLM(["Dạ, 3 ➕ 2 ", "➡ 5 ạ. ", "▶️ Mời anh hỏi tiếp."])
    tts = FakeTTS()
    engine = _engine(config, FakeSTT(), llm, tts)

    await _collect(engine)

    assert tts.spoken == ["Dạ, 3 cộng 2, 5 ạ.", "Mời anh hỏi tiếp."]


async def test_emoji_of_the_answer_are_not_spoken_shown_or_remembered(config):
    llm = FakeLLM(["Trời đẹp quá nhỉ 😊 ", "Anh có kế hoạch gì chưa? 💕"])
    tts = FakeTTS()
    engine = _engine(config, FakeSTT(), llm, tts)

    events = await _collect(engine)

    assert _texts(events) == ["Trời đẹp quá nhỉ Anh có kế hoạch gì chưa?"]
    assert tts.spoken == ["Trời đẹp quá nhỉ Anh có kế hoạch gì chưa?"]


async def test_an_emoji_cut_between_two_deltas_is_removed_whole(config):
    llm = FakeLLM(["Chào anh 👩", "\u200d💻. ", "Em là Bông 1\ufe0f", "\u20e3."])
    tts = FakeTTS()
    engine = _engine(config, FakeSTT(), llm, tts)

    await _collect(engine)

    assert tts.spoken == ["Chào anh.", "Em là Bông 1."]


async def test_a_sentence_of_emoji_alone_is_not_spoken(config):
    llm = FakeLLM(["Dạ vâng ạ. ", "😊💕. ", "🎉"])
    tts = FakeTTS()
    engine = _engine(config, FakeSTT(), llm, tts)

    events = await _collect(engine)

    assert tts.spoken == ["Dạ vâng ạ."]
    assert isinstance(events[-1], DoneEvent)


async def test_an_answer_of_emoji_alone_speaks_the_fallback_text(config):
    engine = _engine(config, FakeSTT(), FakeLLM(["😊"]), FakeTTS())

    events = await _collect(engine)

    assert _texts(events) == [config.LLM_FALLBACK_TEXT]


# ── Sentences the STT invents ─────────────────────────────────────────────

INVENTED = (
    "Cảm ơn các bạn đã theo dõi và ủng hộ cho kênh lalaschool Để không bỏ lỡ những video "
    "hấp dẫn"
)


def test_spoken_words_are_lower_case_without_punctuation():
    assert spoken_words("  Cảm ơn,  các BẠN!\n(đã theo dõi…) ") == "cảm ơn các bạn đã theo dõi"
    assert spoken_words("Lãi suất 5,5% một năm?") == "lãi suất 5 5 một năm"
    # Decomposed letters compare equal to composed ones.
    assert spoken_words("ca\u0309m o\u031bn") == spoken_words("cảm ơn")


def _cut(config: AppConfig, text: str) -> str:
    kept, _ = without_invented(
        text, config.STT_HALLUCINATIONS, config.STT_HALLUCINATION_MAX_REST_WORDS
    )
    return kept


@pytest.mark.parametrize(
    "text",
    [
        INVENTED,
        "Cảm ơn các bạn đã theo dõi.",
        "cám ơn các bạn đã xem video",
        "Hãy đăng ký kênh để ủng hộ mình nhé!",
        "Hẹn gặp lại các bạn trong những video tiếp theo.",
        "Thanks for watching!",
        "Ghiền Mì Gõ",
        "Hãy đăng kí kênh để ủng hộ mình",
        "Đăng ký kênh nhé",
        "Hãy like và đăng ký kênh",
        "Đừng quên like, share và đăng ký kênh nhé",
        "Đăng ký kênh để xem những video mới nhất",
        "Các bạn hãy đăng ký kênh để nhận được những video mới nhất nhé",
        "Cảm ơn các bạn đã theo dõi và hẹn gặp lại.",
        "Ủng hộ kênh nhé các bạn",
        "Phụ đề được thực hiện bởi cộng đồng Amara.org",
        "Hãy subscribe cho kênh Ghiền Mì Gõ Để không bỏ lỡ những video hấp dẫn",
        "Hãy subscribe cho kênh La La School Để không bỏ lỡ những video hấp dẫn",
        "Thank you for watching.",
        "Cảm ơn các bạn đã lắng nghe.",
        "Chúc các bạn xem video vui vẻ.",
        "Please subscribe.",
    ],
)
def test_a_sentence_the_stt_invents_is_dropped_whole(config, text):
    kept, dropped = without_invented(
        text, config.STT_HALLUCINATIONS, config.STT_HALLUCINATION_MAX_REST_WORDS
    )

    assert kept == ""
    assert dropped and set(dropped) <= set(config.STT_HALLUCINATIONS)


@pytest.mark.parametrize(
    "text",
    [
        "Hôm nay thời tiết thế nào",
        "Cảm ơn bạn nhé",
        "Tôi đã theo dõi lãi suất cả tuần nay",
        "Kênh thanh toán nào được hỗ trợ?",
        "Cho tôi đăng ký thẻ tín dụng",
        "Tôi muốn xem video hướng dẫn",
        "Tôi muốn đăng ký kênh SMS Banking",
        "Ngân hàng có ủng hộ kênh thanh toán QR không",
        "Đăng ký kênh SMS",
        "Ngân hàng có ủng hộ cho kênh thanh toán QR không",
        "Đăng ký kênh để nhận OTP",
        "Đăng ký kênh để nhận thông báo",
        "Tôi muốn ủng hộ mình một tài khoản tiết kiệm",
        # A bank has channels too: a verb with "kênh" is what a customer asks for.
        "Hãy đăng ký kênh SMS Banking.",
        "Hãy đăng ký kênh SMS cho tôi.",
        "Hãy đăng ký kênh thông báo",
        "Nhớ đăng ký kênh eBank",
        "Đừng quên đăng ký kênh nhận OTP",
        "Ủng hộ cho kênh quỹ vắc xin",
        "Ủng hộ kênh của mình thì làm sao",
        "Subscribe cho kênh nào",
        "Đăng ký kênh nào?",
        "Cảm ơn bạn",
        "Cảm ơn các bạn đã hỗ trợ tôi",
        "Tôi muốn xem video hướng dẫn mở tài khoản",
        "  Lãi suất là 5.5% một năm.  Còn kỳ hạn sáu tháng?\n",
        "",
    ],
)
def test_what_a_person_says_is_kept_as_it_was_written(config, text):
    assert without_invented(
        text, config.STT_HALLUCINATIONS, config.STT_HALLUCINATION_MAX_REST_WORDS
    ) == (text, ())


def test_an_invented_sentence_is_cut_and_the_real_ones_are_kept(config):
    question = "Lãi suất tiết kiệm bao nhiêu?"

    assert _cut(config, f"{question} Cảm ơn các bạn đã theo dõi.") == question
    assert _cut(config, f"Cảm ơn các bạn đã theo dõi. {question}") == question
    assert _cut(config, f"Thanks for watching!\n{question} Hãy đăng ký kênh nhé.") == question
    assert _cut(
        config, f"Tôi tên là Linh. Cảm ơn các bạn đã xem video. {question}"
    ) == f"Tôi tên là Linh. {question}"


def test_an_unpunctuated_mix_of_real_and_invented_words_is_kept_whole(config):
    text = "Lãi suất tiết kiệm bao nhiêu cảm ơn các bạn đã theo dõi"

    assert without_invented(
        text, config.STT_HALLUCINATIONS, config.STT_HALLUCINATION_MAX_REST_WORDS
    ) == (text, ())


def test_the_phrases_a_cut_was_recognised_by_are_told_once_each():
    phrases = ("cảm ơn các bạn", "hẹn gặp lại", "không có trong câu")

    kept, dropped = without_invented(
        "Hẹn gặp lại. Mấy giờ rồi nhỉ các bạn? Cảm ơn các bạn! Hẹn gặp lại nhé.", phrases, 1
    )

    assert kept == "Mấy giờ rồi nhỉ các bạn?"
    assert dropped == ("hẹn gặp lại", "cảm ơn các bạn")


def test_a_sentence_is_invented_up_to_the_words_allowed_beside_the_phrase():
    phrases = ("cảm ơn các bạn",)
    text = "Xin cảm ơn các bạn rất nhiều"  # three words beside the phrase

    assert without_invented(text, phrases, 3) == ("", phrases)
    assert without_invented(text, phrases, 2) == (text, ())
    assert without_invented("Cảm ơn các bạn", phrases, 0) == ("", phrases)


def test_words_shared_by_two_phrases_are_counted_once():
    # "đăng ký kênh" belongs to both phrases: one word is left, not a negative count
    # nor three.
    phrases = ("hãy đăng ký kênh", "đăng ký kênh để")

    assert without_invented("Hãy đăng ký kênh để xem", phrases, 1) == ("", phrases)
    assert without_invented("Hãy đăng ký kênh để xem", phrases, 0)[1] == ()


def test_a_phrase_is_matched_on_whole_words():
    assert without_invented("ủng hộ kênhh", ("ủng hộ kênh",), 3) == ("ủng hộ kênhh", ())
    assert without_invented("xin ủng hộ kênh nhé", ("ủng hộ kênh",), 3) == ("", ("ủng hộ kênh",))
    assert without_invented("anything", ("", "  "), 3) == ("anything", ())


async def test_the_engine_answers_what_is_left_of_a_cut_transcript(config):
    llm = FakeLLM(["Dạ, 5,5% một năm ạ."])
    knowledge = FakeKnowledge([])
    stt = FakeSTT("Lãi suất tiết kiệm bao nhiêu? Cảm ơn các bạn đã theo dõi.")
    stt.confidence = 0.8
    engine = ConversationEngine(config, stt=stt, llm=llm, tts=FakeTTS(), knowledge=knowledge)

    events = await _collect(engine)

    assert events[0] == TranscriptEvent("Lãi suất tiết kiệm bao nhiêu?", 0.8)
    assert knowledge.queries == ["Lãi suất tiết kiệm bao nhiêu?"]
    assert llm.calls == ["Lãi suất tiết kiệm bao nhiêu?"]
    assert _texts(events) == ["Dạ, 5,5% một năm ạ."]


async def test_a_question_holding_words_of_a_phrase_is_answered(config):
    # Dropped whole by the first filter, which matched "đăng ký kênh" anywhere.
    llm = FakeLLM(["Dạ được ạ."])
    engine = _engine(config, FakeSTT("Tôi muốn đăng ký kênh SMS Banking"), llm, FakeTTS())

    events = await _collect(engine)

    assert events[0].text == "Tôi muốn đăng ký kênh SMS Banking"
    assert llm.calls == ["Tôi muốn đăng ký kênh SMS Banking"]


async def test_the_span_of_a_cut_transcript_keeps_what_was_written_and_what_was_dropped(
    config, spans
):
    written = "Lãi suất tiết kiệm bao nhiêu? Cảm ơn các bạn đã theo dõi và ủng hộ cho kênh."

    await _collect(_engine(config, FakeSTT(written), FakeLLM(["Dạ."]), FakeTTS()))

    (heard,) = [span for span in spans.get_finished_spans() if span.name == "stt"]
    assert heard.attributes["stt.text"] == written
    assert heard.attributes["stt.dropped"] == (
        "cảm ơn các bạn đã theo dõi",
        "đã theo dõi và ủng hộ cho kênh",
    )


async def test_the_words_allowed_beside_a_phrase_are_the_ones_of_the_config(config):
    said = "Dạ cảm ơn các bạn đã theo dõi chương trình hôm nay"  # five words beside
    llm = FakeLLM(["Dạ."])

    await _collect(_engine(config, FakeSTT(said), llm, FakeTTS()))
    config.STT_HALLUCINATION_MAX_REST_WORDS = 5
    await _collect(_engine(config, FakeSTT(said), llm, FakeTTS()))

    assert llm.calls == [said]


async def test_an_invented_transcript_starts_no_answer(config):
    llm = FakeLLM(["Không có gì."])
    tts = FakeTTS()
    engine = _engine(config, FakeSTT(INVENTED), llm, tts)

    events = await _collect(engine)

    assert [type(event) for event in events] == [TranscriptEvent, DoneEvent]
    assert events[0].text == ""
    assert llm.calls == [] and tts.spoken == []


async def test_an_invented_transcript_makes_no_lookup_and_no_memory_read(config):
    knowledge, memory = FakeKnowledge([]), FakeMemory()
    engine = ConversationEngine(
        config, stt=FakeSTT(INVENTED), llm=FakeLLM([]), tts=FakeTTS(),
        knowledge=knowledge, memory=memory,
    )

    await _collect(engine, user_id="user-7")

    assert knowledge.queries == [] and memory.user_ids == []


async def test_the_phrases_are_the_ones_of_the_config(config):
    config.STT_HALLUCINATIONS = ("xin chào quý khách",)
    llm = FakeLLM(["Dạ."])

    await _collect(_engine(config, FakeSTT("Xin chào, quý khách!"), llm, FakeTTS()))
    await _collect(_engine(config, FakeSTT(INVENTED), llm, FakeTTS()))

    assert llm.calls == [INVENTED]
