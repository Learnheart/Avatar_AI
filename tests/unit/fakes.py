"""Fakes for the orchestrator's neighbours: VAD, engine, camera, tracker, memory,
WebSocket."""
import asyncio
import base64
import json
import time
from typing import AsyncIterator, Callable, Optional

from backend.config import AppConfig
from backend.core.contracts import Memory
from backend.models import (
    AudioEvent,
    ChatTurn,
    DoneEvent,
    EngineEvent,
    HeadRotation,
    MemoryProfile,
    TrackingResult,
    TranscriptEvent,
    VADEvent,
)
from backend.core.orchestrator import Orchestrator

TTS_RATE = 48000

# The first byte of a mic chunk tells FakeVAD what to report for it.
SILENCE, START, END, START_END = 0, 1, 2, 3
_SCRIPT = {
    SILENCE: [],
    START: [VADEvent.SPEECH_START],
    END: [VADEvent.SPEECH_END],
    START_END: [VADEvent.SPEECH_START, VADEvent.SPEECH_END],
}


def chunk(marker: int, fill: int = 0) -> bytes:
    """A 20 ms mic chunk (640 bytes) tagged with a FakeVAD marker."""
    return bytes([marker]) + bytes([fill]) * 639


def audio_message(pcm: bytes) -> str:
    return json.dumps(
        {"type": "audio_chunk", "audio_data": base64.b64encode(pcm).decode("ascii")}
    )


class FakeVAD:
    def __init__(self) -> None:
        self.loaded = False
        self.resets = 0

    def load(self) -> None:
        self.loaded = True

    def reset(self) -> None:
        self.resets += 1

    def process_chunk(self, pcm16: bytes) -> list[VADEvent]:
        return list(_SCRIPT[pcm16[0]])


class FakeEngine:
    """Replays `events` for each turn. `gate` holds the turn before any event (thinking),
    or right after the first event of type `gate_after` when that is set; `error` is
    raised after the first event of type `error_after`; `close_gate` holds the turn while
    it is being closed. `histories` and `user_ids` hold the history and the user id each
    turn was given."""

    def __init__(self) -> None:
        self.events: list[EngineEvent] = [
            TranscriptEvent("xin chào"),
            AudioEvent("Xin chào.", b"\x01\x00" * 480, TTS_RATE),
            AudioEvent("", b"\x02\x00" * 480, TTS_RATE),
            DoneEvent(),
        ]
        self.gate: Optional[asyncio.Event] = None
        self.gate_after: Optional[type] = None
        self.error: Optional[Exception] = None
        self.error_after: type = TranscriptEvent
        self.close_gate: Optional[asyncio.Event] = None
        self.utterances: list[bytes] = []
        self.histories: list[tuple] = []
        self.user_ids: list[Optional[str]] = []
        self.closed = 0
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def close(self) -> None:
        self.loaded = False

    async def respond(
        self, pcm16: bytes, sample_rate: int, history: tuple = (), user_id: Optional[str] = None
    ) -> AsyncIterator[EngineEvent]:
        self.utterances.append(pcm16)
        self.histories.append(tuple(history))
        self.user_ids.append(user_id)
        try:
            if self.gate is not None and self.gate_after is None:
                await self.gate.wait()
            for event in self.events:
                yield event
                if self.gate is not None and isinstance(event, self.gate_after or ()):
                    await self.gate.wait()
                if self.error and isinstance(event, self.error_after):
                    raise self.error
        finally:
            if self.close_gate is not None:
                await self.close_gate.wait()
            self.closed += 1


class FakeCamera:
    def __init__(self) -> None:
        self.connected = False
        self.frame: Optional[object] = None
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def take_frame(self) -> Optional[object]:
        frame, self.frame = self.frame, None
        return frame


class FakeTracker:
    def __init__(self) -> None:
        self.result = TrackingResult(present=True, head_rotation=HeadRotation(0.1, 0.2, 0.3))
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def close(self) -> None:
        self.loaded = False

    def process(self, frame: object) -> TrackingResult:
        return self.result


class FakeWebSocket:
    """Records every JSON message the server sends."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.accepted = False
        self.closed = False
        self.broken = False  # True: the client is gone, every send raises

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict) -> None:
        if self.broken:
            raise RuntimeError("socket is gone")
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True

    def of_type(self, *types: str) -> list[dict]:
        return [m for m in self.sent if m["type"] in types]

    def turn_messages(self) -> list[dict]:
        """Everything except the tracking stream and the `connected` handshake message."""
        return [m for m in self.sent if m["type"] not in ("tracking_update", "connected")]


class FakeClock:
    """The orchestrator's clock, moved by the test. The event loop keeps its own."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeMemory(Memory):
    """Records what the orchestrator tells the memory: `calls` holds
    ("record_turn", user id, conversation id, turn) and ("close_conversation", user id,
    conversation id) in the order they ran. `gate` holds every call until it is set;
    `error` is raised by every call."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.gate: Optional[asyncio.Event] = None
        self.error: Optional[Exception] = None
        self.cancelled: list[str] = []

    async def profile(self, user_id: str) -> MemoryProfile:
        return MemoryProfile()

    async def record_turn(self, user_id: str, conversation_id: str, turn: ChatTurn) -> None:
        await self._run("record_turn", user_id, conversation_id, turn)

    async def close_conversation(self, user_id: str, conversation_id: str) -> None:
        await self._run("close_conversation", user_id, conversation_id)

    async def _run(self, *call) -> None:
        try:
            if self.gate is not None:
                await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled.append(call[0])
            raise
        self.calls.append(call)
        if self.error:
            raise self.error


def build_orchestrator(
    config: AppConfig,
    clock: Callable[[], float] = time.monotonic,
    memory: Optional[Memory] = None,
) -> Orchestrator:
    """The orchestrator measures its conversation on `clock`."""
    return Orchestrator(
        config,
        vad=FakeVAD(),
        engine=FakeEngine(),
        camera=FakeCamera(),
        tracker=FakeTracker(),
        memory=memory,
        clock=clock,
    )


async def wait_until(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll `condition` on the event loop until it holds."""
    async def poll() -> None:
        while not condition():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(poll(), timeout=timeout)
