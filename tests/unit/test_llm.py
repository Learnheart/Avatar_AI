from types import SimpleNamespace

import pytest
from openai import OpenAIError

from backend.providers.catalog import create_llm
from backend.providers.llm import openai_compatible
from backend.core.contracts import LLMError
from backend.models import ChatTurn
from backend.providers.llm.openai_compatible import OpenAICompatibleLLM


def _chunk(content=None, reasoning=None):
    delta = SimpleNamespace(content=content, reasoning=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _completion(content, reasoning=None, finish_reason="stop"):
    """A whole (not streamed) chat completion."""
    message = SimpleNamespace(content=content, reasoning=reasoning)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )


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
        self.completion = _completion("")
        self.error: Exception | None = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.stream if kwargs["stream"] else self.completion


@pytest.fixture
def llm(config, monkeypatch) -> OpenAICompatibleLLM:
    monkeypatch.setattr(openai_compatible, "AsyncOpenAI", FakeAsyncOpenAI)
    return create_llm(config)


async def _collect(llm: OpenAICompatibleLLM, text: str = "Lãi suất tiết kiệm?") -> list[str]:
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


async def test_history_becomes_alternating_messages_before_the_utterance(llm, config):
    history = (ChatTurn("Lãi suất?", ("Năm phần trăm.", "Kỳ hạn sáu tháng.")), ChatTurn("còn"))

    [d async for d in llm.stream_answer("mười hai tháng thì sao?", history)]

    assert llm._client.calls[0]["messages"] == [
        {"role": "system", "content": config.SYSTEM_PROMPT},
        {"role": "user", "content": "Lãi suất?"},
        {"role": "assistant", "content": "Năm phần trăm. Kỳ hạn sáu tháng."},
        {"role": "user", "content": "còn\nmười hai tháng thì sao?"},
    ]


async def test_profile_is_a_second_system_message_after_the_system_prompt(llm, config):
    [d async for d in llm.stream_answer("Xin chào", (), "Tên: Linh")]

    assert llm._client.calls[0]["messages"] == [
        {"role": "system", "content": config.SYSTEM_PROMPT},
        {"role": "system", "content": "Tên: Linh"},
        {"role": "user", "content": "Xin chào"},
    ]


async def test_persona_is_a_system_message_between_the_system_prompt_and_the_profile(llm, config):
    [d async for d in llm.stream_answer("Xin chào", (), "Tên: Linh", "Tên trợ lý: Mai")]

    assert llm._client.calls[0]["messages"] == [
        {"role": "system", "content": config.SYSTEM_PROMPT},
        {"role": "system", "content": "Tên trợ lý: Mai"},
        {"role": "system", "content": "Tên: Linh"},
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


# ── complete: a whole answer, outside a turn ──────────────────────────────

async def test_complete_sends_the_given_system_text_and_returns_the_whole_answer(llm, config):
    llm._client.completion = _completion('[{"category": "identity"}]', reasoning="thinking")

    answer = await llm.complete("Trích thông tin về người dùng.", "Tôi tên là Linh.")

    assert answer == '[{"category": "identity"}]'
    (call,) = llm._client.calls
    assert call["messages"] == [
        {"role": "system", "content": "Trích thông tin về người dùng."},
        {"role": "user", "content": "Tôi tên là Linh."},
    ]
    assert config.SYSTEM_PROMPT not in str(call["messages"])
    assert call["stream"] is False
    assert call["model"] == config.LLM_MODEL


async def test_complete_has_its_own_limits_not_those_of_a_spoken_answer(llm, config):
    await llm.complete("s", "u")

    (call,) = llm._client.calls
    assert call["max_completion_tokens"] == config.LLM_COMPLETE_MAX_TOKENS == 2048
    assert call["timeout"] == config.LLM_COMPLETE_TIMEOUT_SEC == 30
    assert call["extra_body"] == {"reasoning_effort": "low", "include_reasoning": False}


@pytest.mark.parametrize("content", [None, ""])
async def test_complete_without_content_is_an_empty_answer(llm, content):
    llm._client.completion = _completion(content)

    assert await llm.complete("s", "u") == ""


async def test_complete_provider_error_raises_llm_error_without_the_body(llm):
    llm._client.error = OpenAIError("401 " + "x" * 500 + " sk-secret")

    with pytest.raises(LLMError) as raised:
        await llm.complete("s", "u")

    assert "sk-secret" not in str(raised.value)


async def test_complete_without_a_choice_raises_llm_error(llm):
    llm._client.completion = SimpleNamespace(choices=[])

    with pytest.raises(LLMError):
        await llm.complete("s", "u")


async def test_complete_cut_at_the_token_limit_raises_llm_error_naming_the_cause(llm, config):
    llm._client.completion = _completion('[{"category": "identity", "te', finish_reason="length")

    with pytest.raises(LLMError, match="LLM_COMPLETE_MAX_TOKENS") as raised:
        await llm.complete("s", "u")

    assert str(config.LLM_COMPLETE_MAX_TOKENS) in str(raised.value)
    assert "identity" not in str(raised.value)  # the cut answer is not repeated
