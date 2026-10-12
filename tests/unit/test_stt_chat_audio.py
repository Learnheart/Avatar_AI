import base64
import io
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from openai import OpenAIError

from backend.config import AppConfig
from backend.core.contracts import STTError
from backend.providers.catalog import create_stt
from backend.providers.stt import chat_audio
from backend.providers.stt.chat_audio import ChatAudioSTT

BASE_URL = "https://dbx.local/ai-gateway/mlflow/v1"


def _reply(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeAsyncOpenAI:
    """Stands in for openai.AsyncOpenAI: records the chat request, returns a scripted reply."""

    def __init__(self, api_key, base_url, timeout, max_retries):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.calls: list[dict] = []
        self.error: Exception | None = None
        self.reply = _reply(" Xin chào quý khách. ")
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.reply


@pytest.fixture
def config() -> AppConfig:
    return AppConfig(
        STT_BACKEND="databricks",
        DATABRICKS_BASE_URL=BASE_URL,
        DATABRICKS_TOKEN="dapi-token",
        DATABRICKS_STT_MODEL="catalog.schema.hears",
    )


@pytest.fixture
def stt(config, monkeypatch) -> ChatAudioSTT:
    monkeypatch.setattr(chat_audio, "AsyncOpenAI", FakeAsyncOpenAI)
    return create_stt(config)


def _speech(samples: int = 1600) -> bytes:
    return np.full(samples, 1200, dtype="<i2").tobytes()


def test_client_is_built_from_config(stt, config):
    assert (stt._client.api_key, stt._client.base_url) == ("dapi-token", BASE_URL)
    assert stt._client.timeout == config.STT_TIMEOUT_SEC
    assert stt._client.max_retries == config.GROQ_MAX_RETRIES == 0


async def test_utterance_is_sent_as_a_wav_file_part_with_the_prompt(stt, config):
    pcm = _speech()

    transcript = await stt.transcribe(pcm, 16000)

    call = stt._client.calls[0]
    assert call["model"] == "catalog.schema.hears"
    assert call["max_completion_tokens"] == config.STT_MAX_TOKENS
    assert call["reasoning_effort"] == config.STT_REASONING_EFFORT == "minimal"
    (message,) = call["messages"]
    assert message["role"] == "user"
    text_part, file_part = message["content"]
    assert text_part == {"type": "text", "text": config.STT_TRANSCRIBE_PROMPT}
    assert file_part["type"] == "file"
    assert file_part["file"]["filename"] == "audio.wav"
    prefix, _, encoded = file_part["file"]["file_data"].partition(",")
    assert prefix == "data:audio/wav;base64"
    with wave.open(io.BytesIO(base64.b64decode(encoded)), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
        assert wav.readframes(wav.getnframes()) == pcm
    assert transcript.text == "Xin chào quý khách."
    assert transcript.language == "vi"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ([{"type": "text", "text": "Xin chào.", "thoughtSignature": "abc"}], "Xin chào."),
        ([{"type": "text", "text": "Xin "}, {"type": "other"}, {"type": "text", "text": "chào."}], "Xin chào."),
        ("  \n", ""),
        (None, ""),
    ],
)
async def test_reply_text_is_read_from_a_string_or_from_parts(stt, content, expected):
    stt._client.reply = _reply(content)

    assert (await stt.transcribe(_speech(), 16000)).text == expected


async def test_reply_without_choices_gives_empty_text(stt):
    stt._client.reply = SimpleNamespace(choices=[])

    assert (await stt.transcribe(_speech(), 16000)).text == ""


@pytest.mark.parametrize("pcm", [b"", b"\x00" * 3200])
async def test_empty_or_silent_audio_gives_empty_text_without_a_call(stt, pcm):
    transcript = await stt.transcribe(pcm, 16000)

    assert transcript.text == ""
    assert stt._client.calls == []


async def test_bad_sample_rate_raises_value_error(stt):
    with pytest.raises(ValueError, match="44100"):
        await stt.transcribe(_speech(), 44100)
    assert stt._client.calls == []


async def test_provider_error_raises_a_summarized_stt_error(stt, config):
    error = OpenAIError("<html>\n" + "<p>blocked by proxy</p>\n" * 1000)
    error.status_code = 403
    stt._client.error = error

    with pytest.raises(STTError) as raised:
        await stt.transcribe(_speech(), 16000)

    message = str(raised.value)
    assert "OpenAIError (status 403)" in message
    assert "\n" not in message
    assert len(message) < config.ERROR_SUMMARY_MAX_CHARS + 100
    assert raised.value.__cause__ is error
