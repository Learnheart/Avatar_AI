"""
Contracts of the model stages the core calls: STT, LLM, TTS, and of the knowledge lookup.

The core depends on these interfaces only. Backends live in `backend/providers/` and are
chosen by configuration (`backend/providers/catalog.py`), never by editing a caller.
"""
from abc import ABC, abstractmethod
from typing import AsyncIterator

from backend.models import Passage, Transcript


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


# ── Embedder: texts in → one vector per text out ──────────────────────────

class EmbeddingError(Exception):
    """The embedding backend failed (network, auth, timeout, bad response)."""


class Embedder(ABC):
    @property
    @abstractmethod
    def signature(self) -> str:
        """Identifies the backend and model that produce the vectors. Vectors of
        different signatures cannot be compared."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """
        One vector per text, in the same order, all of the same dimension.
        No text → no vector. Provider failures raise EmbeddingError.
        """


# ── Knowledge: user question in → passages of the uploaded documents out ──

class KnowledgeError(Exception):
    """The knowledge base failed (embedding, store)."""


class Knowledge(ABC):
    @abstractmethod
    async def search(self, query: str) -> list[Passage]:
        """
        The passages most relevant to the question, best first. Nothing relevant or an
        empty base → []. Failures raise KnowledgeError.
        """
