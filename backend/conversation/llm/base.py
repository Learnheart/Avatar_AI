"""LLM contract — user text in → streamed Vietnamese answer text out."""
from abc import ABC, abstractmethod
from typing import AsyncIterator


class LLMError(Exception):
    """The LLM provider failed (network, auth, timeout, bad response)."""


class LLM(ABC):
    @abstractmethod
    def stream_answer(self, user_text: str) -> AsyncIterator[str]:
        """
        Stream the answer to one user utterance as text deltas (no reasoning text).
        Fixed system prompt, no history, no tools. Provider failures raise LLMError.
        """
