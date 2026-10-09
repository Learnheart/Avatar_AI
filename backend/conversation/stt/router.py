"""
Device Capability Router — picks the STT backend for this machine.

Detect metal / cuda / cpu once at startup and map the device to a backend name.
`DEVICE` and `STT_BACKEND` override the detection. STT is cloud in the current stack, so
every device maps to `groq_whisper`; a local backend drops in by adding it to both maps.
"""
import platform
import shutil
from typing import Callable

from backend.config import AUTO, AppConfig
from backend.conversation.stt.base import STT
from backend.conversation.stt.groq_whisper import GroqWhisperSTT
from backend.utils.logging import get_logger

logger = get_logger("stt.router")

DEVICE_BACKENDS: dict[str, str] = {
    "metal": "groq_whisper",
    "cuda": "groq_whisper",
    "cpu": "groq_whisper",
}

_BACKENDS: dict[str, Callable[[AppConfig], STT]] = {
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
    if name not in _BACKENDS:
        raise ValueError(f"Unknown STT_BACKEND: {name}")

    logger.info({"msg": "STT backend selected", "device": device, "backend": name})
    return _BACKENDS[name](config)
