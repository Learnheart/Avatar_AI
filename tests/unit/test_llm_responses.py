from types import SimpleNamespace

import pytest
from openai import OpenAIError

from backend.config import AppConfig
from backend.core.contracts import LLMError
from backend.models import ChatTurn
from backend.providers.catalog import create_llm
from backend.providers.llm import openai_responses
from backend.providers.llm.openai_responses import OpenAIResponsesLLM

BASE_URL = "https://dbx.local/ai-gateway/mlflow/v1"


def _delta(text):
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def _event(type_, **fields):
    return SimpleNamespace(type=type_, **fields)


def _response(text, status="completed", error=None, incomplete=None):
    """A whole (not streamed) response."""
    details = SimpleNamespace(reason=incomplete) if incomplete else None
    return SimpleNamespace(
        output_text=text, status=status, error=error, incomplete_details=details
    )


class FakeStream:
    def __init__(self, events, error=None):
        self._events = list(events)
        self._error = error
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._events:
            return self._events.pop(0)
        if self._error:
            raise self._error
        raise StopAsyncIteration

    async def close(self):
        self.closed = True


class FakeAsyncOpenAI:
    """Stands in for openai.AsyncOpenAI: records the request, returns a scripted stream."""

    def __init__(self, api_key, base_url, timeout, max_retries):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.calls: list[dict] = []
        self.stream = FakeStream([])
        self.response = _response("")
        self.error: Exception | None = None
        self.responses = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.stream if kwargs["stream"] else self.response


@pytest.fixture
def config() -> AppConfig:
    return AppConfig(
        LLM_BACKEND="databricks",
        DATABRICKS_LLM_MODEL="catalog.schema.model",
        DATABRICKS_BASE_URL=BASE_URL,
        DATABRICKS_TOKEN="dapi-token",
    )


@pytest.fixture
def llm(config, monkeypatch) -> OpenAIResponsesLLM:
    monkeypatch.setattr(openai_responses, "AsyncOpenAI", FakeAsyncOpenAI)
    return create_llm(config)


async def _collect(llm: OpenAIResponsesLLM, text: str = "Lãi suất tiết kiệm?") -> list[str]:
    return [delta async for delta in llm.stream_answer(text)]


async def test_only_output_text_deltas_are_yielded(llm):
    llm._client.stream = FakeStream([
        _event("response.created"),
        _event("response.reasoning_summary_text.delta", delta="The user asks about rates."),
        _delta(""),
        _delta("Xin "),
        _event("response.output_item.added"),
        _delta("chào."),
        _event("response.output_text.done", text="Xin chào."),
        _event("response.completed"),
    ])

    assert await _collect(llm) == ["Xin ", "chào."]
    assert llm._client.stream.closed


async def test_request_is_built_from_config(llm, config):
    await _collect(llm, "Xin chào")

    call = llm._client.calls[0]
    assert (llm._client.api_key, llm._client.base_url) == ("dapi-token", BASE_URL)
    assert llm._client.timeout == config.LLM_TIMEOUT_SEC
    assert llm._client.max_retries == config.GROQ_MAX_RETRIES == 0
    assert call == {
        "model": "catalog.schema.model",
        "stream": True,
        "max_output_tokens": config.LLM_MAX_TOKENS,
        "reasoning": {"effort": config.LLM_REASONING_EFFORT},
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": config.SYSTEM_PROMPT}],
            },
            {"role": "user", "content": [{"type": "input_text", "text": "Xin chào"}]},
        ],
    }


def _part(role, text, content_type="input_text"):
    return {"role": role, "content": [{"type": content_type, "text": text}]}


async def test_history_becomes_alternating_messages_before_the_utterance(llm, config):
    history = (ChatTurn("Lãi suất?", ("Năm phần trăm.", "Kỳ hạn sáu tháng.")), ChatTurn("còn"))

    [d async for d in llm.stream_answer("mười hai tháng thì sao?", history)]

    # What the model said earlier is an `output_text` part; the rest stays `input_text`.
    assert llm._client.calls[0]["input"] == [
        _part("system", config.SYSTEM_PROMPT),
        _part("user", "Lãi suất?"),
        _part("assistant", "Năm phần trăm. Kỳ hạn sáu tháng.", "output_text"),
        _part("user", "còn\nmười hai tháng thì sao?"),
    ]


async def test_profile_is_a_second_system_message_after_the_system_prompt(llm, config):
    [d async for d in llm.stream_answer("Xin chào", (), "Tên: Linh")]

    assert llm._client.calls[0]["input"] == [
        _part("system", config.SYSTEM_PROMPT),
        _part("system", "Tên: Linh"),
        _part("user", "Xin chào"),
    ]


async def test_provider_error_on_request_raises_llm_error(llm):
    llm._client.error = OpenAIError("401 bad token")

    with pytest.raises(LLMError, match="401 bad token"):
        await _collect(llm)


async def test_provider_error_mid_stream_raises_llm_error(llm):
    llm._client.stream = FakeStream([_delta("Xin ")], error=OpenAIError("reset"))

    received: list[str] = []
    with pytest.raises(LLMError, match="reset"):
        async for delta in llm.stream_answer("Xin chào"):
            received.append(delta)

    assert received == ["Xin "]
    assert llm._client.stream.closed


@pytest.mark.parametrize(
    "event",
    [
        _event("error", message="rate limit exceeded"),
        _event(
            "response.failed",
            response=SimpleNamespace(error=SimpleNamespace(message="rate limit exceeded")),
        ),
        _event("response.failed", response=SimpleNamespace(error=None)),
    ],
)
async def test_error_events_raise_llm_error(llm, event):
    llm._client.stream = FakeStream([_delta("Xin "), event, _delta("chào.")])

    received: list[str] = []
    with pytest.raises(LLMError, match="rate limit" if event.type == "error" else "failed"):
        async for delta in llm.stream_answer("Xin chào"):
            received.append(delta)

    assert received == ["Xin "]
    assert llm._client.stream.closed


async def test_error_text_is_summarized(llm, config):
    error = OpenAIError("<html>\n" + "<p>blocked by proxy</p>\n" * 1000)
    error.status_code = 403
    llm._client.error = error
    with pytest.raises(LLMError) as raised:
        await _collect(llm)
    assert "OpenAIError (status 403)" in str(raised.value)
    assert len(str(raised.value)) < config.ERROR_SUMMARY_MAX_CHARS + 100

    llm._client.error = None
    llm._client.stream = FakeStream([_event("error", message="x\n" * 1000)])
    with pytest.raises(LLMError) as raised:
        await _collect(llm)
    assert "\n" not in str(raised.value)
    assert len(str(raised.value)) < config.ERROR_SUMMARY_MAX_CHARS + 100


# ── complete: a whole answer, outside a turn ──────────────────────────────

async def test_complete_sends_the_given_system_text_and_returns_the_whole_answer(llm, config):
    llm._client.response = _response('[{"category": "identity"}]')

    answer = await llm.complete("Trích thông tin về người dùng.", "Tôi tên là Linh.")

    assert answer == '[{"category": "identity"}]'
    (call,) = llm._client.calls
    assert call["input"] == [
        _part("system", "Trích thông tin về người dùng."),
        _part("user", "Tôi tên là Linh."),
    ]
    assert config.SYSTEM_PROMPT not in str(call["input"])
    assert call["stream"] is False
    assert call["model"] == "catalog.schema.model"


async def test_complete_has_its_own_limits_not_those_of_a_spoken_answer(llm, config):
    await llm.complete("s", "u")

    (call,) = llm._client.calls
    assert call["max_output_tokens"] == config.LLM_COMPLETE_MAX_TOKENS == 2048
    assert call["timeout"] == config.LLM_COMPLETE_TIMEOUT_SEC == 30
    assert call["reasoning"] == {"effort": config.LLM_REASONING_EFFORT}


async def test_complete_provider_error_raises_llm_error(llm):
    llm._client.error = OpenAIError("503 upstream")

    with pytest.raises(LLMError):
        await llm.complete("s", "u")


async def test_complete_failed_response_raises_llm_error(llm):
    llm._client.response = _response(
        "", status="failed", error=SimpleNamespace(message="model overloaded")
    )

    with pytest.raises(LLMError, match="model overloaded"):
        await llm.complete("s", "u")


async def test_complete_cut_at_the_token_limit_raises_llm_error_naming_the_cause(llm, config):
    llm._client.response = _response(
        '[{"category": "identity", "te', status="incomplete", incomplete="max_output_tokens"
    )

    with pytest.raises(LLMError, match="LLM_COMPLETE_MAX_TOKENS") as raised:
        await llm.complete("s", "u")

    assert str(config.LLM_COMPLETE_MAX_TOKENS) in str(raised.value)
    assert "identity" not in str(raised.value)


async def test_complete_left_incomplete_for_another_reason_raises_llm_error(llm):
    llm._client.response = _response("[", status="incomplete", incomplete="content_filter")

    with pytest.raises(LLMError, match="content_filter"):
        await llm.complete("s", "u")
