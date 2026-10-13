"""
STT backend for any OpenAI-compatible transcription API (Whisper).

Serves hosted APIs (Groq, ...) and self-hosted Whisper servers: the endpoint is passed in
by `backend/providers/catalog.py`, the model comes from `STT_MODEL`.
"""
import io
import math
import wave
from typing import Optional

from openai import AsyncOpenAI, OpenAIError

from backend.config import AppConfig
from backend.core.contracts import STT, STTError
from backend.models import MIC_SAMPLE_RATE, PCM_SAMPLE_WIDTH, Transcript
from backend.utils.errors import summarize_error
from backend.utils.logging import get_logger

logger = get_logger("stt.openai_compatible")


def pcm16_to_wav(pcm16: bytes, sample_rate: int) -> bytes:
    """Wrap mono Int16 PCM in an in-memory WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(PCM_SAMPLE_WIDTH)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm16)
    return buffer.getvalue()


def _segment_values(response: object, name: str) -> list[float]:
    """The numbers named `name` of the response's segments. A response without
    segments, or segments without a finite number there (missing, NaN, infinite, a
    boolean), give none; a segment is an object or a dict, depending on the server."""
    values: list[float] = []
    for segment in getattr(response, "segments", None) or []:
        value = segment.get(name) if isinstance(segment, dict) else getattr(segment, name, None)
        is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
        if is_number and math.isfinite(value):
            values.append(float(value))
    return values


def _confidence(response: object) -> Optional[float]:
    """0–1 from the mean log-probability of the segments; None without segments."""
    logprobs = _segment_values(response, "avg_logprob")
    if not logprobs:
        return None
    return min(max(math.exp(sum(logprobs) / len(logprobs)), 0.0), 1.0)


class OpenAICompatibleSTT(STT):
    def __init__(self, config: AppConfig, *, base_url: str, api_key: str) -> None:
        self.config = config
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=config.STT_TIMEOUT_SEC,
            max_retries=config.GROQ_MAX_RETRIES,
        )

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> Transcript:
        if sample_rate != MIC_SAMPLE_RATE:
            raise ValueError(
                f"Unsupported sample rate {sample_rate}; expected {MIC_SAMPLE_RATE}"
            )

        language = self.config.STT_LANGUAGE
        # All-zero audio is digital silence: skip the call (Whisper hallucinates on it).
        if pcm16.count(0) == len(pcm16):
            return Transcript(text="", language=language)

        try:
            response = await self._client.audio.transcriptions.create(
                model=self.config.STT_MODEL,
                file=("audio.wav", pcm16_to_wav(pcm16, sample_rate), "audio/wav"),
                language=language,
                # The segments carry what the confidence is derived from.
                response_format="verbose_json",
            )
        except OpenAIError as e:
            summary = summarize_error(e, self.config.ERROR_SUMMARY_MAX_CHARS)
            raise STTError(f"STT request failed: {summary}") from e

        text = (response.text or "").strip()
        confidence = _confidence(response)
        no_speech = _segment_values(response, "no_speech_prob")
        # The lowest value: above the threshold, every segment is.
        no_speech_prob = min(no_speech) if no_speech else None
        if no_speech_prob is not None and no_speech_prob > self.config.STT_NO_SPEECH_MAX_PROB:
            logger.info({
                "msg": "Utterance refused, not speech",
                "no_speech_prob": round(no_speech_prob, 3),
                "chars": len(text),
            })
            text = ""
        logger.debug({"msg": "Transcribed", "chars": len(text)})
        return Transcript(
            text=text, language=language, confidence=confidence, no_speech_prob=no_speech_prob
        )
