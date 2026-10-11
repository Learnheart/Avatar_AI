import asyncio
from typing import AsyncIterator

import pytest

from backend.config import AppConfig
from backend.core.conversation.engine import ConversationEngine, split_sentences
from backend.core.contracts import LLM, STT, TTS, LLMError, STTError, TTSError
from backend.models import AudioEvent, DoneEvent, Transcript, TranscriptEvent

RATE = 48000


class FakeSTT(STT):
    def __init__(self, text: str = "Lãi suất tiết kiệm là bao nhiêu?") -> None:
        self.text = text
        self.delay = 0.0
        self.calls: list[tuple[bytes, int]] = []

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> Transcript:
        self.calls.append((pcm16, sample_rate))
        await asyncio.sleep(self.delay)
        return Transcript(text=self.text, language="vi")


class FakeLLM(LLM):
    """Yields scripted deltas; `hold_after` pauses the stream until `release` is set."""

    def __init__(self, deltas: list[str]) -> None:
        self.deltas = deltas
        self.error: Exception | None = None
        self.hold_after: int | None = None
        self.release = asyncio.Event()
        self.calls: list[str] = []
        self.closed = False

    async def stream_answer(self, user_text: str) -> AsyncIterator[str]:
        self.calls.append(user_text)
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


async def _collect(engine: ConversationEngine) -> list:
    return [event async for event in engine.respond(b"\x01\x00" * 160, 16000)]


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
