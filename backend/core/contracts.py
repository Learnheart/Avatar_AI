"""
Contracts of the model stages the core calls: STT, LLM, TTS.

The core depends on these interfaces only. Backends live in `backend/providers/` and are
chosen by configuration (`backend/providers/catalog.py`), never by editing a caller.
"""
from abc import ABC, abstractmethod
from typing import AsyncIterator

from backend.models import Transcript


# ── STT: utterance audio in → Transcript out, one shape for every backend ──

class STTError(Exception):
    """The STT provider failed (network, auth, timeout, bad response)."""


class STT(ABC):
    @abstractmethod
    async def transcribe(self, pcm16: bytes, sample_rate: int) -> Transcript:
        """
        Transcribe one utterance of Int16 LE mono PCM.
        Empty / silent audio → Transcript(text=""). Unsupported sample rate → ValueError.
        Provider failures raise STTError.
        """


# ── LLM: user text in → streamed Vietnamese answer text out ───────────────

class LLMError(Exception):
    """The LLM provider failed (network, auth, timeout, bad response)."""


class LLM(ABC):
    @abstractmethod
    def stream_answer(self, user_text: str) -> AsyncIterator[str]:
        """
        Stream the answer to one user utterance as text deltas (no reasoning text).
        Fixed system prompt, no history, no tools. Provider failures raise LLMError.
        """


# ── TTS: answer text in → streamed Int16 LE mono PCM out ──────────────────

class TTSError(Exception):
    """The TTS backend failed (model load, synthesis)."""


class TTS(ABC):
    @abstractmethod
    def load(self) -> None:
        """Load the model. Call once at startup, off the event loop (blocking)."""

    @abstractmethod
    def close(self) -> None:
        """Release the model's resources. Call once at shutdown."""

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """Sample rate (Hz) of the PCM yielded by synthesize_stream. Valid after load()."""

    @abstractmethod
    def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """
        Synthesize one sentence as chunks of Int16 LE mono PCM.
        Empty text yields nothing. Failures raise TTSError.
        """
