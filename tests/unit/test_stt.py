import io
import math
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
        self.segments: object = "absent"  # by default the response has no such field
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        if self.segments == "absent":
            return SimpleNamespace(text=self.text)
        return SimpleNamespace(text=self.text, segments=self.segments)


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


# ── Confidence and non-speech ─────────────────────────────────────────────

def _segment(avg_logprob: float, no_speech_prob: float) -> SimpleNamespace:
    return SimpleNamespace(avg_logprob=avg_logprob, no_speech_prob=no_speech_prob)


async def test_the_segments_are_asked_for(stt):
    await stt.transcribe(_speech(), 16000)

    assert stt._client.calls[0]["response_format"] == "verbose_json"


async def test_confidence_comes_from_the_mean_logprob_of_the_segments(stt):
    stt._client.segments = [_segment(-0.2, 0.01), _segment(-0.4, 0.3)]

    transcript = await stt.transcribe(_speech(), 16000)

    assert transcript.confidence == pytest.approx(math.exp(-0.3))
    assert 0.0 <= transcript.confidence <= 1.0
    assert transcript.no_speech_prob == 0.01
    assert transcript.text == "Xin chào quý khách."


async def test_segments_may_be_dicts_and_the_confidence_stays_within_0_and_1(stt):
    stt._client.segments = [{"avg_logprob": 0.5, "no_speech_prob": 0.2}]

    transcript = await stt.transcribe(_speech(), 16000)

    assert transcript.confidence == 1.0
    assert transcript.no_speech_prob == 0.2


@pytest.mark.parametrize("segments", ["absent", None, []])
async def test_no_segments_give_no_confidence(stt, segments):
    stt._client.segments = segments

    transcript = await stt.transcribe(_speech(), 16000)

    assert transcript.confidence is None
    assert transcript.no_speech_prob is None
    assert transcript.text == "Xin chào quý khách."


async def test_every_segment_above_the_threshold_gives_an_empty_transcript(stt, config):
    config.STT_NO_SPEECH_MAX_PROB = 0.6
    stt._client.segments = [_segment(-0.9, 0.8), _segment(-1.2, 0.7)]

    transcript = await stt.transcribe(_speech(), 16000)

    assert transcript.text == ""
    assert transcript.no_speech_prob == 0.7  # still reported, for the trace


async def test_one_segment_under_the_threshold_keeps_the_text(stt, config):
    config.STT_NO_SPEECH_MAX_PROB = 0.6
    stt._client.segments = [_segment(-0.9, 0.8), _segment(-0.2, 0.5)]

    assert (await stt.transcribe(_speech(), 16000)).text == "Xin chào quý khách."


async def test_the_default_threshold_refuses_nothing(stt, config):
    assert config.STT_NO_SPEECH_MAX_PROB == 1.0
    stt._client.segments = [_segment(-3.0, 1.0)]

    assert (await stt.transcribe(_speech(), 16000)).text == "Xin chào quý khách."


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), True, "0.5", None])
async def test_a_value_that_is_not_a_finite_number_is_ignored(stt, bad):
    stt._client.segments = [
        {"avg_logprob": bad, "no_speech_prob": bad},
        {"avg_logprob": -0.2, "no_speech_prob": 0.1},
    ]

    transcript = await stt.transcribe(_speech(), 16000)

    assert transcript.confidence == pytest.approx(math.exp(-0.2))
    assert transcript.no_speech_prob == 0.1


async def test_segments_with_only_unusable_values_give_no_confidence(stt):
    stt._client.segments = [{"avg_logprob": float("nan"), "no_speech_prob": float("nan")}]

    transcript = await stt.transcribe(_speech(), 16000)

    assert transcript.confidence is None
    assert transcript.no_speech_prob is None
    assert transcript.text == "Xin chào quý khách."


async def test_a_response_without_text_fails_the_turn_instead_of_passing_as_silence(stt):
    async def no_text(**kwargs):
        return SimpleNamespace(segments=[])

    stt._client.audio.transcriptions.create = no_text

    with pytest.raises(AttributeError):
        await stt.transcribe(_speech(), 16000)
