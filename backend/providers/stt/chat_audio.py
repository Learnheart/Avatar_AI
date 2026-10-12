"""
STT backend for a multimodal chat model — the utterance is sent as an audio file part of
a chat completion and the model is asked to transcribe it.

Serves the endpoints that have no Whisper-style transcription API but a model that hears,
such as Gemini behind the Databricks AI Gateway: the endpoint and the model are passed in
by `backend/providers/catalog.py`.
"""
import base64
from typing import Awaitable, Callable, Union

from openai import AsyncOpenAI, OpenAIError

from backend.config import AppConfig
from backend.core.contracts import STT, STTError
from backend.models import MIC_SAMPLE_RATE, Transcript
from backend.providers.stt.openai_compatible import pcm16_to_wav
from backend.utils.errors import summarize_error
from backend.utils.logging import get_logger

logger = get_logger("stt.chat_audio")


def _text_of(content: object) -> str:
    """The reply text: a plain string, or a list of parts of which the text ones count."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text") or "" for part in content if isinstance(part, dict)
        )
    return ""


class ChatAudioSTT(STT):
    def __init__(
        self,
        config: AppConfig,
        *,
        base_url: str,
        api_key: Union[str, Callable[[], Awaitable[str]]],
        model: str,
    ) -> None:
        self.config = config
        self._model = model
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
        # All-zero audio is digital silence: skip the call (the model would invent words).
        if pcm16.count(0) == len(pcm16):
            return Transcript(text="", language=language)

        wav = base64.b64encode(pcm16_to_wav(pcm16, sample_rate)).decode("ascii")
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": self.config.STT_TRANSCRIBE_PROMPT},
                            {
                                "type": "file",
                                "file": {
                                    "file_data": f"data:audio/wav;base64,{wav}",
                                    "filename": "audio.wav",
                                },
                            },
                        ],
                    }
                ],
                max_completion_tokens=self.config.STT_MAX_TOKENS,
                # Transcribing needs no thinking; it only adds latency.
                reasoning_effort=self.config.STT_REASONING_EFFORT,
            )
        except OpenAIError as e:
            summary = summarize_error(e, self.config.ERROR_SUMMARY_MAX_CHARS)
            raise STTError(f"STT request failed: {summary}") from e

        text = _text_of(response.choices[0].message.content if response.choices else "").strip()
        logger.debug({"msg": "Transcribed", "chars": len(text)})
        return Transcript(text=text, language=language)
