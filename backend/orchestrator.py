"""
Orchestrator — State machine và điều phối toàn bộ backend.

State machine (BR-004):
  IDLE → TRACKING → LISTENING → SPEAKING → TRACKING

Đây là NƠI DUY NHẤT thay đổi avatar_state.
Nhận events từ: Vision, VAD, GeminiClient, WebSocket
Gửi messages qua: WSManager → Frontend
"""
import asyncio
import base64
import time
from typing import Optional

from backend.config import AppConfig
from backend.models import AvatarState, HeadRotation, SessionState, SpeechOutput
from backend.modules.gemini_client import GeminiClient
from backend.modules.tts import TTSModule
from backend.modules.vad import VADModule
from backend.modules.vision import VisionModule
from backend.utils.logging import get_logger

logger = get_logger("orchestrator")


class WSManager:
    """Manages the single active WebSocket connection."""

    def __init__(self) -> None:
        self._ws = None

    async def connect(self, websocket) -> str:
        await websocket.accept()
        self._ws = websocket
        state_info = {
            "type": "connected",
            "session_id": "session-" + str(int(time.time())),
            "config": {
                "tracking_fps": 30,
                "audio_format": "pcm_16khz_mono",
            },
        }
        await self.send(state_info)
        return state_info["session_id"]

    async def disconnect(self) -> None:
        self._ws = None

    async def send(self, message: dict) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send_json(message)
        except Exception as e:
            logger.warning({"msg": "WS send failed", "error": str(e)})
            self._ws = None

    @property
    def is_connected(self) -> bool:
        return self._ws is not None


class Orchestrator:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.ws_manager = WSManager()
        self.session_state = SessionState()

        # Modules
        self._vision = VisionModule(config)
        self._vad = VADModule(config)
        self._gemini = GeminiClient(config)
        self._tts = TTSModule(config)

        # Internal state
        self._tasks: list[asyncio.Task] = []
        self._accumulated_text = ""   # Collect text across response turns
        self._audio_buffer: list[bytes] = []  # Accumulate audio chunks
        self._last_speech_activity = 0.0

        # EC-005: barge-in flag
        self._barge_in_pending = False

    @property
    def camera_connected(self) -> bool:
        return self._vision._thread is not None and self._vision._thread.is_alive()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        logger.info({"msg": "Orchestrator starting"})
        self._vad.load()
        await self._tts.start()
        await self._vision.start()

        self._gemini.on_response(self._on_gemini_response)

        self._tasks = [
            asyncio.create_task(self._tracking_loop(), name="tracking"),
            asyncio.create_task(self._timeout_monitor(), name="timeout-monitor"),
            asyncio.create_task(self._snapshot_loop(), name="snapshot"),
        ]
        logger.info({"msg": "Orchestrator started"})

    async def stop(self) -> None:
        logger.info({"msg": "Orchestrator stopping"})
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

        await self._gemini.close_session()
        await self._vision.stop()
        await self._tts.stop()
        logger.info({"msg": "Orchestrator stopped"})

    # ── Handle incoming messages from frontend ────────────────────────────

    async def handle_client_message(self, data: dict) -> None:
        msg_type = data.get("type")

        if msg_type == "audio_chunk":
            audio_b64: str = data.get("audio_data", "")
            if audio_b64:
                pcm_bytes = base64.b64decode(audio_b64)
                await self._process_audio(pcm_bytes)

    # ── Audio pipeline ────────────────────────────────────────────────────

    async def _process_audio(self, pcm_bytes: bytes) -> None:
        """Run PCM through VAD → state transitions → Gemini stream."""
        state = self.session_state.avatar_state

        # EC-005: barge-in — interrupt if speaking
        if state == AvatarState.SPEAKING:
            vad_event = self._vad.process_audio(pcm_bytes)
            if vad_event and vad_event.event_type == "speech_start":
                await self._handle_barge_in()
                return

        vad_event = self._vad.process_audio(pcm_bytes)

        if vad_event:
            if vad_event.event_type == "speech_start":
                await self._on_speech_start()
            elif vad_event.event_type == "speech_end":
                await self._on_speech_end()

        # Stream audio to Gemini if in LISTENING state
        if self.session_state.avatar_state == AvatarState.LISTENING:
            await self._gemini.send_audio(pcm_bytes)
            self._last_speech_activity = time.time()
            self.session_state.last_speech_activity_at = self._last_speech_activity

    async def _on_speech_start(self) -> None:
        state = self.session_state.avatar_state
        if state in (AvatarState.IDLE, AvatarState.TRACKING):
            await self._transition(AvatarState.LISTENING)

            # Open Gemini session if not already open
            if not self._gemini.is_active:
                self._accumulated_text = ""
                self._audio_buffer.clear()
                self._vad.reset()
                await self._gemini.start_session(self.config.SYSTEM_PROMPT)
                self.session_state.gemini_session_active = True

            self._last_speech_activity = time.time()
            logger.info({"msg": "Speech started → LISTENING"})

    async def _on_speech_end(self) -> None:
        if self.session_state.avatar_state == AvatarState.LISTENING:
            logger.info({"msg": "Speech ended, awaiting Gemini response"})
            # Gemini Live API will respond automatically after silence
            # Keep state as LISTENING until we get audio back

    async def _handle_barge_in(self) -> None:
        """EC-005: Customer speaks while avatar is speaking → interrupt."""
        logger.info({"msg": "Barge-in detected, interrupting playback"})
        self._audio_buffer.clear()

        await self.ws_manager.send({"type": "interrupt"})
        await self._transition(AvatarState.LISTENING)

        if not self._gemini.is_active:
            await self._gemini.start_session(self.config.SYSTEM_PROMPT)
            self.session_state.gemini_session_active = True

    # ── Gemini response handler ───────────────────────────────────────────

    async def _on_gemini_response(
        self, kind: str, data: object, mime_type: Optional[str]
    ) -> None:
        """Called by GeminiClient for each response piece."""

        if kind == "audio":
            self._audio_buffer.append(data)  # type: ignore
            # Stream audio chunk immediately to frontend
            await self._send_audio_chunk(data, mime_type or "audio/pcm;rate=24000")

            if self.session_state.avatar_state != AvatarState.SPEAKING:
                await self._transition(AvatarState.SPEAKING)

        elif kind == "text":
            self._accumulated_text += str(data)

        elif kind == "turn_complete":
            await self._on_turn_complete()

    async def _send_audio_chunk(self, audio_bytes: bytes, mime_type: str) -> None:
        """Send a single audio chunk to frontend as speech_output."""
        await self.ws_manager.send({
            "type": "speech_output",
            "text_content": "",
            "audio_source": self.config.TTS_MODE,
            "audio_data": base64.b64encode(audio_bytes).decode("utf-8"),
            "audio_mime_type": mime_type,
            "is_final": False,
        })

    async def _on_turn_complete(self) -> None:
        """Gemini finished one complete response turn."""
        logger.info({
            "msg": "Gemini turn complete",
            "text": self._accumulated_text[:100],
            "audio_chunks": len(self._audio_buffer),
        })

        if self.config.TTS_MODE != "gemini" and self._accumulated_text:
            # Use external TTS
            speech = await self._tts.process_output(
                self._accumulated_text, None, None
            )
            if speech:
                await self._deliver_speech(speech)
        else:
            # Send final marker
            await self.ws_manager.send({
                "type": "speech_output",
                "text_content": self._accumulated_text,
                "audio_source": self.config.TTS_MODE,
                "audio_data": "",
                "audio_mime_type": "",
                "is_final": True,
            })

        self._accumulated_text = ""
        self._audio_buffer.clear()

        # Back to TRACKING when speaking is done
        await self._transition(AvatarState.TRACKING)

    async def _deliver_speech(self, speech: SpeechOutput) -> None:
        """Send complete speech output (from Azure/Viettel TTS) to frontend."""
        await self.ws_manager.send({
            "type": "speech_output",
            "text_content": speech.text_content,
            "audio_source": speech.audio_source.value,
            "audio_data": base64.b64encode(speech.audio_data).decode("utf-8"),
            "audio_mime_type": speech.audio_mime_type,
            "is_final": True,
        })

    # ── Background tasks ──────────────────────────────────────────────────

    async def _tracking_loop(self) -> None:
        """Continuously read head rotation from Vision and send to frontend."""
        while True:
            try:
                rotation: HeadRotation = await asyncio.wait_for(
                    self._vision.get_rotation(), timeout=0.1
                )
                self.session_state.last_head_rotation = rotation
                now = time.time()

                face_detected = not self._is_zero_rotation(rotation)
                if face_detected:
                    self.session_state.last_face_detected_at = now

                    if self.session_state.avatar_state == AvatarState.IDLE:
                        await self._transition(AvatarState.TRACKING)

                await self.ws_manager.send({
                    "type": "tracking_update",
                    "timestamp": now,
                    "avatar_control": {
                        "head_rotation": rotation.to_dict(),
                        "is_speaking": self.session_state.is_speaking,
                    },
                })

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning({"msg": "Tracking loop error", "error": str(e)})
                await asyncio.sleep(0.033)

    async def _snapshot_loop(self) -> None:
        """Periodically send camera snapshot to Gemini for visual context (FR-001-C)."""
        while True:
            try:
                jpeg = await self._vision.get_snapshot()
                if jpeg and self._gemini.is_active:
                    await self._gemini.send_image(jpeg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning({"msg": "Snapshot loop error", "error": str(e)})
            await asyncio.sleep(self.config.SNAPSHOT_INTERVAL_SEC)

    async def _timeout_monitor(self) -> None:
        """
        Monitor timeouts:
        - BR-004 TRACKING→IDLE: no face for 3s
        - BR-005: no speech for 30s → close Gemini session
        """
        while True:
            try:
                await asyncio.sleep(1.0)
                now = time.time()
                state = self.session_state.avatar_state

                # No-face timeout → IDLE (BR-004)
                if state == AvatarState.TRACKING:
                    last_face = self.session_state.last_face_detected_at
                    if last_face and (now - last_face) > self.config.NO_FACE_TIMEOUT_SEC:
                        await self._transition(AvatarState.IDLE)
                        logger.info({"msg": "No face detected → IDLE"})

                # Gemini idle timeout → close session (BR-005)
                if self._gemini.is_active and self.session_state.last_speech_activity_at:
                    idle_time = now - self.session_state.last_speech_activity_at
                    if idle_time > self.config.GEMINI_IDLE_TIMEOUT_SEC:
                        logger.info({"msg": "Gemini idle timeout, closing session"})
                        await self._gemini.close_session()
                        self.session_state.gemini_session_active = False

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning({"msg": "Timeout monitor error", "error": str(e)})

    # ── State machine ─────────────────────────────────────────────────────

    async def _transition(self, new_state: AvatarState) -> None:
        old = self.session_state.avatar_state
        if old == new_state:
            return

        self.session_state.avatar_state = new_state
        self.session_state.is_speaking = new_state == AvatarState.SPEAKING

        logger.info({"msg": f"State: {old.value} → {new_state.value}"})

        # Notify frontend of speaking state change (BR-007 / BR-008)
        if new_state in (AvatarState.SPEAKING, AvatarState.TRACKING):
            await self.ws_manager.send({
                "type": "tracking_update",
                "timestamp": time.time(),
                "avatar_control": {
                    "head_rotation": self.session_state.last_head_rotation.to_dict(),
                    "is_speaking": self.session_state.is_speaking,
                },
            })

    # ── Helpers ───────────────────────────────────────────────────────────

    def _is_zero_rotation(self, r: HeadRotation) -> bool:
        return abs(r.pitch) < 0.01 and abs(r.yaw) < 0.01 and abs(r.roll) < 0.01
