"""
LLM backend for the OpenAI Responses API — streamed response for a turn, a whole one for
`complete`.

Serves the endpoints that expose `/responses` instead of `/chat/completions`, such as the
Databricks AI Gateway: the endpoint and the model are passed in by
`backend/providers/catalog.py`.
"""
from typing import AsyncIterator, Awaitable, Callable, Sequence, Union

from openai import AsyncOpenAI, OpenAIError

from backend.config import AppConfig
from backend.core.contracts import LLM, LLMError
from backend.core.conversation.history import ASSISTANT, SYSTEM, USER, chat_messages
from backend.models import ChatTurn
from backend.utils.errors import summarize_error

_TEXT_DELTA = "response.output_text.delta"
_ERROR = "error"
_FAILED = "response.failed"
# `status` of a whole response that stopped early, and its reason at `max_output_tokens`.
_INCOMPLETE = "incomplete"
_CUT_AT_THE_LIMIT = "max_output_tokens"


def _message(role: str, text: str) -> dict:
    """What the model said earlier is an output of it; everything else is an input."""
    content_type = "output_text" if role == ASSISTANT else "input_text"
    return {"role": role, "content": [{"type": content_type, "text": text}]}


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

    async def stream_answer(
        self, user_text: str, history: Sequence[ChatTurn] = (), profile: str = ""
    ) -> AsyncIterator[str]:
        messages = chat_messages(
            history, user_text, system_prompt=self.config.SYSTEM_PROMPT, profile=profile
        )
        try:
            stream = await self._client.responses.create(
                model=self._model,
                stream=True,
                # The system prompt travels as a message, not as `instructions`: a gateway
                # that ignores unknown top-level fields cannot drop it silently.
                input=[_message(role, text) for role, text in messages],
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

    async def complete(self, system: str, user: str) -> str:
        try:
            response = await self._client.responses.create(
                model=self._model,
                stream=False,
                input=[_message(SYSTEM, system), _message(USER, user)],
                max_output_tokens=self.config.LLM_COMPLETE_MAX_TOKENS,
                reasoning={"effort": self._reasoning_effort},
                timeout=self.config.LLM_COMPLETE_TIMEOUT_SEC,
            )
        except OpenAIError as e:
            summary = summarize_error(e, self.config.ERROR_SUMMARY_MAX_CHARS)
            raise LLMError(f"LLM request failed: {summary}") from e
        if response.error:
            raise LLMError(f"LLM response failed: {self._summary(response.error.message)}")
        if response.status == _INCOMPLETE:
            # Half an answer is no answer to work that parses it.
            details = response.incomplete_details
            reason = details.reason if details else ""
            if reason == _CUT_AT_THE_LIMIT:
                raise LLMError(
                    "LLM answer cut at the token limit: it needs more than "
                    f"LLM_COMPLETE_MAX_TOKENS ({self.config.LLM_COMPLETE_MAX_TOKENS})"
                )
            raise LLMError(f"LLM answer incomplete: {self._summary(reason) or 'no reason given'}")
        # Only the answer: the text parts of the output, without the reasoning items.
        return response.output_text or ""

    def _summary(self, text: str) -> str:
        return " ".join((text or "").split())[: self.config.ERROR_SUMMARY_MAX_CHARS]
