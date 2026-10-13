"""
Contracts of the model stages the core calls: STT, LLM, TTS, and of the knowledge lookup,
the memory and the persona.

The core depends on these interfaces only. Backends live in `backend/providers/` and are
chosen by configuration (`backend/providers/catalog.py`), never by editing a caller.
"""
from abc import ABC, abstractmethod
from typing import AsyncIterator, Sequence

from backend.models import ChatTurn, MemoryProfile, Passage, Transcript


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


# ── LLM: user text + earlier turns in → streamed Vietnamese answer text out;
#    an instruction + a text in → one whole answer out ──────────────────────

class LLMError(Exception):
    """The LLM provider failed (network, auth, timeout, bad response)."""


class LLM(ABC):
    @abstractmethod
    def stream_answer(
        self,
        user_text: str,
        history: Sequence[ChatTurn] = (),
        profile: str = "",
        persona: str = "",
    ) -> AsyncIterator[str]:
        """
        Stream the answer to one user utterance as text deltas (no reasoning text).
        `history` holds the earlier turns of the conversation, oldest first. `persona`
        is the personality the person gave the avatar, written into the system prompt
        ("" = the default persona); `profile` is what is known about the person, given
        as a second system message. No tools. Provider failures raise LLMError.
        """

    @abstractmethod
    async def complete(self, system: str, user: str) -> str:
        """
        The whole answer to one request, for work done outside a turn (reading what a
        person imported): `system` is the instruction, sent in place of the persona,
        `user` the text it applies to. Not streamed, no reasoning text, no tools. It has
        its own limits, `LLM_COMPLETE_TIMEOUT_SEC` and `LLM_COMPLETE_MAX_TOKENS`.
        Provider failures raise LLMError.
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


# ── Memory: user id in → what is known about that user out ────────────────

class MemoryServiceError(Exception):
    """The memory failed (its store). Not named MemoryError: that is a builtin."""


class Memory(ABC):
    @abstractmethod
    async def profile(self, user_id: str) -> MemoryProfile:
        """
        What is known about the user, as the text given to the LLM. Nothing known →
        an empty profile. Failures raise MemoryServiceError.
        """

    @abstractmethod
    async def record_turn(self, user_id: str, conversation_id: str, turn: ChatTurn) -> None:
        """
        One turn of a conversation of the user ended: keep it until the conversation
        closes. Called in the order of the turns, after the audio was sent, and never
        awaited by a turn. Failures raise MemoryServiceError.
        """

    @abstractmethod
    async def close_conversation(self, user_id: str, conversation_id: str) -> None:
        """
        The conversation of the user is over, its last turn recorded: gather from it
        what is worth remembering about the user. It may take as long as a request to
        the LLM and is never awaited by a turn. A conversation without any recorded turn
        is nothing to do. Failures raise MemoryServiceError.
        """


# ── Persona: user id in → the personality that user gave the avatar out ───

class PersonaError(Exception):
    """The persona failed (its store)."""


class Persona(ABC):
    @abstractmethod
    async def prompt(self, user_id: str) -> str:
        """
        The personality the user gave the avatar, as it is written into the system
        prompt: their fields over the default ones. None given → "". Failures raise
        PersonaError.
        """
