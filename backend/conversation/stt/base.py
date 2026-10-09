"""STT contract — utterance audio in → Transcript out, one shape for every backend."""
from abc import ABC, abstractmethod

from backend.models import Transcript


class STTError(Exception):
    """The STT provider failed (network, auth, timeout, bad response)."""


class STT(ABC):
    @abstractmethod
    async def transcribe(self, pcm16: bytes, sample_rate: int) -> Transcript:
        """
        Transcribe one utterance of Int16 LE mono PCM.
        Empty / silent audio → Transcript(text=""). Unsupported sample rate → ValueError.
        Provider failures raise STTError.
        """
