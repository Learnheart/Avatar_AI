"""
A primary backend with a backup behind it, for the stages that call a provider (STT, LLM,
embedding).

The backup takes over when the primary fails or does not answer within
`FALLBACK_PRIMARY_TIMEOUT_SEC`. After a failure the primary is skipped for
`FALLBACK_COOLDOWN_SEC`, so a network that blocks it costs one slow turn, not every turn.
Which backend is the backup is configuration: `STT_BACKEND_BACKUP`, `LLM_BACKEND_BACKUP`,
`EMBEDDING_BACKEND_BACKUP`.
"""
import asyncio
import time
from contextlib import aclosing
from typing import AsyncIterator

from backend.config import AppConfig
from backend.core.contracts import LLM, STT, Embedder, EmbeddingError, LLMError, STTError
from backend.models import Transcript
from backend.utils.logging import get_logger

logger = get_logger("providers.fallback")


class _Primary:
    """Whether the primary is tried on this request, and the cooldown after it failed."""

    def __init__(self, config: AppConfig, stage: str) -> None:
        self._config = config
        self._stage = stage
        self._skip_until = 0.0

    @property
    def timeout(self) -> float:
        return self._config.FALLBACK_PRIMARY_TIMEOUT_SEC

    def available(self) -> bool:
        return time.monotonic() >= self._skip_until

    def failed(self, reason: str) -> None:
        cooldown = self._config.FALLBACK_COOLDOWN_SEC
        self._skip_until = time.monotonic() + cooldown
        logger.warning({
            "msg": "Primary backend failed, using the backup",
            "stage": self._stage,
            "error": reason,
            "cooldown_sec": cooldown,
        })


class FallbackSTT(STT):
    def __init__(self, config: AppConfig, primary: STT, backup: STT) -> None:
        self._primary = primary
        self._backup = backup
        self._state = _Primary(config, "stt")

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> Transcript:
        if self._state.available():
            try:
                # ValueError (bad input) is not a provider failure: it is not caught here.
                return await asyncio.wait_for(
                    self._primary.transcribe(pcm16, sample_rate), self._state.timeout
                )
            except STTError as e:
                self._state.failed(str(e))
            except asyncio.TimeoutError:
                self._state.failed(f"no answer within {self._state.timeout}s")
        return await self._backup.transcribe(pcm16, sample_rate)


class FallbackLLM(LLM):
    def __init__(self, config: AppConfig, primary: LLM, backup: LLM) -> None:
        self._primary = primary
        self._backup = backup
        self._state = _Primary(config, "llm")

    async def stream_answer(self, user_text: str) -> AsyncIterator[str]:
        if self._state.available():
            async with aclosing(self._primary.stream_answer(user_text)) as answer:
                try:
                    # Only the wait for the first delta is bounded: that is where a dead
                    # provider shows.
                    first = await asyncio.wait_for(anext(answer), self._state.timeout)
                except StopAsyncIteration:
                    return
                except LLMError as e:
                    self._state.failed(str(e))
                except asyncio.TimeoutError:
                    self._state.failed(f"no first delta within {self._state.timeout}s")
                else:
                    # Part of the answer is out: a later failure is not replayed by the
                    # backup, it reaches the caller.
                    yield first
                    async for delta in answer:
                        yield delta
                    return

        async with aclosing(self._backup.stream_answer(user_text)) as answer:
            async for delta in answer:
                yield delta


class FallbackEmbedder(Embedder):
    """The backup must serve the same model as the primary: its vectors are compared
    with the ones the primary put in the index, which is why the signature stays the
    primary's and nothing is re-indexed when the backup answers."""

    def __init__(self, config: AppConfig, primary: Embedder, backup: Embedder) -> None:
        self._primary = primary
        self._backup = backup
        self._state = _Primary(config, "embedding")

    @property
    def signature(self) -> str:
        return self._primary.signature

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self._state.available():
            try:
                # No timeout of its own: an upload embeds many chunks and may take
                # seconds. The primary's request timeout arrives as an EmbeddingError.
                return await self._primary.embed(texts)
            except EmbeddingError as e:
                self._state.failed(str(e))
            except asyncio.CancelledError:
                # The caller gave up waiting (the lookup timeout of a turn): this
                # request is lost, the next ones go to the backup.
                self._state.failed("the caller stopped waiting for it")
                raise
        return await self._backup.embed(texts)
