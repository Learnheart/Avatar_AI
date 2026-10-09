"""TTS backends. `create_tts` picks the one named by TTS_BACKEND."""
from typing import Callable

from backend.config import AppConfig
from backend.conversation.tts.base import TTS
from backend.conversation.tts.zerotts import ZeroTTSBackend

_BACKENDS: dict[str, Callable[[AppConfig], TTS]] = {
    "zerotts": ZeroTTSBackend,
}


def create_tts(config: AppConfig) -> TTS:
    if config.TTS_BACKEND not in _BACKENDS:
        raise ValueError(f"Unknown TTS_BACKEND: {config.TTS_BACKEND}")
    return _BACKENDS[config.TTS_BACKEND](config)
