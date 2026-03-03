"""Single source of truth for all configuration — loaded from environment variables."""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


@dataclass
class AppConfig:
    # ── Server ──────────────────────────────────────────────────────────
    HOST: str = field(default_factory=lambda: _env("HOST", "0.0.0.0"))
    PORT: int = field(default_factory=lambda: _env_int("PORT", 8000))

    # ── Camera ──────────────────────────────────────────────────────────
    CAMERA_INDEX: int = field(default_factory=lambda: _env_int("CAMERA_INDEX", 0))
    TRACKING_FPS: int = field(default_factory=lambda: _env_int("TRACKING_FPS", 30))
    SNAPSHOT_INTERVAL_SEC: float = field(
        default_factory=lambda: _env_float("SNAPSHOT_INTERVAL_SEC", 2.5)
    )
    SNAPSHOT_QUALITY: int = field(
        default_factory=lambda: _env_int("SNAPSHOT_QUALITY", 70)
    )
    SNAPSHOT_MAX_WIDTH: int = field(
        default_factory=lambda: _env_int("SNAPSHOT_MAX_WIDTH", 640)
    )

    # ── VAD ─────────────────────────────────────────────────────────────
    VAD_THRESHOLD: float = field(
        default_factory=lambda: _env_float("VAD_THRESHOLD", 0.5)
    )
    VAD_SILENCE_TIMEOUT_SEC: float = field(
        default_factory=lambda: _env_float("VAD_SILENCE_TIMEOUT_SEC", 30.0)
    )

    # ── Gemini ──────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = field(default_factory=lambda: _env("GEMINI_API_KEY", ""))
    GEMINI_MODEL: str = field(
        default_factory=lambda: _env("GEMINI_MODEL", "gemini-2.0-flash-live-001")
    )
    GEMINI_RESPONSE_TIMEOUT_SEC: float = field(
        default_factory=lambda: _env_float("GEMINI_RESPONSE_TIMEOUT_SEC", 15.0)
    )
    GEMINI_VOICE: str = field(default_factory=lambda: _env("GEMINI_VOICE", "Kore"))

    # ── TTS ─────────────────────────────────────────────────────────────
    TTS_MODE: str = field(default_factory=lambda: _env("TTS_MODE", "gemini"))
    AZURE_TTS_KEY: str = field(default_factory=lambda: _env("AZURE_TTS_KEY", ""))
    AZURE_TTS_REGION: str = field(
        default_factory=lambda: _env("AZURE_TTS_REGION", "eastasia")
    )
    AZURE_TTS_VOICE: str = field(
        default_factory=lambda: _env("AZURE_TTS_VOICE", "vi-VN-HoaiMyNeural")
    )
    VIETTEL_TTS_KEY: str = field(default_factory=lambda: _env("VIETTEL_TTS_KEY", ""))
    VIETTEL_TTS_URL: str = field(default_factory=lambda: _env("VIETTEL_TTS_URL", ""))

    # ── Head rotation clamps (radians) ──────────────────────────────────
    HEAD_PITCH_MAX: float = field(
        default_factory=lambda: _env_float("HEAD_PITCH_MAX", 0.52)
    )
    HEAD_YAW_MAX: float = field(
        default_factory=lambda: _env_float("HEAD_YAW_MAX", 0.78)
    )
    HEAD_ROLL_MAX: float = field(
        default_factory=lambda: _env_float("HEAD_ROLL_MAX", 0.35)
    )

    # ── Timeouts ────────────────────────────────────────────────────────
    NO_FACE_TIMEOUT_SEC: float = field(
        default_factory=lambda: _env_float("NO_FACE_TIMEOUT_SEC", 3.0)
    )
    GEMINI_IDLE_TIMEOUT_SEC: float = field(
        default_factory=lambda: _env_float("GEMINI_IDLE_TIMEOUT_SEC", 30.0)
    )

    # ── System prompt ───────────────────────────────────────────────────
    SYSTEM_PROMPT: str = field(
        default_factory=lambda: _env(
            "SYSTEM_PROMPT",
            (
                "Bạn là Chuyên viên tài chính VIP tại ngân hàng. "
                "Hãy trả lời bằng tiếng Việt, phong cách chuyên nghiệp, trang trọng và thân thiện. "
                "Câu trả lời ngắn gọn, rõ ràng, tối đa 2-3 câu mỗi lượt."
            ),
        )
    )

    def validate(self) -> None:
        if not self.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is required. Set it in .env file.")
        if self.TTS_MODE not in ("gemini", "azure", "viettel"):
            raise ValueError(f"Invalid TTS_MODE: {self.TTS_MODE}")
        if self.TTS_MODE == "azure" and not self.AZURE_TTS_KEY:
            raise ValueError("AZURE_TTS_KEY is required when TTS_MODE=azure.")
        if self.TTS_MODE == "viettel" and not self.VIETTEL_TTS_KEY:
            raise ValueError("VIETTEL_TTS_KEY is required when TTS_MODE=viettel.")
