"""AppConfig — the one configuration object every module receives.

This file holds the secrets and the values that depend on the machine or the provider.
Behaviour parameters are in `backend/tuning.py` and texts in `backend/prompts.py`;
AppConfig inherits both, so a module reads any of them as `config.X`. Every key of this
file and of `tuning.py` can be set in the environment (`.env`); only `groq_api_key` has
to be. Texts cannot: `prompts.py` is their only source.
"""
from dataclasses import dataclass

from dotenv import load_dotenv

from backend.prompts import Prompts
from backend.tuning import Tuning
from backend.utils.env import env_int, env_str

load_dotenv()

DEVICES = ("metal", "cuda", "cpu")
AUTO = "auto"
# Backend name for any OpenAI-compatible endpoint other than Groq (LLM and STT).
OPENAI_COMPATIBLE = "openai_compatible"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


@dataclass
class AppConfig(Tuning, Prompts):
    # ── Server ──────────────────────────────────────────────────────────
    HOST: str = env_str("HOST", "0.0.0.0")
    PORT: int = env_int("PORT", 8000)
    LOG_LEVEL: str = env_str("LOG_LEVEL", "INFO", normalize=str.upper)

    # ── Groq (STT + LLM) ────────────────────────────────────────────────
    # The env name is lowercase `groq_api_key` (CLAUDE.md §3); the SDK default is not used.
    GROQ_API_KEY: str = env_str("groq_api_key", repr=False)
    GROQ_BASE_URL: str = env_str("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

    # ── Device Capability Router ────────────────────────────────────────
    DEVICE: str = env_str("DEVICE", AUTO, normalize=str.lower)

    # ── Models and backends ─────────────────────────────────────────────
    # Backend names are the entries of `backend/providers/catalog.py`.
    # auto (picked from the device) | groq_whisper | openai_compatible
    STT_BACKEND: str = env_str("STT_BACKEND", AUTO, normalize=str.lower)
    STT_MODEL: str = env_str("STT_MODEL", "whisper-large-v3-turbo")
    # groq | openai_compatible
    LLM_BACKEND: str = env_str("LLM_BACKEND", "groq", normalize=str.lower)
    LLM_MODEL: str = env_str("LLM_MODEL", "openai/gpt-oss-20b")
    TTS_BACKEND: str = env_str("TTS_BACKEND", "zerotts", normalize=str.lower)
    # A Hugging Face repo id, or a local directory holding the weights.
    ZEROTTS_REPO: str = env_str("ZEROTTS_REPO", "zeroweight-ai/ZeroTTS")

    # ── Endpoints of the `openai_compatible` backends ───────────────────
    # Another hosted API (OpenRouter, ...) or a self-hosted server (vLLM, Ollama, ...).
    # The key may stay empty for a server that needs none.
    LLM_BASE_URL: str = env_str("LLM_BASE_URL", "")
    LLM_API_KEY: str = env_str("LLM_API_KEY", repr=False)
    STT_BASE_URL: str = env_str("STT_BASE_URL", "")
    STT_API_KEY: str = env_str("STT_API_KEY", repr=False)

    # ── Camera ──────────────────────────────────────────────────────────
    CAMERA_INDEX: int = env_int("CAMERA_INDEX", 0)
    CAMERA_URL: str = env_str("CAMERA_URL", "")
    CAMERA_ROTATE: int = env_int("CAMERA_ROTATE", 0)
    CAMERA_WIDTH: int = env_int("CAMERA_WIDTH", 1280)
    CAMERA_HEIGHT: int = env_int("CAMERA_HEIGHT", 720)

    def validate(self) -> None:
        # `auto` STT resolves to Groq Whisper on every device (providers/catalog.py).
        uses_groq = self.LLM_BACKEND == "groq" or self.STT_BACKEND in (AUTO, "groq_whisper")
        if uses_groq and not self.GROQ_API_KEY:
            raise ValueError("groq_api_key is required. Set it in .env file.")
        if self.LLM_BACKEND == OPENAI_COMPATIBLE and not self.LLM_BASE_URL:
            raise ValueError("LLM_BASE_URL is required when LLM_BACKEND=openai_compatible.")
        if self.STT_BACKEND == OPENAI_COMPATIBLE and not self.STT_BASE_URL:
            raise ValueError("STT_BASE_URL is required when STT_BACKEND=openai_compatible.")
        if self.DEVICE != AUTO and self.DEVICE not in DEVICES:
            raise ValueError(f"Invalid DEVICE: {self.DEVICE}")
        if self.LOG_LEVEL not in LOG_LEVELS:
            raise ValueError(f"Invalid LOG_LEVEL: {self.LOG_LEVEL}")
        if self.TRACKING_FPS <= 0:
            raise ValueError("TRACKING_FPS must be positive.")
        if self.TTS_CHUNK_MS <= 0:
            raise ValueError("TTS_CHUNK_MS must be positive.")
