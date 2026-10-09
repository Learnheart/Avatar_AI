"""Groq LLM backend — streamed chat completion through the OpenAI-compatible API."""
from typing import AsyncIterator

from openai import AsyncOpenAI, OpenAIError

from backend.config import AppConfig
from backend.conversation.llm.base import LLM, LLMError
from backend.utils.errors import summarize_error


class GroqLLM(LLM):
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._client = AsyncOpenAI(
            api_key=config.GROQ_API_KEY,
            base_url=config.GROQ_BASE_URL,
            timeout=config.LLM_TIMEOUT_SEC,
            max_retries=config.GROQ_MAX_RETRIES,
        )

    async def stream_answer(self, user_text: str) -> AsyncIterator[str]:
        try:
            stream = await self._client.chat.completions.create(
                model=self.config.LLM_MODEL,
                stream=True,
                messages=[
                    {"role": "system", "content": self.config.SYSTEM_PROMPT},
                    {"role": "user", "content": user_text},
                ],
                max_completion_tokens=self.config.LLM_MAX_TOKENS,
                extra_body={
                    "reasoning_effort": self.config.LLM_REASONING_EFFORT,
                    "include_reasoning": False,
                },
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
            raise LLMError(f"Groq LLM request failed: {summary}") from e
