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

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "size_bytes": self.size_bytes,
            "chunk_count": self.chunk_count,
            "created_at": self.created_at,
        }


# ── ConversationEngine events ─────────────────────────────────────────────

@dataclass
class TranscriptEvent:
    """Emitted once per turn, right after STT."""
    text: str


@dataclass
class AudioEvent:
    """One chunk of Int16 LE mono PCM. `text` is the sentence this audio belongs to,
    set on the first chunk of a sentence and "" afterwards."""
    text: str
    pcm: bytes
    sample_rate: int


@dataclass
class DoneEvent:
    """Emitted last: the turn produced everything it will produce."""


EngineEvent = Union[TranscriptEvent, AudioEvent, DoneEvent]
