"""Domain dataclasses and enums shared across all backend modules."""
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Union

# Microphone audio contract (design.md §3): PCM Int16 LE, mono, 16 kHz.
MIC_SAMPLE_RATE = 16000
PCM_SAMPLE_WIDTH = 2  # bytes per Int16 sample


class AvatarState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class VADEvent(str, Enum):
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"


@dataclass
class HeadRotation:
    """Head rotation in radians, clamped to the HEAD_*_MAX limits."""
    pitch: float = 0.0
    yaw: float = 0.0
    roll: float = 0.0

    def to_dict(self) -> dict:
        return {"pitch": self.pitch, "yaw": self.yaw, "roll": self.roll}


@dataclass
class TrackingResult:
    """Output of HeadTracker: presence + rotation of the largest face."""
    present: bool = False
    head_rotation: Optional[HeadRotation] = None


@dataclass
class Transcript:
    """Output of every STT backend — one fixed shape regardless of device."""
    text: str
    language: str
    # How sure the backend is of the text, 0–1. None: the backend cannot tell.
    confidence: Optional[float] = None
    # The lowest probability, over the segments, that the audio holds no speech.
    # None: the backend cannot tell.
    no_speech_prob: Optional[float] = None


@dataclass(frozen=True)
class ChatTurn:
    """One turn of a conversation as it happened: what the person said and what the
    avatar was heard saying. `sentences` may be empty (the turn was cut or failed before
    anything was spoken)."""
    user_text: str
    sentences: tuple[str, ...] = ()
    # The answer was cut before its end (barge-in, reset, disconnect, shutdown).
    interrupted: bool = False
    # STT confidence of `user_text`, 0–1; None when the backend gave none.
    confidence: Optional[float] = None

    @property
    def answer(self) -> str:
        return " ".join(self.sentences)


@dataclass
class Passage:
    """A piece of an uploaded document returned by a knowledge search. `score` is the
    cosine similarity to the query (1 = same direction)."""
    text: str
    source: str
    score: float


@dataclass
class KnowledgeDocument:
    """An uploaded document, as listed by the knowledge base."""
    id: str
    name: str
    size_bytes: int
    chunk_count: int
    created_at: str  # ISO 8601, UTC
    # False: kept in the base but left out of the lookups.
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "size_bytes": self.size_bytes,
            "chunk_count": self.chunk_count,
            "created_at": self.created_at,
            "enabled": self.enabled,
        }


# ── ConversationEngine events ─────────────────────────────────────────────

@dataclass
class TranscriptEvent:
    """Emitted once per turn, right after STT. `confidence` is the STT's, 0–1 or None."""
    text: str
    confidence: Optional[float] = None


@dataclass
class AudioEvent:
    """One chunk of Int16 LE mono PCM. `text` is the sentence this audio belongs to,
    set on the first chunk of a sentence and "" afterwards. `fallback` marks the
    sentence spoken in place of an answer the LLM did not give (LLM_FALLBACK_TEXT)."""
    text: str
    pcm: bytes
    sample_rate: int
    fallback: bool = False


@dataclass
class DoneEvent:
    """Emitted last: the turn produced everything it will produce."""


EngineEvent = Union[TranscriptEvent, AudioEvent, DoneEvent]
