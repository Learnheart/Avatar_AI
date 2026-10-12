"""
LLM backend for the OpenAI Responses API — streamed response.

Serves the endpoints that expose `/responses` instead of `/chat/completions`, such as the
Databricks AI Gateway: the endpoint and the model are passed in by
`backend/providers/catalog.py`.
"""
from typing import AsyncIterator, Awaitable, Callable, Union

from openai import AsyncOpenAI, OpenAIError

from backend.config import AppConfig
from backend.core.contracts import LLM, LLMError
from backend.utils.errors import summarize_error

_TEXT_DELTA = "response.output_text.delta"
_ERROR = "error"
_FAILED = "response.failed"


def _message(role: str, text: str) -> dict:
    return {"role": role, "content": [{"type": "input_text", "text": text}]}


class OpenAIResponsesLLM(LLM):
    def __init__(
        self,
        config: AppConfig,
        *,
        base_url: str,
        api_key: Union[str, Callable[[], Awaitable[str]]],
        model: str,
        reasoning_effort: str,
    ) -> None:
        self.config = config
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=config.LLM_TIMEOUT_SEC,
            max_retries=config.GROQ_MAX_RETRIES,
        )

    async def stream_answer(self, user_text: str) -> AsyncIterator[str]:
        try:
            stream = await self._client.responses.create(
                model=self._model,
                stream=True,
                # The system prompt travels as a message, not as `instructions`: a gateway
                # that ignores unknown top-level fields cannot drop it silently.
                input=[
                    _message("system", self.config.SYSTEM_PROMPT),
                    _message("user", user_text),
                ],
                max_output_tokens=self.config.LLM_MAX_TOKENS,
                # The model thinks before the first word of the answer: keep it short.
                reasoning={"effort": self._reasoning_effort},
            )
            try:
                async for event in stream:
                    # Only the answer: reasoning and lifecycle events have other types.
                    if event.type == _TEXT_DELTA:
                        if event.delta:
                            yield event.delta
                    elif event.type == _ERROR:
                        raise LLMError(f"LLM stream failed: {self._summary(event.message)}")
                    elif event.type == _FAILED:
                        error = event.response.error
                        raise LLMError(
                            f"LLM response failed: {self._summary(error.message if error else '')}"
                        )
            finally:
                await stream.close()
        except OpenAIError as e:
            summary = summarize_error(e, self.config.ERROR_SUMMARY_MAX_CHARS)
            raise LLMError(f"LLM request failed: {summary}") from e

    def _summary(self, text: str) -> str:
        return " ".join((text or "").split())[: self.config.ERROR_SUMMARY_MAX_CHARS]
