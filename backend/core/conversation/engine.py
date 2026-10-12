"""
ConversationEngine — the swappable boundary "audio in → text + audio out".

One turn: STT → knowledge lookup (optional) → LLM (streamed) → TTS (per sentence). The LLM keeps streaming in its own
task while finished sentences are synthesized, so the first audio does not wait for the
whole answer. Every stage runs under its own timeout.

The engine keeps nothing between turns: the earlier turns of the conversation are handed
to it with each utterance.
"""
import asyncio
import re
import time
from contextlib import aclosing
from typing import AsyncIterator, Callable, Optional, Sequence, TypeVar

from opentelemetry import trace
from opentelemetry.trace import Span

from backend.config import AppConfig
from backend.core.contracts import LLM, STT, TTS, Knowledge, LLMError, STTError, TTSError
from backend.core.conversation.history import search_query
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
    ) -> None:
        """Without `knowledge`, the LLM answers the transcript alone."""
        self.config = config
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._knowledge = knowledge

    def load(self) -> None:
        """Load local models. Call once at startup, off the event loop (blocking)."""
        self._tts.load()

    def close(self) -> None:
        """Release the local models. Call once at shutdown."""
        self._tts.close()

    async def respond(
        self, pcm16: bytes, sample_rate: int, history: Sequence[ChatTurn] = ()
    ) -> AsyncIterator[EngineEvent]:
        """Run one turn. `history` holds the earlier turns of the conversation, oldest
        first. Cancellation-safe: closing it stops the LLM stream and the TTS worker."""
        transcript = await self._transcribe(pcm16, sample_rate)
        yield TranscriptEvent(transcript.text, transcript.confidence)
        if not transcript.text.strip():
            yield DoneEvent()
            return

        question = await self._with_knowledge(transcript.text, history)
        sentences: asyncio.Queue[Optional[_QueuedSentence]] = asyncio.Queue()
        producer = asyncio.create_task(
            self._produce_sentences(question, history, sentences), name="llm-sentences"
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

    async def _produce_sentences(
        self,
        user_text: str,
        history: Sequence[ChatTurn],
        sentences: "asyncio.Queue[Optional[_QueuedSentence]]",
    ) -> None:
        """Queue each sentence of the LLM answer as soon as it is complete.
        Always ends the queue with None, so the consumer never waits on a dead producer."""
        queued = 0
        try:
            try:
                async with aclosing(self._answer_sentences(user_text, history)) as answer:
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
        self, user_text: str, history: Sequence[ChatTurn]
    ) -> AsyncIterator[str]:
        """Stream the LLM answer as speakable sentences."""
        profile = ""  # nothing is known about the person yet
        # LLM_TIMEOUT_SEC bounds the wait for each delta, first token included.
        deltas = _bounded(
            self._llm.stream_answer(user_text, history, profile),
            lambda: self.config.LLM_TIMEOUT_SEC,
            LLMError(f"LLM timed out after {self.config.LLM_TIMEOUT_SEC}s"),
        )
        buffer = ""
        answer: list[str] = []
        started = time.perf_counter()
        with detached_span(
            "llm",
            **{
                "llm.system_prompt": self.config.SYSTEM_PROMPT,
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
                        for sentence in filter(_speakable, done):
                            yield sentence

                tail = buffer.strip()
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
