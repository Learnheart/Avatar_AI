import io
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from openai import OpenAIError

from backend.providers.stt import openai_compatible
from backend.core.contracts import STTError
from backend.providers.stt.openai_compatible import OpenAICompatibleSTT


class FakeAsyncOpenAI:
    """Stands in for openai.AsyncOpenAI: records the transcription request."""

    def __init__(self, api_key, base_url, timeout, max_retries):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.calls: list[dict] = []
        self.error: Exception | None = None
        self.text = " Xin chào quý khách. "
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


@pytest.fixture
def stt(config, monkeypatch) -> OpenAICompatibleSTT:
    monkeypatch.setattr(openai_compatible, "AsyncOpenAI", FakeAsyncOpenAI)
    return OpenAICompatibleSTT(
        config, base_url=config.GROQ_BASE_URL, api_key=config.GROQ_API_KEY
    )


def _speech(samples: int = 1600) -> bytes:
    return np.full(samples, 1200, dtype="<i2").tobytes()


def test_client_gets_key_and_base_url_from_config(stt, config):
    assert stt._client.api_key == "test-key"
    assert stt._client.base_url == config.GROQ_BASE_URL


def test_client_fails_fast_with_the_configured_timeout_and_retries(stt, config):
    assert stt._client.timeout == config.STT_TIMEOUT_SEC
    assert stt._client.max_retries == config.GROQ_MAX_RETRIES == 0


async def test_wav_payload_is_valid_16khz_mono(stt, config):
    pcm = _speech()

    transcript = await stt.transcribe(pcm, 16000)

    call = stt._client.calls[0]
    assert call["model"] == config.STT_MODEL
    assert call["language"] == "vi"
    filename, payload, mime = call["file"]
    assert (filename, mime) == ("audio.wav", "audio/wav")
    with wave.open(io.BytesIO(payload), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.readframes(wav.getnframes()) == pcm
    assert transcript.text == "Xin chào quý khách."
    assert transcript.language == "vi"


@pytest.mark.parametrize("pcm", [b"", b"\x00" * 3200])
async def test_empty_or_silent_audio_gives_empty_text_without_a_call(stt, pcm):
    transcript = await stt.transcribe(pcm, 16000)

    assert transcript.text == ""
    assert stt._client.calls == []


async def test_bad_sample_rate_raises_value_error(stt):
    with pytest.raises(ValueError, match="44100"):
        await stt.transcribe(_speech(), 44100)
    assert stt._client.calls == []


async def test_provider_error_raises_stt_error(stt):
    stt._client.error = OpenAIError("503 from provider")

    with pytest.raises(STTError, match="503 from provider"):
        await stt.transcribe(_speech(), 16000)


async def test_provider_error_body_is_not_embedded_in_the_stt_error(stt, config):
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
