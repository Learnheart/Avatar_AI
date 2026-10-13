"""
ConversationEngine — the swappable boundary "audio in → text + audio out".

One turn: STT → knowledge lookup (optional) → persona and profile of the user (optional)
→ LLM (streamed) → TTS (per sentence). The LLM keeps streaming in its own
task while finished sentences are synthesized, so the first audio does not wait for the
whole answer. Every stage runs under its own timeout.

The engine keeps nothing between turns: the earlier turns of the conversation and the id
of its user are handed to it with each utterance.
"""
import asyncio
import re
import time
import unicodedata
from contextlib import aclosing
from typing import AsyncIterator, Callable, Optional, Sequence, TypeVar

from opentelemetry import trace
from opentelemetry.trace import Span

from backend.config import AppConfig
from backend.core.contracts import (
    LLM, STT, TTS, Knowledge, LLMError, Memory, Persona, STTError, TTSError,
)
from backend.core.conversation.history import search_query, system_prompt
from backend.models import (
    PCM_SAMPLE_WIDTH,
    AudioEvent,
    ChatTurn,
    DoneEvent,
    EngineEvent,
    Passage,
    Transcript,
    TranscriptEvent,
)
from backend.utils.errors import summarize_error
from backend.utils.logging import get_logger
from backend.utils.telemetry import detached_span, fail, stage_duration, tracer

logger = get_logger("engine")

T = TypeVar("T")

# A sentence ends at . ! ? … followed by whitespace, or at a newline. Requiring the
# whitespace keeps "1.000.000" and "3.5" in one piece; an ending still waiting for its
# next character stays in the tail.
_SENTENCE_END = re.compile(r"[.!?…]+[\"')\]”’]*\s+|\n+")


def split_sentences(text: str) -> tuple[list[str], str]:
    """Cut the completed sentences off the front of `text`. Returns (sentences, tail)."""
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        sentence = text[start:match.end()].strip()
        if sentence:
            sentences.append(sentence)
        start = match.end()
    return sentences, text[start:]


# What is drawn and never pronounced: the pictographs (flags and skin tones included),
# the symbol and dingbat blocks, and the characters that only build emoji (variation
# selector, zero width joiner, keycap, tags), with the spaces around a run of them.
_EMOJI = re.compile(
    r"\s*(?:["
    "\U0001F000-\U0001FAFF"
    "\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF"
    "\uFE0F\u200D\u20E3\U000E0020-\U000E007F"
    r"]+\s*)+"
)
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+(?=[.,!?…;:])")


def without_emoji(text: str) -> str:
    """The text as it can be spoken: every run of emoji becomes one space, and the
    space this leaves before a punctuation mark or at an end is closed. A text without
    emoji is returned as given."""
    cleaned = _EMOJI.sub(" ", text)
    if cleaned == text:
        return text
    return _SPACE_BEFORE_PUNCTUATION.sub("", cleaned).strip()


_NOT_A_WORD = re.compile(r"[^\w\s]+", re.UNICODE)


def spoken_words(text: str) -> str:
    """The text as two transcripts are compared: lower case, composed letters, no
    punctuation, one space between words."""
    plain = _NOT_A_WORD.sub(" ", unicodedata.normalize("NFC", text).lower())
    return " ".join(plain.split())


def invented(text: str, phrases: Sequence[str]) -> Optional[str]:
    """The phrase of `phrases` the transcript holds, or None. `phrases` are sentences
    an STT writes for audio that holds no such words (STT_HALLUCINATIONS)."""
    heard = f" {spoken_words(text)} "
    for phrase in phrases:
        words = spoken_words(phrase)
        if words and f" {words} " in heard:
            return phrase
    return None


def _speakable(sentence: str) -> bool:
    """False for fragments with nothing to pronounce (stray punctuation, list dashes)."""
    return any(ch.isalnum() for ch in sentence)


async def _bounded(
    stream: AsyncIterator[T], timeout: Callable[[], float], timed_out: Exception
) -> AsyncIterator[T]:
    """Yield from `stream`, waiting at most `timeout()` seconds for each item; a late item
    raises `timed_out`. Closing this generator closes `stream`."""
    async with aclosing(stream):
        while True:
            try:
                item = await asyncio.wait_for(anext(stream), timeout=timeout())
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError as e:
                raise timed_out from e
            yield item


# A sentence to speak, and whether it is the fallback text and not an answer.
_QueuedSentence = tuple[str, bool]


class ConversationEngine:
    def __init__(
        self,
        config: AppConfig,
        stt: STT,
        llm: LLM,
        tts: TTS,
        knowledge: Optional[Knowledge] = None,
        memory: Optional[Memory] = None,
        persona: Optional[Persona] = None,
    ) -> None:
        """Without `knowledge`, the LLM answers the transcript alone; without `memory`,
        it is told nothing about the person; without `persona`, it plays the default
        persona."""
        self.config = config
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._knowledge = knowledge
        self._memory = memory
        self._persona = persona

    def load(self) -> None:
        """Load local models. Call once at startup, off the event loop (blocking)."""
        self._tts.load()

    def close(self) -> None:
        """Release the local models. Call once at shutdown."""
        self._tts.close()

    async def respond(
        self,
        pcm16: bytes,
        sample_rate: int,
        history: Sequence[ChatTurn] = (),
        user_id: Optional[str] = None,
    ) -> AsyncIterator[EngineEvent]:
        """Run one turn. `history` holds the earlier turns of the conversation, oldest
        first; `user_id` is the person speaking, None for a guest.
        Cancellation-safe: closing it stops the LLM stream and the TTS worker."""
        transcript = await self._transcribe(pcm16, sample_rate)
        yield TranscriptEvent(transcript.text, transcript.confidence)
        if not transcript.text.strip():
            yield DoneEvent()
            return

        question = await self._with_knowledge(transcript.text, history)
        persona = await self._persona_prompt(user_id)
        profile = await self._profile(user_id)
        sentences: asyncio.Queue[Optional[_QueuedSentence]] = asyncio.Queue()
        producer = asyncio.create_task(
            self._produce_sentences(question, history, profile, persona, sentences),
            name="llm-sentences",
        )
        try:
            while (sentence := await sentences.get()) is not None:
                async with aclosing(self._speak(*sentence)) as events:
                    async for event in events:
                        yield event
            yield DoneEvent()
        finally:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

    # ── Stages ────────────────────────────────────────────────────────────

    async def _transcribe(self, pcm16: bytes, sample_rate: int) -> Transcript:
        audio_ms = round(len(pcm16) / PCM_SAMPLE_WIDTH / sample_rate * 1000)
        with tracer.start_as_current_span("stt", attributes={"stt.audio_ms": audio_ms}) as span:
            try:
                transcript = await asyncio.wait_for(
                    self._stt.transcribe(pcm16, sample_rate),
                    timeout=self.config.STT_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError as e:
                raise STTError(f"STT timed out after {self.config.STT_TIMEOUT_SEC}s") from e
            span.set_attributes({"stt.text": transcript.text, "stt.language": transcript.language})
            if transcript.confidence is not None:
                span.set_attribute("stt.confidence", round(transcript.confidence, 4))
            if transcript.no_speech_prob is not None:
                span.set_attribute("stt.no_speech_prob", round(transcript.no_speech_prob, 4))
            phrase = invented(transcript.text, self.config.STT_HALLUCINATIONS)
            if phrase is not None:
                # Not what the person said: the turn ends as for an empty transcript.
                span.set_attribute("stt.dropped", phrase)
                logger.warning({
                    "msg": "Transcript dropped: a sentence the STT invents",
                    "phrase": phrase,
                    "audio_ms": audio_ms,
                })
                return Transcript(
                    text="",
                    language=transcript.language,
                    confidence=transcript.confidence,
                    no_speech_prob=transcript.no_speech_prob,
                )
            return transcript

    async def _with_knowledge(self, question: str, history: Sequence[ChatTurn]) -> str:
        """The question, preceded by the passages of the uploaded documents that match
        it. The search also uses the person's previous utterances, so a follow-up finds
        the passages of its subject; only the question itself is wrapped. No passage, a
        slow or a failing lookup → the question alone: the knowledge base never costs
        the turn."""
        if self._knowledge is None:
            return question
        query = search_query(history, question, self.config.KNOWLEDGE_QUERY_HISTORY_TURNS)
        with tracer.start_as_current_span(
            "knowledge", attributes={"knowledge.query": query}
        ) as span:
            passages = await self._lookup(query, span)
            span.set_attribute("knowledge.passages", len(passages))
            for index, passage in enumerate(passages, start=1):
                span.add_event("passage", {
                    "index": index,
                    "source": passage.source,
                    "score": round(passage.score, 4),
                    "text": passage.text,
                })
        if not passages:
            return question
        listed = "\n\n".join(
            self.config.KNOWLEDGE_PASSAGE.format(index=index, source=p.source, text=p.text)
            for index, p in enumerate(passages, start=1)
        )
        return self.config.KNOWLEDGE_PROMPT.format(passages=listed, question=question)

    async def _lookup(self, query: str, span: Span) -> list[Passage]:
        """The passages for the query; [] when the lookup is slow or fails, with the
        reason on the span."""
        started = time.perf_counter()
        try:
            passages = await asyncio.wait_for(
                self._knowledge.search(query), timeout=self.config.KNOWLEDGE_TIMEOUT_SEC
            )
        except asyncio.TimeoutError as e:
            fail(span, e)
            span.set_attribute("knowledge.outcome", "timeout")
            logger.warning({
                "msg": "Knowledge lookup timed out, answering without it",
                "timeout_sec": self.config.KNOWLEDGE_TIMEOUT_SEC,
            })
            return []
        except Exception as e:
            fail(span, e)
            span.set_attribute("knowledge.outcome", "error")
            summary = summarize_error(e, self.config.ERROR_SUMMARY_MAX_CHARS)
            logger.warning({"msg": "Knowledge lookup failed, answering without it", "error": summary})
            return []

        span.set_attribute("knowledge.outcome", "hit" if passages else "empty")
        logger.info({
            "msg": "Knowledge lookup",
            "passages": len(passages),
            "top_score": round(passages[0].score, 3) if passages else None,
            "knowledge_ms": round((time.perf_counter() - started) * 1000),
        })
        return passages

    async def _persona_prompt(self, user_id: Optional[str]) -> str:
        """The personality the user gave the avatar, as it is written into the system
        prompt. A guest has none and nothing is read. None given, a slow or a failing
        read → "": the avatar plays the default persona, and the turn is not lost."""
        if self._persona is None or user_id is None:
            return ""
        with tracer.start_as_current_span("persona") as span:
            started = time.perf_counter()
            try:
                persona = await asyncio.wait_for(
                    self._persona.prompt(user_id), timeout=self.config.PERSONA_TIMEOUT_SEC
                )
            except asyncio.TimeoutError as e:
                fail(span, e)
                span.set_attribute("persona.outcome", "timeout")
                logger.warning({
                    "msg": "Persona read timed out, answering without it",
                    "timeout_sec": self.config.PERSONA_TIMEOUT_SEC,
                })
                return ""
            except Exception as e:
                fail(span, e)
                span.set_attribute("persona.outcome", "error")
                summary = summarize_error(e, self.config.ERROR_SUMMARY_MAX_CHARS)
                logger.warning({"msg": "Persona read failed, answering without it", "error": summary})
                return ""

            span.set_attributes({
                "persona.text": persona,
                "persona.outcome": "hit" if persona else "empty",
            })
            logger.info({
                "msg": "Persona read",
                "set": bool(persona),
                "persona_ms": round((time.perf_counter() - started) * 1000),
            })
            return persona

    async def _profile(self, user_id: Optional[str]) -> str:
        """What the avatar remembers about the user, as the text given to the LLM. A
        guest has none and nothing is read. Nothing remembered, a slow or a failing
        read → "": the memory never costs the turn."""
        if self._memory is None or user_id is None:
            return ""
        with tracer.start_as_current_span("memory") as span:
            started = time.perf_counter()
            try:
                profile = await asyncio.wait_for(
                    self._memory.profile(user_id), timeout=self.config.MEMORY_TIMEOUT_SEC
                )
            except asyncio.TimeoutError as e:
                fail(span, e)
                span.set_attributes({"memory.items": 0, "memory.outcome": "timeout"})
                logger.warning({
                    "msg": "Profile read timed out, answering without it",
                    "timeout_sec": self.config.MEMORY_TIMEOUT_SEC,
                })
                return ""
            except Exception as e:
                fail(span, e)
                span.set_attributes({"memory.items": 0, "memory.outcome": "error"})
                summary = summarize_error(e, self.config.ERROR_SUMMARY_MAX_CHARS)
                logger.warning({"msg": "Profile read failed, answering without it", "error": summary})
                return ""

            span.set_attributes({
                "memory.items": profile.items,
                "memory.outcome": "hit" if profile.text else "empty",
            })
            logger.info({
                "msg": "Profile read",
                "items": profile.items,
                "memory_ms": round((time.perf_counter() - started) * 1000),
            })
            return profile.text

    async def _produce_sentences(
        self,
        user_text: str,
        history: Sequence[ChatTurn],
        profile: str,
        persona: str,
        sentences: "asyncio.Queue[Optional[_QueuedSentence]]",
    ) -> None:
        """Queue each sentence of the LLM answer as soon as it is complete.
        Always ends the queue with None, so the consumer never waits on a dead producer."""
        queued = 0
        try:
            try:
                async with aclosing(
                    self._answer_sentences(user_text, history, profile, persona)
                ) as answer:
                    async for sentence in answer:
                        sentences.put_nowait((sentence, False))
                        queued += 1
            except LLMError as e:
                logger.warning({"msg": "LLM failed", "error": str(e), "sentences_before": queued})
            except Exception as e:
                summary = summarize_error(e, self.config.ERROR_SUMMARY_MAX_CHARS)
                logger.error(
                    {"msg": "LLM crashed", "error": summary, "sentences_before": queued},
                    exc_info=True,
                )

            if queued == 0:
                # LLM failure or empty answer → speak the fallback instead of staying silent.
                trace.get_current_span().add_event("llm_fallback_text_spoken")
                sentences.put_nowait((self.config.LLM_FALLBACK_TEXT, True))
        finally:
            sentences.put_nowait(None)

    async def _answer_sentences(
        self, user_text: str, history: Sequence[ChatTurn], profile: str, persona: str
    ) -> AsyncIterator[str]:
        """Stream the LLM answer as speakable sentences, without the emoji the model
        may have written. `profile` is what is known about the person and `persona` the
        personality they gave the avatar ("" = nothing)."""
        # LLM_TIMEOUT_SEC bounds the wait for each delta, first token included.
        deltas = _bounded(
            self._llm.stream_answer(user_text, history, profile, persona),
            lambda: self.config.LLM_TIMEOUT_SEC,
            LLMError(f"LLM timed out after {self.config.LLM_TIMEOUT_SEC}s"),
        )
        buffer = ""
        answer: list[str] = []
        started = time.perf_counter()
        with detached_span(
            "llm",
            **{
                "llm.system_prompt": system_prompt(self.config, persona),
                "llm.persona": persona,
                "llm.profile": profile,
                "llm.history_turns": len(history),
                "llm.prompt": user_text,
            },
        ) as span:
            try:
                async with aclosing(deltas):
                    async for delta in deltas:
                        if not answer:
                            first_token_ms = (time.perf_counter() - started) * 1000
                            span.set_attribute("llm.first_token_ms", round(first_token_ms))
                            stage_duration.record(first_token_ms, {"stage": "llm_first_token"})
                        answer.append(delta)
                        buffer += delta
                        done, buffer = split_sentences(buffer)
                        for sentence in filter(_speakable, map(without_emoji, done)):
                            yield sentence

                tail = without_emoji(buffer.strip())
                if _speakable(tail):
                    yield tail
            finally:
                # Whatever was generated, also when the stream fails or is cancelled.
                span.set_attribute("llm.answer", "".join(answer))

    async def _speak(self, sentence: str, fallback: bool = False) -> AsyncIterator[AudioEvent]:
        """Synthesize one sentence. On TTS failure the sentence is emitted as text only.
        `fallback` marks its events as the fallback text."""
        sample_rate = self._tts.sample_rate
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.TTS_TIMEOUT_SEC
        chunks = _bounded(
            self._tts.synthesize_stream(sentence),
            lambda: deadline - loop.time(),
            TTSError(f"TTS timed out after {self.config.TTS_TIMEOUT_SEC}s"),
        )
        first = True
        started = time.perf_counter()
        audio_bytes = 0
        with detached_span("tts", **{"tts.text": sentence, "tts.sample_rate": sample_rate}) as span:
            try:
                async with aclosing(chunks):
                    async for pcm in chunks:
                        if first:
                            first_chunk_ms = (time.perf_counter() - started) * 1000
                            span.set_attribute("tts.first_chunk_ms", round(first_chunk_ms))
                            stage_duration.record(first_chunk_ms, {"stage": "tts_first_chunk"})
                        audio_bytes += len(pcm)
                        yield AudioEvent(sentence if first else "", pcm, sample_rate, fallback)
                        first = False
            except TTSError as e:
                fail(span, e)
                logger.warning({"msg": "TTS failed, sending text only", "error": str(e)})
                if first:
                    yield AudioEvent(sentence, b"", sample_rate, fallback)
            finally:
                span.set_attribute(
                    "tts.audio_ms", round(audio_bytes / PCM_SAMPLE_WIDTH / sample_rate * 1000)
                )
