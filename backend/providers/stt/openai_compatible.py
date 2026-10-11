"""
STT backend for any OpenAI-compatible transcription API (Whisper).

Serves hosted APIs (Groq, ...) and self-hosted Whisper servers: the endpoint is passed in
by `backend/providers/catalog.py`, the model comes from `STT_MODEL`.
"""
import io
import wave

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
            )
        except OpenAIError as e:
            summary = summarize_error(e, self.config.ERROR_SUMMARY_MAX_CHARS)
            raise STTError(f"STT request failed: {summary}") from e

        text = (response.text or "").strip()
        logger.debug({"msg": "Transcribed", "chars": len(text)})
        return Transcript(text=text, language=language)
