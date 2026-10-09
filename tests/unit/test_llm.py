from types import SimpleNamespace

import pytest
from openai import OpenAIError

from backend.conversation.llm import groq_llm
from backend.conversation.llm.base import LLMError
from backend.conversation.llm.groq_llm import GroqLLM


def _chunk(content=None, reasoning=None):
    delta = SimpleNamespace(content=content, reasoning=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


class FakeStream:
    def __init__(self, chunks, error=None):
        self._chunks = list(chunks)
        self._error = error
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        if self._error:
            raise self._error
        raise StopAsyncIteration

    async def close(self):
        self.closed = True


class FakeAsyncOpenAI:
    """Stands in for openai.AsyncOpenAI: records the chat request, returns a scripted stream."""

    def __init__(self, api_key, base_url, timeout, max_retries):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.calls: list[dict] = []
        self.stream = FakeStream([])
        self.error: Exception | None = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.stream


@pytest.fixture
def llm(config, monkeypatch) -> GroqLLM:
    monkeypatch.setattr(groq_llm, "AsyncOpenAI", FakeAsyncOpenAI)
    return GroqLLM(config)


async def _collect(llm: GroqLLM, text: str = "Lãi suất tiết kiệm?") -> list[str]:
    return [delta async for delta in llm.stream_answer(text)]


async def test_content_deltas_are_yielded_and_reasoning_is_dropped(llm):
    llm._client.stream = FakeStream([
        _chunk(content=""),                       # first chunk: empty content
        _chunk(reasoning="The user asks about rates."),
        _chunk(content="Xin "),
        _chunk(content=None, reasoning="more thinking"),
        _chunk(content="chào."),
        SimpleNamespace(choices=[]),              # usage-only chunk
    ])

    assert await _collect(llm) == ["Xin ", "chào."]
    assert llm._client.stream.closed


async def test_request_is_built_from_config(llm, config):
    await _collect(llm, "Xin chào")

    call = llm._client.calls[0]
    assert (llm._client.api_key, llm._client.base_url) == ("test-key", config.GROQ_BASE_URL)
    assert llm._client.timeout == config.LLM_TIMEOUT_SEC
    assert llm._client.max_retries == config.GROQ_MAX_RETRIES == 0
    assert call["model"] == config.LLM_MODEL
    assert call["stream"] is True
    assert call["max_completion_tokens"] == config.LLM_MAX_TOKENS
    assert call["extra_body"] == {"reasoning_effort": "low", "include_reasoning": False}
    assert call["messages"] == [
        {"role": "system", "content": config.SYSTEM_PROMPT},
        {"role": "user", "content": "Xin chào"},
    ]


async def test_provider_error_on_request_raises_llm_error(llm):
    llm._client.error = OpenAIError("401 bad key")

    with pytest.raises(LLMError, match="401 bad key"):
        await _collect(llm)


async def test_provider_error_mid_stream_raises_llm_error(llm):
    llm._client.stream = FakeStream([_chunk(content="Xin ")], error=OpenAIError("reset"))

    received: list[str] = []
    with pytest.raises(LLMError, match="reset"):
        async for delta in llm.stream_answer("Xin chào"):
            received.append(delta)

    assert received == ["Xin "]
    assert llm._client.stream.closed


async def test_provider_error_body_is_not_embedded_in_the_llm_error(llm, config):
    error = OpenAIError("<html>\n" + "<p>blocked by proxy</p>\n" * 1000)
    error.status_code = 403
    llm._client.error = error

    with pytest.raises(LLMError) as raised:
        await _collect(llm)

    message = str(raised.value)
    assert "OpenAIError (status 403)" in message
    assert "\n" not in message
    assert len(message) < config.ERROR_SUMMARY_MAX_CHARS + 100
    assert raised.value.__cause__ is error
