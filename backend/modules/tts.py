"""
TTS Abstraction Layer.

TTS_MODE = "gemini":  Audio đến trực tiếp từ Gemini Live response → pass-through.
TTS_MODE = "azure":   Gemini trả TEXT → gọi Azure Cognitive Speech TTS → audio.
TTS_MODE = "viettel": Gemini trả TEXT → gọi Viettel AI TTS REST API → audio.

process_output() nhận (text, audio_bytes, mime_type) từ GeminiClient callback
và trả về SpeechOutput để orchestrator gửi qua WebSocket.
"""
import asyncio
import base64
from typing import Optional

import httpx

from backend.config import AppConfig
from backend.models import AudioSource, SpeechOutput
from backend.utils.logging import get_logger

logger = get_logger("tts")


class TTSModule:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._http_client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        if self.config.TTS_MODE in ("azure", "viettel"):
            self._http_client = httpx.AsyncClient(timeout=10.0)
        logger.info({"msg": "TTS module started", "mode": self.config.TTS_MODE})

    async def stop(self) -> None:
        if self._http_client:
            await self._http_client.aclose()

    async def process_output(
        self,
        text: str,
        audio_bytes: Optional[bytes],
        mime_type: Optional[str],
    ) -> Optional[SpeechOutput]:
        """
        Convert Gemini output to SpeechOutput.

        - gemini mode: audio_bytes is already the final audio.
        - azure/viettel mode: send text to TTS provider, get audio back.
        """
        mode = self.config.TTS_MODE

        if mode == "gemini":
            if audio_bytes is None:
                return None
            return SpeechOutput(
                text_content=text,
                audio_source=AudioSource.GEMINI,
                audio_data=audio_bytes,
                audio_mime_type=mime_type or "audio/pcm;rate=24000",
                is_final=True,
            )

        if mode == "azure":
            return await self._azure_tts(text)

        if mode == "viettel":
            return await self._viettel_tts(text)

        logger.error({"msg": "Unknown TTS mode", "mode": mode})
        return None

    # ── Azure TTS ─────────────────────────────────────────────────────────

    async def _azure_tts(self, text: str) -> Optional[SpeechOutput]:
        """Call Azure Cognitive Services Speech REST API."""
        if not self._http_client:
            return None

        token_url = (
            f"https://{self.config.AZURE_TTS_REGION}"
            ".api.cognitive.microsoft.com/sts/v1.0/issuetoken"
        )
        tts_url = (
            f"https://{self.config.AZURE_TTS_REGION}"
            ".tts.speech.microsoft.com/cognitiveservices/v1"
        )

        try:
            # Get access token
            token_resp = await self._http_client.post(
                token_url,
                headers={"Ocp-Apim-Subscription-Key": self.config.AZURE_TTS_KEY},
            )
            token_resp.raise_for_status()
            token = token_resp.text

            # SSML payload
            ssml = (
                "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='vi-VN'>"
                f"<voice name='{self.config.AZURE_TTS_VOICE}'>{text}</voice>"
                "</speak>"
            )

            audio_resp = await self._http_client.post(
                tts_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "audio-16khz-32kbitrate-mono-mp3",
                },
                content=ssml.encode("utf-8"),
            )
            audio_resp.raise_for_status()

            logger.info({"msg": "Azure TTS success", "text_len": len(text)})
            return SpeechOutput(
                text_content=text,
                audio_source=AudioSource.AZURE,
                audio_data=audio_resp.content,
                audio_mime_type="audio/mpeg",
                is_final=True,
            )
        except Exception as e:
            logger.error({"msg": "Azure TTS error", "error": str(e)})
            return None

    # ── Viettel TTS ───────────────────────────────────────────────────────

    async def _viettel_tts(self, text: str) -> Optional[SpeechOutput]:
        """Call Viettel AI TTS REST API."""
        if not self._http_client or not self.config.VIETTEL_TTS_URL:
            logger.error({"msg": "Viettel TTS not configured"})
            return None

        try:
            resp = await self._http_client.post(
                self.config.VIETTEL_TTS_URL,
                json={"text": text, "voice": "hn-female-quynhanh"},
                headers={"Token": self.config.VIETTEL_TTS_KEY},
            )
            resp.raise_for_status()
            data = resp.json()
            audio_b64: str = data.get("async", "") or data.get("data", "")
            audio_bytes = base64.b64decode(audio_b64)

            logger.info({"msg": "Viettel TTS success", "text_len": len(text)})
            return SpeechOutput(
                text_content=text,
                audio_source=AudioSource.VIETTEL,
                audio_data=audio_bytes,
                audio_mime_type="audio/mp3",
                is_final=True,
            )
        except Exception as e:
            logger.error({"msg": "Viettel TTS error", "error": str(e)})
            return None
