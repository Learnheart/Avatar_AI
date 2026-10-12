"""
Orchestrator — the state machine that coordinates the whole backend.

State machine (design.md §2):
  idle → listening → thinking → speaking → idle

This is the ONLY place that changes the avatar state (through _transition).
Receives events from: Camera/HeadTracker, VAD, ConversationEngine, WebSocket.
Sends messages through: WSManager → frontend.

It also owns the conversation, the session of one user: it opens at the first utterance,
its turns are recorded here as they were spoken and handed to the engine with each new
utterance, and it closes when the person left, stayed silent, changed, or asked for a
new one.
"""
import asyncio
import base64
import binascii
import json
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing
from typing import Awaitable, Callable, Optional

from fastapi import WebSocket
from opentelemetry import trace

from backend.config import AppConfig
from backend.core.conversation.engine import ConversationEngine
from backend.core.conversation.history import Conversation, TurnRecord
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
    conversation_message,
    error_message,
    interrupt_message,
    speech_message,
    state_message,
    tracking_message,
    transcript_message,
)
from backend.core.perception.camera import Camera
from backend.core.perception.tracking import HeadTracker
from backend.core.perception.vad import VADModule
from backend.utils.errors import summarize_error
from backend.utils.capture import CaptureStore
from backend.utils.logging import get_logger
from backend.utils.telemetry import (
    audio_chunks_dropped,
    frames_tracked,
    perception_duration,
    speech_events,
    stage_duration,
    tracer,
    turns,
)

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
        capture: Optional[CaptureStore] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """With `capture`, every tracked frame, the audio of each turn and the perception
        events are stored on disk. `clock` gives the seconds the conversation boundary
        and the record of what was spoken are measured in."""
        self.config = config
        self._clock = clock
        self.ws_manager = WSManager(config)

        self._vad = vad
        self._engine = engine
        self._camera = camera
        self._tracker = tracker
        self._capture = capture

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
        # Wall-clock time (ns) of the speech_start of the utterance being heard: the
        # start of the turn's trace.
        self._speech_started_ns = 0

        self._last_tracking = TrackingResult()
        self._last_rotation = HeadRotation()
        self._last_face_at: Optional[float] = None
        self._present = False  # whether a person is in front of the camera
        self._session_id = ""  # of the connected client; names the turns of one visit

        # The conversation: the session of one user, open from the first utterance to
        # its close. One at a time, because the backend serves one connection.
        self._user_id: Optional[str] = None  # of the connected client; None = guest
        self._conversation: Optional[Conversation] = None
        # When it was opened or its last turn ended, on `clock`.
        self._conversation_active_at = 0.0
        # Since when no face is seen while the camera is connected; None = not absent.
        self._absent_since: Optional[float] = None
        # What the running turn has heard and spoken so far.
        self._turn_record: Optional[TurnRecord] = None

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

    async def handle_connect(
        self, websocket: WebSocket, user_id: Optional[str] = None
    ) -> str:
        """The previous socket is detached before the reset, so nothing of the old session
        (its `state` messages, its audio still in flight) leaks into the new one. The reset
        therefore reaches no client, so the new one is told the current state itself.
        `user_id` is the person behind the connection, None for a guest: the open
        conversation is kept for the same user and closed for another one."""
        await self.ws_manager.detach()
        await self._reset_turn()
        if self._conversation is not None and self._conversation.user_id != user_id:
            await self._close_conversation("user_changed")
        self._user_id = user_id
        self._session_id = await self.ws_manager.connect(websocket)
        await self.ws_manager.send(state_message(self._state))
        conversation = self._conversation
        await self.ws_manager.send(
            conversation_message(conversation.id if conversation else None)
        )
        return self._session_id

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
                audio_chunks_dropped.add(1)
                logger.debug({"msg": "Audio chunk dropped", "bytes": len(pcm)})
        elif msg_type == "playback_ended":
            if self._state == AvatarState.SPEAKING:
                self._playback_ended.set()
        elif msg_type == "conversation_reset":
            await self._reset_conversation()
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
        started = time.perf_counter()
        events = await asyncio.get_running_loop().run_in_executor(
            self._vad_executor, self._vad.process_chunk, pcm
        )
        perception_duration.record((time.perf_counter() - started) * 1000, {"stage": "vad"})
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
        barge_in = self._state in (AvatarState.THINKING, AvatarState.SPEAKING)
        if barge_in:
            logger.info({"msg": "Barge-in detected, cancelling turn", "state": self._state.value})
            await self._cancel_turn()
            await self.ws_manager.send(interrupt_message())
        self._speech_started_ns = time.time_ns()
        self._perception_event("speech_start", barge_in=barge_in)

        # The utterance starts from the pre-roll, so the first syllable is not clipped.
        self._utterance = bytearray(b"".join(self._preroll))
        self._clear_preroll()
        await self._transition(AvatarState.LISTENING)

    async def _on_speech_end(self) -> None:
        if self._state != AvatarState.LISTENING:
            return

        pcm = bytes(self._utterance)
        self._utterance.clear()
        self._perception_event(
            "speech_end", utterance_ms=round(len(pcm) / _MIC_BYTES_PER_SEC * 1000)
        )
        conversation = await self._open_conversation()
        await self._transition(AvatarState.THINKING)
        self._turn_record = TurnRecord()
        self._turn_task = asyncio.create_task(
            self._run_turn(pcm, self._speech_started_ns, conversation, self._turn_record),
            name="turn",
        )

    def _perception_event(self, event: str, **data: object) -> None:
        """A perception event: counted, logged and, with capture on, stored."""
        if event.startswith("speech_"):
            speech_events.add(1, {"event": event})
        logger.info({"msg": "Perception event", "event": event, **data})
        if self._capture:
            self._capture.event(event, **data)

    def _clear_preroll(self) -> None:
        self._preroll.clear()
        self._preroll_bytes = 0

    # ── Turn: STT → LLM → TTS → client ────────────────────────────────────

    async def _run_turn(
        self, pcm: bytes, speech_started_ns: int, conversation: Conversation, record: TurnRecord
    ) -> None:
        """One turn under TURN_TIMEOUT_SEC. Any failure → `error` message + idle.
        The turn is one trace, starting when the person started to speak. However it
        ends, what was heard and spoken is recorded in its conversation."""
        utterance_ms = round(len(pcm) / _MIC_BYTES_PER_SEC * 1000)
        attributes = {"session.id": self._session_id, "conversation.id": conversation.id}
        if conversation.user_id is not None:
            attributes["user.id"] = conversation.user_id
        with tracer.start_as_current_span(
            "turn", start_time=speech_started_ns, attributes=attributes
        ) as span:
            # The listening phase is over when the turn starts: a span closed at once.
            tracer.start_span(
                "listen", start_time=speech_started_ns, attributes={"utterance.ms": utterance_ms}
            ).end()
            if self._capture:
                trace_id = trace.format_trace_id(span.get_span_context().trace_id)
                self._capture.audio(trace_id, "utterance", pcm, MIC_SAMPLE_RATE)

            outcome = "completed"
            try:
                await self._turn(pcm, conversation, record)
            except asyncio.CancelledError:
                # Barge-in, reset, disconnect or shutdown: always through _cancel_turn,
                # which took the cut before the engine was closed.
                outcome = "cancelled"
                raise
            except asyncio.TimeoutError:
                outcome = "timeout"
                record.cut(self._clock())
                logger.error({"msg": "Turn timed out", "timeout_sec": self.config.TURN_TIMEOUT_SEC})
                await self._fail_turn("turn_timeout", "The turn took too long")
            except Exception as e:
                outcome = "failed"
                record.cut(self._clock())
                span.set_status(trace.StatusCode.ERROR, f"{type(e).__name__}")
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
            finally:
                span.set_attribute("turn.outcome", outcome)
                turns.add(1, {"outcome": outcome})
                self._record_turn(conversation, record)

    def _record_turn(self, conversation: Conversation, record: TurnRecord) -> None:
        """The one place where a turn that ended, however it ended, enters the history
        of its conversation. A turn in which nothing was understood is not recorded and
        does not keep the conversation open: noise is not the person speaking."""
        turn = record.chat_turn()
        if turn is None:
            return
        conversation.append(turn)
        if conversation is self._conversation:
            self._conversation_active_at = self._clock()
        logger.info({
            "msg": "Turn recorded",
            "conversation_id": conversation.id,
            "sentences": len(turn.sentences),
            "interrupted": turn.interrupted,
        })

    async def _turn(self, pcm: bytes, conversation: Conversation, record: TurnRecord) -> None:
        playback_end = await asyncio.wait_for(
            self._respond(pcm, conversation, record), timeout=self.config.TURN_TIMEOUT_SEC
        )
        if playback_end is not None:
            await self._wait_for_playback(playback_end)
        await self._transition(AvatarState.IDLE)

    async def _respond(
        self, pcm: bytes, conversation: Conversation, record: TurnRecord
    ) -> Optional[float]:
        """
        Stream the engine's events to the client, noting in `record` what was heard and
        what was sent to be spoken.
        Returns the loop time at which the sent audio is estimated to finish playing,
        or None if no audio was sent.
        """
        loop = asyncio.get_running_loop()
        started = loop.time()
        first_audio_at: Optional[float] = None
        audio_sec = 0.0
        sample_rate = 0
        replied = False
        span = trace.get_current_span()  # the turn
        answer_pcm = bytearray()  # kept only to be captured

        # The engine keeps no history: it is handed the turns of this user's conversation,
        # and who that user is.
        answer = self._engine.respond(
            pcm, MIC_SAMPLE_RATE, conversation.history, conversation.user_id
        )
        async with aclosing(answer) as events:
            async for event in events:
                if isinstance(event, TranscriptEvent):
                    logger.info({
                        "msg": "Transcript",
                        "chars": len(event.text),
                        "stt_ms": round((loop.time() - started) * 1000),
                    })
                    if event.text.strip():
                        record.heard(event.text, event.confidence)
                        await self.ws_manager.send(transcript_message(event.text))
                elif isinstance(event, AudioEvent):
                    replied = True
                    sample_rate = event.sample_rate
                    if self._capture:
                        answer_pcm += event.pcm
                    if event.pcm and first_audio_at is None:
                        first_audio_at = loop.time()
                        first_audio_ms = (first_audio_at - started) * 1000
                        span.set_attribute("turn.first_audio_ms", round(first_audio_ms))
                        stage_duration.record(first_audio_ms, {"stage": "turn_first_audio"})
                        logger.info({"msg": "First audio", "first_audio_ms": round(first_audio_ms)})
                        await self._transition(AvatarState.SPEAKING)
                    chunk_sec = len(event.pcm) / PCM_SAMPLE_WIDTH / event.sample_rate
                    audio_sec += chunk_sec
                    await self._send_speech(event.text, event.pcm, sample_rate)
                    record.spoke(event.text, chunk_sec, self._clock(), fallback=event.fallback)

        if replied:
            # Cleared before the final message: only a `playback_ended` answering it counts.
            self._playback_ended.clear()
            await self._send_final(sample_rate)
        span.set_attribute("turn.audio_sec", round(audio_sec, 2))
        if self._capture and answer_pcm:
            trace_id = trace.format_trace_id(span.get_span_context().trace_id)
            self._capture.audio(trace_id, "answer", bytes(answer_pcm), sample_rate)
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
        with tracer.start_as_current_span("playback") as span:
            try:
                await asyncio.wait_for(
                    self._playback_ended.wait(),
                    timeout=remaining + self.config.PLAYBACK_END_TIMEOUT_SEC,
                )
                span.set_attribute("playback.ended_by", "client")
            except asyncio.TimeoutError:
                span.set_attribute("playback.ended_by", "timeout")
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
        """Cancel the running turn and wait until it is recorded as interrupted."""
        task, self._turn_task = self._turn_task, None
        if task is None or task.done():
            return
        # The cut is now: closing the engine may take a moment.
        if self._turn_record is not None:
            self._turn_record.cut(self._clock(), interrupted=True)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _reset_turn(self) -> None:
        """Drop the running turn and all buffered audio; back to idle."""
        async with self._turn_lock:
            await self._drop_turn()

    async def _drop_turn(self) -> None:
        """The reset itself. Call with the turn lock held."""
        await self._cancel_turn()
        while not self._audio_queue.empty():
            self._audio_queue.get_nowait()
        self._utterance.clear()
        self._clear_preroll()
        await asyncio.get_running_loop().run_in_executor(
            self._vad_executor, self._vad.reset
        )
        await self._transition(AvatarState.IDLE)

    # ── Conversation: the session of one user ─────────────────────────────

    async def _open_conversation(self) -> Conversation:
        """The open conversation; the first utterance opens one for the connected user."""
        if self._conversation is None:
            self._conversation = Conversation(
                id=str(uuid.uuid4()),
                user_id=self._user_id,
                max_turns=self.config.MEMORY_HISTORY_MAX_TURNS,
            )
            self._conversation_active_at = self._clock()
            logger.info({
                "msg": "Conversation opened",
                "conversation_id": self._conversation.id,
                "user_id": self._user_id,
            })
            await self.ws_manager.send(conversation_message(self._conversation.id))
        return self._conversation

    async def _close_conversation(self, reason: str) -> None:
        """Drop the open conversation and its history, and tell the client."""
        conversation, self._conversation = self._conversation, None
        if conversation is None:
            return
        logger.info({
            "msg": "Conversation closed",
            "conversation_id": conversation.id,
            "user_id": conversation.user_id,
            "reason": reason,
            "turns": len(conversation.history),
        })
        await self.ws_manager.send(conversation_message(None))

    async def _reset_conversation(self) -> None:
        """The person asked for a new conversation. A running turn is cancelled as by a
        barge-in and recorded as interrupted, an utterance being heard is dropped, then
        the conversation closes. With none open, nothing happens."""
        async with self._turn_lock:
            if self._conversation is None:
                return
            answering = self._state in (AvatarState.THINKING, AvatarState.SPEAKING)
            # Cancelled here, ahead of the reset below, so that `interrupt` goes out
            # after the last audio of the turn and before `state: idle`.
            await self._cancel_turn()
            if answering:
                await self.ws_manager.send(interrupt_message())
            await self._drop_turn()
            await self._close_conversation("reset")

    def _turn_running(self) -> bool:
        return self._turn_task is not None and not self._turn_task.done()

    async def _check_conversation(self) -> None:
        """Close the conversation when the person left or stayed silent. Never while a
        turn runs or an utterance is being heard."""
        now = self._clock()
        # Absence is only known while the camera is connected.
        if self._present or not self._camera.connected:
            self._absent_since = None
        elif self._absent_since is None:
            self._absent_since = now

        reason = self._close_reason(now)
        if reason is None:
            return
        # An utterance may be ending right now: decide again with the turn lock held.
        async with self._turn_lock:
            reason = self._close_reason(self._clock())
            if reason is not None:
                await self._close_conversation(reason)

    def _close_reason(self, now: float) -> Optional[str]:
        """Why the open conversation is over at `now`, or None."""
        if (
            self._conversation is None
            or self._state != AvatarState.IDLE
            or self._turn_running()
        ):
            return None
        if now - self._conversation_active_at >= self.config.CONVERSATION_IDLE_SEC:
            return "idle"
        if self._absent_since is not None:
            # A person who just spoke was there: absence counts from the later of the
            # last face and the last turn.
            absent_from = max(self._absent_since, self._conversation_active_at)
            if now - absent_from >= self.config.CONVERSATION_ABSENCE_SEC:
                return "absence"
        return None

    # ── Tracking (parallel plane) ─────────────────────────────────────────

    async def _tracking_loop(self) -> None:
        """Track the camera at TRACKING_FPS, regardless of the turn state, and send
        tracking_update while a client is connected. Presence is followed without a
        client too: each tick checks whether the conversation is over."""
        loop = asyncio.get_running_loop()
        interval = 1.0 / self.config.TRACKING_FPS
        while True:
            tick = loop.time()
            try:
                tracking = await self._read_tracking()
                if self.ws_manager.is_connected:
                    await self.ws_manager.send(tracking_message(
                        present=tracking.present,
                        head_rotation=self._head_rotation(tracking),
                        is_speaking=self._state == AvatarState.SPEAKING,
                    ))
            except Exception as e:
                logger.warning({"msg": "Tracking loop error", "error": str(e)})
            try:
                await self._check_conversation()
            except Exception as e:
                logger.warning({"msg": "Conversation check error", "error": str(e)})
            await asyncio.sleep(max(interval - (loop.time() - tick), 0.0))

    async def _read_tracking(self) -> TrackingResult:
        """Track the newest camera frame off the loop. No camera → no face."""
        frame = self._camera.take_frame()
        if frame is not None:
            started = time.perf_counter()
            self._last_tracking = await asyncio.get_running_loop().run_in_executor(
                self._vision_executor, self._tracker.process, frame
            )
            self._record_frame(frame, (time.perf_counter() - started) * 1000)
        elif not self._camera.connected:
            self._last_tracking = TrackingResult()
        self._note_presence(self._last_tracking.present)
        return self._last_tracking

    def _record_frame(self, frame: object, process_ms: float) -> None:
        """Measure one tracked frame and, with capture on, store it with its result.
        A frame is stored only while a client is connected: with nobody using the
        avatar, tracking still follows presence but no image is kept."""
        tracking = self._last_tracking
        perception_duration.record(process_ms, {"stage": "tracking"})
        frames_tracked.add(1, {"present": tracking.present})
        if self._capture and self.ws_manager.is_connected:
            rotation = tracking.head_rotation
            self._capture.frame(frame, {
                "present": tracking.present,
                "head_rotation": rotation.to_dict() if rotation else None,
                "process_ms": round(process_ms, 1),
                "state": self._state.value,
            })

    def _note_presence(self, present: bool) -> None:
        """A person arriving in front of the camera, or leaving it, is an event."""
        if present != self._present:
            self._present = present
            self._perception_event("person_arrived" if present else "person_left")

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
        if self._capture:
            self._capture.event("state", old=old.value, new=new_state.value)
        await self.ws_manager.send(state_message(new_state))
