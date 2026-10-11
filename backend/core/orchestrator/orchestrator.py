"""
Orchestrator — the state machine that coordinates the whole backend.

State machine (design.md §2):
  idle → listening → thinking → speaking → idle

This is the ONLY place that changes the avatar state (through _transition).
Receives events from: Camera/HeadTracker, VAD, ConversationEngine, WebSocket.
Sends messages through: WSManager → frontend.
"""
import asyncio
import base64
import binascii
import json
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing
from typing import Awaitable, Optional

from fastapi import WebSocket

from backend.config import AppConfig
from backend.core.conversation.engine import ConversationEngine
from backend.core.contracts import STTError
from backend.models import (
    MIC_SAMPLE_RATE,
    PCM_SAMPLE_WIDTH,
    AudioEvent,
    AvatarState,
    HeadRotation,
    TrackingResult,
    TranscriptEvent,
    VADEvent,
)
from backend.core.orchestrator.state import can_transition
from backend.core.orchestrator.ws_manager import (
    WSManager,
    error_message,
    interrupt_message,
    speech_message,
    state_message,
    tracking_message,
)
from backend.core.perception.camera import Camera
from backend.core.perception.tracking import HeadTracker
from backend.core.perception.vad import VADModule
from backend.utils.errors import summarize_error
from backend.utils.logging import get_logger

logger = get_logger("orchestrator")

_MIC_BYTES_PER_SEC = MIC_SAMPLE_RATE * PCM_SAMPLE_WIDTH


class Orchestrator:
    def __init__(
        self,
        config: AppConfig,
        vad: VADModule,
        engine: ConversationEngine,
        camera: Camera,
        tracker: HeadTracker,
    ) -> None:
        self.config = config
        self.ws_manager = WSManager(config)

        self._vad = vad
        self._engine = engine
        self._camera = camera
        self._tracker = tracker

        # Blocking inference never runs on the event loop: one thread per model.
        self._vad_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vad")
        self._vision_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vision")

        self._state = AvatarState.IDLE
        self._tasks: list[asyncio.Task] = []
        self._turn_task: Optional[asyncio.Task] = None
        self._playback_ended = asyncio.Event()
        # The audio worker (one chunk, barge-in included) and a reset never interleave, so
        # a reset always has the last word on the state.
        self._turn_lock = asyncio.Lock()

        # Audio: mic chunks queued by the WS loop, consumed by the audio worker
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._preroll: deque[bytes] = deque()
        self._preroll_bytes = 0
        self._utterance = bytearray()

        self._last_tracking = TrackingResult()
        self._last_rotation = HeadRotation()
        self._last_face_at: Optional[float] = None

    @property
    def state(self) -> AvatarState:
        return self._state

    @property
    def camera_connected(self) -> bool:
        return self._camera.connected

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        logger.info({"msg": "Orchestrator starting"})
        loop = asyncio.get_running_loop()
        await asyncio.gather(
            loop.run_in_executor(self._vad_executor, self._vad.load),
            loop.run_in_executor(self._vision_executor, self._tracker.load),
            asyncio.to_thread(self._engine.load),
        )
        self._camera.start()

        self._tasks = [
            asyncio.create_task(self._audio_worker(), name="audio-worker"),
            asyncio.create_task(self._tracking_loop(), name="tracking"),
        ]
        logger.info({"msg": "Orchestrator started"})

    async def stop(self) -> None:
        logger.info({"msg": "Orchestrator stopping"})
        await self._cancel_turn()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

        loop = asyncio.get_running_loop()
        await self._release("camera", asyncio.to_thread(self._camera.stop))
        await self._release(
            "tracker", loop.run_in_executor(self._vision_executor, self._tracker.close)
        )
        await self._release("engine", asyncio.to_thread(self._engine.close))
        self._vad_executor.shutdown(wait=False)
        self._vision_executor.shutdown(wait=False)
        logger.info({"msg": "Orchestrator stopped"})

    async def _release(self, resource: str, release: Awaitable[None]) -> None:
        """Run one shutdown step; a failure is logged so the remaining steps still run."""
        try:
            await release
        except Exception as e:
            logger.error(
                {
                    "msg": "Release failed",
                    "resource": resource,
                    "error": summarize_error(e, self.config.ERROR_SUMMARY_MAX_CHARS),
                },
                exc_info=True,
            )

    # ── WebSocket: connection + incoming messages ─────────────────────────

    async def handle_connect(self, websocket: WebSocket) -> str:
        """The previous socket is detached before the reset, so nothing of the old session
        (its `state` messages, its audio still in flight) leaks into the new one. The reset
        therefore reaches no client, so the new one is told the current state itself."""
        await self.ws_manager.detach()
        await self._reset_turn()
        session_id = await self.ws_manager.connect(websocket)
        await self.ws_manager.send(state_message(self._state))
        return session_id

    async def handle_disconnect(self, websocket: WebSocket) -> None:
        """Losing the active socket cancels the running turn and resets the state to idle.
        A failed send may have dropped the socket already; the late disconnect of a
        replaced socket leaves the newer connection's turn alone."""
        self.ws_manager.disconnect(websocket)
        if not self.ws_manager.is_connected:
            await self._reset_turn()

    async def handle_client_message(self, raw: str) -> None:
        """Route one JSON text frame. Bad input gets an `error` reply; the socket stays open."""
        try:
            data = json.loads(raw)
        except ValueError:
            await self._send_error("bad_message", "Message is not valid JSON")
            return
        if not isinstance(data, dict):
            await self._send_error("bad_message", "Message must be a JSON object")
            return

        msg_type = data.get("type")
        if msg_type == "audio_chunk":
            try:
                pcm = base64.b64decode(data.get("audio_data") or "", validate=True)
            except (binascii.Error, TypeError, ValueError):
                await self._send_error("bad_message", "audio_data is not valid base64")
                return
            # Half a sample would shift every later sample of the utterance by one byte.
            if pcm and len(pcm) % PCM_SAMPLE_WIDTH == 0:
                self._audio_queue.put_nowait(pcm)
            else:
                logger.debug({"msg": "Audio chunk dropped", "bytes": len(pcm)})
        elif msg_type == "playback_ended":
            if self._state == AvatarState.SPEAKING:
                self._playback_ended.set()
        else:
            await self._send_error("unknown_message", f"Unknown message type: {msg_type!r}")

    # ── Audio pipeline ────────────────────────────────────────────────────

    async def _audio_worker(self) -> None:
        """Consume mic chunks one at a time."""
        while True:
            pcm = await self._audio_queue.get()
            try:
                async with self._turn_lock:
                    # A chunk that waited out a disconnect belongs to a client that is gone.
                    if self.ws_manager.is_connected:
                        await self._process_chunk(pcm)
            except Exception as e:
                logger.warning({"msg": "Audio worker error", "error": str(e)})

    async def _process_chunk(self, pcm: bytes) -> None:
        """VAD off the loop → buffer → state transitions."""
        events = await asyncio.get_running_loop().run_in_executor(
            self._vad_executor, self._vad.process_chunk, pcm
        )
        self._buffer_audio(pcm)
        for event in events:
            if event == VADEvent.SPEECH_START:
                await self._on_speech_start()
            else:
                await self._on_speech_end()

    def _buffer_audio(self, pcm: bytes) -> None:
        """While listening, grow the utterance; otherwise keep the last VAD_PREROLL_MS."""
        if self._state == AvatarState.LISTENING:
            room = int(self.config.STT_MAX_UTTERANCE_SEC * _MIC_BYTES_PER_SEC) - len(self._utterance)
            self._utterance += pcm[:max(room, 0)]
            return

        self._preroll.append(pcm)
        self._preroll_bytes += len(pcm)
        limit = self.config.VAD_PREROLL_MS * _MIC_BYTES_PER_SEC // 1000
        while self._preroll and self._preroll_bytes - len(self._preroll[0]) >= limit:
            self._preroll_bytes -= len(self._preroll.popleft())

    async def _on_speech_start(self) -> None:
        if self._state == AvatarState.LISTENING:
            return

        # Barge-in: the customer speaks while the avatar is thinking or speaking.
        if self._state in (AvatarState.THINKING, AvatarState.SPEAKING):
            logger.info({"msg": "Barge-in detected, cancelling turn", "state": self._state.value})
            await self._cancel_turn()
            await self.ws_manager.send(interrupt_message())

        # The utterance starts from the pre-roll, so the first syllable is not clipped.
        self._utterance = bytearray(b"".join(self._preroll))
        self._clear_preroll()
        await self._transition(AvatarState.LISTENING)

    async def _on_speech_end(self) -> None:
        if self._state != AvatarState.LISTENING:
            return

        pcm = bytes(self._utterance)
        self._utterance.clear()
        await self._transition(AvatarState.THINKING)
        self._turn_task = asyncio.create_task(self._run_turn(pcm), name="turn")

    def _clear_preroll(self) -> None:
        self._preroll.clear()
        self._preroll_bytes = 0

    # ── Turn: STT → LLM → TTS → client ────────────────────────────────────

    async def _run_turn(self, pcm: bytes) -> None:
        """One turn under TURN_TIMEOUT_SEC. Any failure → `error` message + idle."""
        try:
            playback_end = await asyncio.wait_for(
                self._respond(pcm), timeout=self.config.TURN_TIMEOUT_SEC
            )
            if playback_end is not None:
                await self._wait_for_playback(playback_end)
            await self._transition(AvatarState.IDLE)
        except asyncio.TimeoutError:
            logger.error({"msg": "Turn timed out", "timeout_sec": self.config.TURN_TIMEOUT_SEC})
            await self._fail_turn("turn_timeout", "The turn took too long")
        except Exception as e:
            # A provider failure is expected and already summarized; its traceback would
            # print the chained provider response in full.
            logger.error(
                {
                    "msg": "Turn failed",
                    "error": summarize_error(e, self.config.ERROR_SUMMARY_MAX_CHARS),
                },
                exc_info=not isinstance(e, STTError),
            )
            await self._fail_turn("turn_failed", "The turn could not be completed")

    async def _respond(self, pcm: bytes) -> Optional[float]:
        """
        Stream the engine's events to the client.
        Returns the loop time at which the sent audio is estimated to finish playing,
        or None if no audio was sent.
        """
        loop = asyncio.get_running_loop()
        started = loop.time()
        first_audio_at: Optional[float] = None
        audio_sec = 0.0
        sample_rate = 0
        replied = False

        async with aclosing(self._engine.respond(pcm, MIC_SAMPLE_RATE)) as events:
            async for event in events:
                if isinstance(event, TranscriptEvent):
                    logger.info({
                        "msg": "Transcript",
                        "chars": len(event.text),
                        "stt_ms": round((loop.time() - started) * 1000),
                    })
                elif isinstance(event, AudioEvent):
                    replied = True
                    sample_rate = event.sample_rate
                    if event.pcm and first_audio_at is None:
                        first_audio_at = loop.time()
                        logger.info({
                            "msg": "First audio",
                            "first_audio_ms": round((first_audio_at - started) * 1000),
                        })
                        await self._transition(AvatarState.SPEAKING)
                    audio_sec += len(event.pcm) / PCM_SAMPLE_WIDTH / event.sample_rate
                    await self._send_speech(event.text, event.pcm, sample_rate)

        if replied:
            # Cleared before the final message: only a `playback_ended` answering it counts.
            self._playback_ended.clear()
            await self._send_final(sample_rate)
        logger.info({
            "msg": "Turn complete",
            "audio_sec": round(audio_sec, 2),
            "turn_ms": round((loop.time() - started) * 1000),
        })
        return None if first_audio_at is None else first_audio_at + audio_sec

    async def _wait_for_playback(self, playback_end: float) -> None:
        """Stay in `speaking` until the client reports the queue drained, or a grace period
        after the estimated end of the audio (TTS synthesizes faster than real time)."""
        remaining = max(playback_end - asyncio.get_running_loop().time(), 0.0)
        try:
            await asyncio.wait_for(
                self._playback_ended.wait(),
                timeout=remaining + self.config.PLAYBACK_END_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.warning({"msg": "No playback_ended from client, leaving speaking"})

    async def _send_speech(self, text: str, pcm: bytes, sample_rate: int) -> None:
        await self.ws_manager.send(speech_message(text, pcm, sample_rate, is_final=False))

    async def _send_final(self, sample_rate: int) -> None:
        """Tell the client the answer is complete; it replies `playback_ended`."""
        await self.ws_manager.send(speech_message("", b"", sample_rate, is_final=True))

    async def _fail_turn(self, code: str, message: str) -> None:
        if self._state == AvatarState.SPEAKING:
            # Audio already reached the client: stop it, or it keeps playing while idle.
            await self.ws_manager.send(interrupt_message())
        await self._send_error(code, message)
        await self._transition(AvatarState.IDLE)

    async def _send_error(self, code: str, message: str) -> None:
        await self.ws_manager.send(error_message(code, message))

    async def _cancel_turn(self) -> None:
        task, self._turn_task = self._turn_task, None
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _reset_turn(self) -> None:
        """Drop the running turn and all buffered audio; back to idle."""
        async with self._turn_lock:
            await self._cancel_turn()
            while not self._audio_queue.empty():
                self._audio_queue.get_nowait()
            self._utterance.clear()
            self._clear_preroll()
            await asyncio.get_running_loop().run_in_executor(
                self._vad_executor, self._vad.reset
            )
            await self._transition(AvatarState.IDLE)

    # ── Tracking (parallel plane) ─────────────────────────────────────────

    async def _tracking_loop(self) -> None:
        """Send tracking_update at TRACKING_FPS, regardless of the turn state."""
        loop = asyncio.get_running_loop()
        interval = 1.0 / self.config.TRACKING_FPS
        while True:
            tick = loop.time()
            if self.ws_manager.is_connected:
                try:
                    tracking = await self._read_tracking()
                    await self.ws_manager.send(tracking_message(
                        present=tracking.present,
                        head_rotation=self._head_rotation(tracking),
                        is_speaking=self._state == AvatarState.SPEAKING,
                    ))
                except Exception as e:
                    logger.warning({"msg": "Tracking loop error", "error": str(e)})
            await asyncio.sleep(max(interval - (loop.time() - tick), 0.0))

    async def _read_tracking(self) -> TrackingResult:
        """Track the newest camera frame off the loop. No camera → no face."""
        frame = self._camera.take_frame()
        if frame is not None:
            self._last_tracking = await asyncio.get_running_loop().run_in_executor(
                self._vision_executor, self._tracker.process, frame
            )
        elif not self._camera.connected:
            self._last_tracking = TrackingResult()
        return self._last_tracking

    def _head_rotation(self, tracking: TrackingResult) -> HeadRotation:
        """Last-known rotation while the face is briefly lost; neutral after
        NO_FACE_TIMEOUT_SEC without a face."""
        now = time.monotonic()
        if tracking.present:
            self._last_face_at = now
            if tracking.head_rotation is not None:
                self._last_rotation = tracking.head_rotation
        elif (
            self._last_face_at is None
            or now - self._last_face_at > self.config.NO_FACE_TIMEOUT_SEC
        ):
            self._last_rotation = HeadRotation()
        return self._last_rotation

    # ── State machine ─────────────────────────────────────────────────────

    async def _transition(self, new_state: AvatarState) -> None:
        old = self._state
        if old == new_state:
            return
        if not can_transition(old, new_state):
            logger.error({"msg": f"Illegal transition refused: {old.value} → {new_state.value}"})
            return

        self._state = new_state
        logger.info({"msg": f"State: {old.value} → {new_state.value}"})
        await self.ws_manager.send(state_message(new_state))
