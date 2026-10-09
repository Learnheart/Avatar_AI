"""TTS contract — answer text in → streamed Int16 LE mono PCM out."""
from abc import ABC, abstractmethod
from typing import AsyncIterator


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
