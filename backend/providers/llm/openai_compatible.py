"""
LLM backend for any OpenAI-compatible chat API — streamed chat completion.

Serves hosted APIs (Groq, OpenRouter, ...) and self-hosted servers (vLLM, Ollama, ...):
the endpoint is passed in by `backend/providers/catalog.py`, the model comes from
`LLM_MODEL`.
"""
from typing import AsyncIterator, Optional

from openai import AsyncOpenAI, OpenAIError

from backend.config import AppConfig
from backend.core.contracts import LLM, LLMError
from backend.utils.errors import summarize_error


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
