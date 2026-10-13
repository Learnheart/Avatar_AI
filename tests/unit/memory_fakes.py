"""Fakes for the memory tests: the LLM that reads an import."""
import asyncio
import json
from typing import Optional

from backend.core.contracts import LLM


class CompletingLLM(LLM):
    """Answers every `complete` with `answer`, or raises `error`; `gate` holds the call
    until it is set. `requests` holds the (system, user) texts it was given."""

    def __init__(self, answer="[]", error: Optional[Exception] = None) -> None:
        self.answer = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        self.error = error
        self.gate: Optional[asyncio.Event] = None
        self.started = asyncio.Event()
        self.requests: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> str:
        self.requests.append((system, user))
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.error:
            raise self.error
        return self.answer

    async def stream_answer(self, user_text, history=(), profile="", persona=""):
        raise AssertionError("The memory asks for a whole answer, not for a stream")
        yield  # an async generator, like the contract
