"""Domain dataclasses and enums shared across all backend modules."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time
import uuid


class AvatarState(str, Enum):
    IDLE = "idle"
    TRACKING = "tracking"
    LISTENING = "listening"
    SPEAKING = "speaking"


class AudioSource(str, Enum):
    GEMINI = "gemini"
    AZURE = "azure"
    VIETTEL = "viettel"


@dataclass
class HeadRotation:
    """Head rotation in radians, clamped to safe limits."""
    pitch: float = 0.0  # [-0.52, 0.52] ~±30°
    yaw: float = 0.0    # [-0.78, 0.78] ~±45°
    roll: float = 0.0   # [-0.35, 0.35] ~±20°
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"pitch": self.pitch, "yaw": self.yaw, "roll": self.roll}


@dataclass
class VadEvent:
    """Voice Activity Detection event."""
    event_type: str  # "speech_start" | "speech_end"
    timestamp: float = field(default_factory=time.time)
    confidence: float = 1.0


@dataclass
class SpeechOutput:
    """Audio response to be sent to frontend."""
    text_content: str
    audio_source: AudioSource
    audio_data: bytes          # Raw PCM bytes (backend) or encoded bytes (TTS)
    audio_mime_type: str       # e.g. "audio/pcm;rate=24000"
    is_final: bool = True


@dataclass
class SessionState:
    """Runtime state of the current kiosk session."""
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    avatar_state: AvatarState = AvatarState.IDLE
    last_head_rotation: HeadRotation = field(default_factory=HeadRotation)
    last_face_detected_at: Optional[float] = None
    gemini_session_active: bool = False
    last_speech_activity_at: Optional[float] = None
    is_speaking: bool = False
