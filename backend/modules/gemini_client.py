"""
Gemini Live API Client — Manages bidirectional streaming session.

Session lifecycle:
  start_session() → send_audio() / send_image() → on_response(callback) → close_session()

Internally runs 2 concurrent tasks inside the Live session:
  - _send_loop: reads from _send_queue, streams to Gemini
  - _receive_loop: reads from Gemini, dispatches to callback

Callback signature: async def callback(kind: str, data: bytes|str, mime_type: str|None)
  kind = "audio" | "text" | "turn_complete"
"""
import asyncio
import base64
from typing import Awaitable, Callable, Optional

from google import genai
from google.genai import types

from backend.config import AppConfig
from backend.utils.logging import get_logger

logger = get_logger("gemini_client")

ResponseCallback = Callable[[str, object, Optional[str]], Awaitable[None]]


class GeminiClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._client = genai.Client(api_key=config.GEMINI_API_KEY)
        self._session_task: Optional[asyncio.Task] = None
        self._send_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._shutdown_event = asyncio.Event()
        self._response_callback: Optional[ResponseCallback] = None
        self._session_ready = asyncio.Event()

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return (
            self._session_task is not None
            and not self._session_task.done()
        )

    def on_response(self, callback: ResponseCallback) -> None:
        """Register async callback for Gemini responses."""
        self._response_callback = callback

    async def start_session(self, system_prompt: str) -> None:
        """Open a new Gemini Live session. No-op if already active."""
        if self.is_active:
            logger.debug({"msg": "Session already active, skipping start"})
            return

        self._shutdown_event.clear()
        self._session_ready.clear()

        # Clear old queue items
        while not self._send_queue.empty():
            try:
                self._send_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        self._session_task = asyncio.create_task(
            self._run_session(system_prompt),
            name="gemini-session",
        )
        logger.info({"msg": "Gemini session starting"})

    async def send_audio(self, pcm_bytes: bytes) -> None:
        """Queue PCM 16kHz mono Int16 audio for sending to Gemini."""
        if not self.is_active:
            return
        try:
            self._send_queue.put_nowait(("audio", pcm_bytes))
        except asyncio.QueueFull:
            logger.debug({"msg": "Send queue full, dropping audio chunk"})

    async def send_image(self, jpeg_bytes: bytes) -> None:
        """Queue JPEG snapshot for Gemini visual context."""
        if not self.is_active:
            return
        try:
            self._send_queue.put_nowait(("image", jpeg_bytes))
        except asyncio.QueueFull:
            pass

    async def close_session(self) -> None:
        """Gracefully close the Gemini Live session."""
        if not self.is_active:
            return
        self._shutdown_event.set()
        try:
            await asyncio.wait_for(self._session_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._session_task.cancel()
        logger.info({"msg": "Gemini session closed"})

    # ── Session runner ────────────────────────────────────────────────────

    async def _run_session(self, system_prompt: str) -> None:
        live_config = self._build_config(system_prompt)

        try:
            async with self._client.aio.live.connect(
                model=self.config.GEMINI_MODEL,
                config=live_config,
            ) as session:
                self._session_ready.set()
                logger.info({"msg": "Gemini Live session connected"})

                await asyncio.gather(
                    self._send_loop(session),
                    self._receive_loop(session),
                    return_exceptions=True,
                )
        except Exception as e:
            logger.error({"msg": "Gemini session error", "error": str(e)})
        finally:
            self._session_ready.clear()

    def _build_config(self, system_prompt: str) -> types.LiveConnectConfig:
        # TTS_MODE determines whether Gemini returns audio or text
        if self.config.TTS_MODE == "gemini":
            response_modalities = ["AUDIO"]
        else:
            response_modalities = ["TEXT"]

        kwargs: dict = dict(
            response_modalities=response_modalities,
            system_instruction=system_prompt,
        )

        if self.config.TTS_MODE == "gemini":
            kwargs["speech_config"] = types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.config.GEMINI_VOICE
                    )
                )
            )

        return types.LiveConnectConfig(**kwargs)

    # ── Send loop ─────────────────────────────────────────────────────────

    async def _send_loop(self, session) -> None:
        while not self._shutdown_event.is_set():
            try:
                item = await asyncio.wait_for(
                    self._send_queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            kind, data = item
            try:
                if kind == "audio":
                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=data,
                            mime_type="audio/pcm;rate=16000",
                        )
                    )
                elif kind == "image":
                    await session.send_realtime_input(
                        video=types.Blob(
                            data=data,
                            mime_type="image/jpeg",
                        )
                    )
            except Exception as e:
                logger.error({"msg": "Send loop error", "kind": kind, "error": str(e)})
                break

    # ── Receive loop ──────────────────────────────────────────────────────

    async def _receive_loop(self, session) -> None:
        try:
            async for response in session.receive():
                if self._shutdown_event.is_set():
                    break
                await self._dispatch_response(response)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error({"msg": "Receive loop error", "error": str(e)})

    async def _dispatch_response(self, response) -> None:
        if self._response_callback is None:
            return

        try:
            # Primary path: server_content with model_turn parts
            server_content = getattr(response, "server_content", None)
            if server_content:
                model_turn = getattr(server_content, "model_turn", None)
                if model_turn:
                    for part in getattr(model_turn, "parts", []):
                        inline_data = getattr(part, "inline_data", None)
                        if inline_data and inline_data.data:
                            await self._response_callback(
                                "audio",
                                inline_data.data,
                                getattr(inline_data, "mime_type", "audio/pcm;rate=24000"),
                            )
                        text = getattr(part, "text", None)
                        if text:
                            await self._response_callback("text", text, None)

                turn_complete = getattr(server_content, "turn_complete", False)
                if turn_complete:
                    await self._response_callback("turn_complete", None, None)
                return

            # Fallback: flat response.data / response.text
            data = getattr(response, "data", None)
            if data:
                await self._response_callback("audio", data, "audio/pcm;rate=24000")
                return

            text = getattr(response, "text", None)
            if text:
                await self._response_callback("text", text, None)

        except Exception as e:
            logger.error({"msg": "Dispatch response error", "error": str(e)})
