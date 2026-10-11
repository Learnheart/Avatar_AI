"""
Builds the backend of every pipeline stage — the one place that says what runs.

Each stage has a contract (`<stage>/base.py`) and one file per backend. To add a backend,
write its file and register it in the stage's table below; callers do not change.

STT goes through the Device Capability Router: detect metal / cuda / cpu once at startup
and map the device to a backend name. `DEVICE` and `STT_BACKEND` override the detection.
STT is cloud in the current stack, so every device maps to `groq_whisper`; a local backend
drops in by adding it to both STT tables.
"""
import platform
import shutil
from typing import Callable

from backend.config import AUTO, AppConfig
from backend.conversation.llm.base import LLM
from backend.conversation.llm.groq_llm import GroqLLM
from backend.conversation.stt.base import STT
from backend.conversation.stt.groq_whisper import GroqWhisperSTT
from backend.conversation.tts.base import TTS
from backend.conversation.tts.zerotts import ZeroTTSBackend
from backend.utils.logging import get_logger

logger = get_logger("factory")

# ── STT ───────────────────────────────────────────────────────────────────

DEVICE_BACKENDS: dict[str, str] = {
    "metal": "groq_whisper",
    "cuda": "groq_whisper",
    "cpu": "groq_whisper",
}

_STT_BACKENDS: dict[str, Callable[[AppConfig], STT]] = {
    "groq_whisper": GroqWhisperSTT,
}


def detect_device() -> str:
    """Return "metal" (Apple Silicon), "cuda" (NVIDIA driver present) or "cpu"."""
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "metal"
    if shutil.which("nvidia-smi"):
        return "cuda"
    return "cpu"


def create_stt(config: AppConfig) -> STT:
    """Build the STT backend: `STT_BACKEND` if set, else the one mapped to the device."""
    device = detect_device() if config.DEVICE == AUTO else config.DEVICE
    name = DEVICE_BACKENDS[device] if config.STT_BACKEND == AUTO else config.STT_BACKEND
    if name not in _STT_BACKENDS:
        raise ValueError(f"Unknown STT_BACKEND: {name}")

    logger.info({"msg": "STT backend selected", "device": device, "backend": name})
    return _STT_BACKENDS[name](config)


# ── LLM ───────────────────────────────────────────────────────────────────

def create_llm(config: AppConfig) -> LLM:
    return GroqLLM(config)


# ── TTS ───────────────────────────────────────────────────────────────────

_TTS_BACKENDS: dict[str, Callable[[AppConfig], TTS]] = {
    "zerotts": ZeroTTSBackend,
}


def create_tts(config: AppConfig) -> TTS:
    """Build the TTS backend named by `TTS_BACKEND`."""
    if config.TTS_BACKEND not in _TTS_BACKENDS:
        raise ValueError(f"Unknown TTS_BACKEND: {config.TTS_BACKEND}")
    return _TTS_BACKENDS[config.TTS_BACKEND](config)
