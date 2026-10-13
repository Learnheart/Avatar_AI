"""
LLM backend for any OpenAI-compatible chat API — streamed chat completion for a turn,
a whole one for `complete`.

Serves hosted APIs (Groq, OpenRouter, ...) and self-hosted servers (vLLM, Ollama, ...):
the endpoint is passed in by `backend/providers/catalog.py`, the model comes from
`LLM_MODEL`.
"""
from typing import AsyncIterator, Optional, Sequence

from openai import AsyncOpenAI, OpenAIError

from backend.config import AppConfig
from backend.core.contracts import LLM, LLMError
from backend.core.conversation.history import SYSTEM, USER, chat_messages, system_prompt
from backend.models import ChatTurn
from backend.utils.errors import summarize_error

# `finish_reason` of an answer that stopped at `max_completion_tokens`.
_CUT_AT_THE_LIMIT = "length"


class OpenAICompatibleLLM(LLM):
    def __init__(
        self,
        config: AppConfig,
        *,
        base_url: str,
        api_key: str,
        extra_body: Optional[dict] = None,
    ) -> None:
        """`extra_body` carries the request parameters only this provider understands."""
        self.config = config
        self._extra_body = extra_body
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=config.LLM_TIMEOUT_SEC,
            max_retries=config.GROQ_MAX_RETRIES,
        )

    async def stream_answer(
        self,
        user_text: str,
        history: Sequence[ChatTurn] = (),
        profile: str = "",
        persona: str = "",
    ) -> AsyncIterator[str]:
        messages = chat_messages(
            history,
            user_text,
            system_prompt=system_prompt(self.config, persona),
            profile=profile,
        )
        try:
            stream = await self._client.chat.completions.create(
                model=self.config.LLM_MODEL,
                stream=True,
                messages=[{"role": role, "content": text} for role, text in messages],
                max_completion_tokens=self.config.LLM_MAX_TOKENS,
                extra_body=self._extra_body,
            )
            try:
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    # Only the answer: reasoning (if any) arrives in `delta.reasoning`.
                    content = chunk.choices[0].delta.content
                    if content:
                        yield content
            finally:
                await stream.close()
        except OpenAIError as e:
            summary = summarize_error(e, self.config.ERROR_SUMMARY_MAX_CHARS)
            raise LLMError(f"LLM request failed: {summary}") from e

    async def complete(self, system: str, user: str) -> str:
        try:
            completion = await self._client.chat.completions.create(
                model=self.config.LLM_MODEL,
                stream=False,
                messages=[
                    {"role": SYSTEM, "content": system},
                    {"role": USER, "content": user},
                ],
                max_completion_tokens=self.config.LLM_COMPLETE_MAX_TOKENS,
                extra_body=self._extra_body,
                timeout=self.config.LLM_COMPLETE_TIMEOUT_SEC,
            )
        except OpenAIError as e:
            summary = summarize_error(e, self.config.ERROR_SUMMARY_MAX_CHARS)
            raise LLMError(f"LLM request failed: {summary}") from e
        if not completion.choices:
            raise LLMError("LLM request failed: the answer holds no choice")
        choice = completion.choices[0]
        if choice.finish_reason == _CUT_AT_THE_LIMIT:
            # Half an answer is no answer to work that parses it.
            raise LLMError(
                "LLM answer cut at the token limit: it needs more than "
                f"LLM_COMPLETE_MAX_TOKENS ({self.config.LLM_COMPLETE_MAX_TOKENS})"
            )
        # Only the answer: reasoning (if any) is in `message.reasoning`.
        return choice.message.content or ""
