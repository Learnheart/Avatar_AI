"""Single source of truth for all configuration — loaded from environment variables."""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

DEVICES = ("metal", "cuda", "cpu")
AUTO = "auto"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


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
    LOG_LEVEL: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO").upper())

    # ── Groq (STT + LLM) ────────────────────────────────────────────────
    # The env name is lowercase `groq_api_key` (CLAUDE.md §3); the SDK default is not used.
    # repr=False keeps the secret out of repr(config), so out of logs and tracebacks.
    GROQ_API_KEY: str = field(
        default_factory=lambda: _env("groq_api_key", ""), repr=False
    )
    GROQ_BASE_URL: str = field(
        default_factory=lambda: _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    )
    # SDK-level retries per request. 0: a failed call goes straight to the engine's own
    # fallback instead of spending the latency budget on retries.
    GROQ_MAX_RETRIES: int = field(default_factory=lambda: _env_int("GROQ_MAX_RETRIES", 0))

    # ── Device Capability Router ────────────────────────────────────────
    DEVICE: str = field(default_factory=lambda: _env("DEVICE", AUTO).lower())

    # ── Camera + tracking ───────────────────────────────────────────────
    CAMERA_INDEX: int = field(default_factory=lambda: _env_int("CAMERA_INDEX", 0))
    CAMERA_URL: str = field(default_factory=lambda: _env("CAMERA_URL", ""))
    CAMERA_ROTATE: int = field(default_factory=lambda: _env_int("CAMERA_ROTATE", 0))
    CAMERA_WIDTH: int = field(default_factory=lambda: _env_int("CAMERA_WIDTH", 1280))
    CAMERA_HEIGHT: int = field(default_factory=lambda: _env_int("CAMERA_HEIGHT", 720))
    CAMERA_RETRY_SEC: float = field(
        default_factory=lambda: _env_float("CAMERA_RETRY_SEC", 2.0)
    )
    TRACKING_FPS: int = field(default_factory=lambda: _env_int("TRACKING_FPS", 30))
    TRACKING_MAX_FACES: int = field(
        default_factory=lambda: _env_int("TRACKING_MAX_FACES", 3)
    )
    TRACKING_MIN_CONFIDENCE: float = field(
        default_factory=lambda: _env_float("TRACKING_MIN_CONFIDENCE", 0.5)
    )

    # ── VAD ─────────────────────────────────────────────────────────────
    VAD_THRESHOLD: float = field(
        default_factory=lambda: _env_float("VAD_THRESHOLD", 0.5)
    )
    VAD_MIN_SILENCE_MS: int = field(
        default_factory=lambda: _env_int("VAD_MIN_SILENCE_MS", 500)
    )
    VAD_SPEECH_PAD_MS: int = field(
        default_factory=lambda: _env_int("VAD_SPEECH_PAD_MS", 100)
    )
    VAD_PREROLL_MS: int = field(
        default_factory=lambda: _env_int("VAD_PREROLL_MS", 300)
    )

    # ── STT ─────────────────────────────────────────────────────────────
    STT_BACKEND: str = field(default_factory=lambda: _env("STT_BACKEND", AUTO).lower())
    STT_MODEL: str = field(
        default_factory=lambda: _env("STT_MODEL", "whisper-large-v3-turbo")
    )
    STT_LANGUAGE: str = field(default_factory=lambda: _env("STT_LANGUAGE", "vi"))
    STT_TIMEOUT_SEC: float = field(
        default_factory=lambda: _env_float("STT_TIMEOUT_SEC", 8.0)
    )
    STT_MAX_UTTERANCE_SEC: float = field(
        default_factory=lambda: _env_float("STT_MAX_UTTERANCE_SEC", 30.0)
    )

    # ── LLM ─────────────────────────────────────────────────────────────
    LLM_MODEL: str = field(default_factory=lambda: _env("LLM_MODEL", "openai/gpt-oss-20b"))
    LLM_REASONING_EFFORT: str = field(
        default_factory=lambda: _env("LLM_REASONING_EFFORT", "low")
    )
    LLM_MAX_TOKENS: int = field(default_factory=lambda: _env_int("LLM_MAX_TOKENS", 512))
    LLM_TIMEOUT_SEC: float = field(
        default_factory=lambda: _env_float("LLM_TIMEOUT_SEC", 8.0)
    )
    LLM_FALLBACK_TEXT: str = field(
        default_factory=lambda: _env(
            "LLM_FALLBACK_TEXT",
            "Xin lỗi, tôi chưa thể trả lời ngay lúc này. Quý khách vui lòng thử lại sau.",
        )
    )

    # ── TTS ─────────────────────────────────────────────────────────────
    TTS_BACKEND: str = field(default_factory=lambda: _env("TTS_BACKEND", "zerotts").lower())
    ZEROTTS_REPO: str = field(
        default_factory=lambda: _env("ZEROTTS_REPO", "zeroweight-ai/ZeroTTS")
    )
    ZEROTTS_VOICE: str = field(default_factory=lambda: _env("ZEROTTS_VOICE", "maichi"))
    TTS_CHUNK_MS: int = field(default_factory=lambda: _env_int("TTS_CHUNK_MS", 240))
    TTS_TIMEOUT_SEC: float = field(
        default_factory=lambda: _env_float("TTS_TIMEOUT_SEC", 10.0)
    )

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
    TURN_TIMEOUT_SEC: float = field(
        default_factory=lambda: _env_float("TURN_TIMEOUT_SEC", 30.0)
    )
    PLAYBACK_END_TIMEOUT_SEC: float = field(
        default_factory=lambda: _env_float("PLAYBACK_END_TIMEOUT_SEC", 2.0)
    )

    # ── Errors ──────────────────────────────────────────────────────────
    # Characters of an underlying error's text kept in domain error messages and logs.
    ERROR_SUMMARY_MAX_CHARS: int = field(
        default_factory=lambda: _env_int("ERROR_SUMMARY_MAX_CHARS", 200)
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
        if not self.GROQ_API_KEY:
            raise ValueError("groq_api_key is required. Set it in .env file.")
        if self.DEVICE != AUTO and self.DEVICE not in DEVICES:
            raise ValueError(f"Invalid DEVICE: {self.DEVICE}")
        if self.LOG_LEVEL not in LOG_LEVELS:
            raise ValueError(f"Invalid LOG_LEVEL: {self.LOG_LEVEL}")
        if self.TRACKING_FPS <= 0:
            raise ValueError("TRACKING_FPS must be positive.")
        if self.TTS_CHUNK_MS <= 0:
            raise ValueError("TTS_CHUNK_MS must be positive.")
