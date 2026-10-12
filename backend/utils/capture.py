"""
Full capture of what the kiosk saw and heard, on local disk (CAPTURE_ENABLED).

    CAPTURE_DIR/<YYYY-MM-DD>/<HH>/
        frames/<HHMMSS.mmm>.jpg     every tracked camera frame (CAPTURE_FRAME_FPS thins them)
        tracking.jsonl              one line per tracked frame: presence, head rotation, timing
        events.jsonl                presence changes, speech start / end, state changes
        traces/<HHMMSS>_<trace id>/
            utterance.wav           what the person said (a turn)
            answer.wav              what the avatar answered (a turn)
            trace.json              every span of the trace with its full attributes

The callers only queue: a writer thread encodes and writes, so capture never blocks the
event loop or the vision thread. A full queue drops the item and counts it. The oldest
hours are deleted when the directory grows past CAPTURE_MAX_GB.

This stores faces and voices. It is off by default; who may read CAPTURE_DIR and how long
it is kept are the operator's responsibility.
"""
import json
import queue
import shutil
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.trace import StatusCode

from backend.config import AppConfig
from backend.models import PCM_SAMPLE_WIDTH
from backend.utils.logging import get_logger
from backend.utils.telemetry import capture_dropped, is_own_span

logger = get_logger("capture")

_GB = 1024 ** 3
_NS_PER_SEC = 1_000_000_000
# Traces whose directory (or whose unfinished spans) are remembered at one time.
_OPEN_TRACES = 256
# Seconds between two "cannot free space" warnings.
_WARN_EVERY_SEC = 60.0

# A queued write: does the I/O, returns the bytes it added to the directory.
_Write = Callable[[], int]


class CaptureStore:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._root = Path(config.CAPTURE_DIR)
        self._queue: "queue.Queue[Optional[_Write]]" = queue.Queue(config.CAPTURE_QUEUE_SIZE)
        self._thread: Optional[threading.Thread] = None
        self._bytes = 0
        self._last_frame_at = 0.0
        self._last_warning_at = 0.0
        self._trace_dirs: dict[str, Path] = {}
        self._lock = threading.Lock()  # guards _trace_dirs and _last_frame_at

    def start(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self._thread.start()
        logger.info({
            "msg": "Capture started",
            "dir": str(self._root),
            "max_gb": self.config.CAPTURE_MAX_GB,
        })

    def stop(self) -> None:
        """Write what is queued, then stop the writer."""
        if self._thread is None:
            return
        self._queue.put(None)
        self._thread.join()
        self._thread = None

    # ── What to capture (never blocks) ────────────────────────────────────

    def frame(self, frame: np.ndarray, tracking: dict[str, Any]) -> None:
        """One tracked camera frame and its tracking result. With CAPTURE_FRAME_FPS, only
        that many images per second are kept; the tracking line is always written."""
        now = datetime.now()
        fps = self.config.CAPTURE_FRAME_FPS
        with self._lock:
            keep = fps <= 0 or now.timestamp() - self._last_frame_at >= 1.0 / fps
            if keep:
                self._last_frame_at = now.timestamp()
        name = now.strftime("%H%M%S.") + f"{now.microsecond // 1000:03d}.jpg" if keep else None
        line = {"ts": now.isoformat(timespec="milliseconds"), "frame": name, **tracking}
        hour = self._hour_dir(now)

        def write() -> int:
            written = _append_line(hour / "tracking.jsonl", line)
            if name:
                quality = [cv2.IMWRITE_JPEG_QUALITY, self.config.CAPTURE_JPEG_QUALITY]
                encoded, jpeg = cv2.imencode(".jpg", frame, quality)
                if encoded:
                    written += _write_bytes(hour / "frames" / name, jpeg.tobytes())
            return written

        self._submit("frame", write)

    def event(self, kind: str, **data: Any) -> None:
        """One perception or state event."""
        now = datetime.now()
        line = {"ts": now.isoformat(timespec="milliseconds"), "event": kind, **data}
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            line["trace_id"] = trace.format_trace_id(context.trace_id)
        path = self._hour_dir(now) / "events.jsonl"
        self._submit("event", lambda: _append_line(path, line))

    def audio(self, trace_id: str, name: str, pcm: bytes, sample_rate: int) -> None:
        """Int16 LE mono PCM of a trace, as `<name>.wav`."""
        if not pcm:
            return
        path = self._trace_dir(trace_id) / f"{name}.wav"
        self._submit("audio", lambda: _write_wav(path, pcm, sample_rate))

    def spans(self, trace_id: str, spans: list[dict]) -> None:
        """Every span of a finished trace, as `trace.json`."""
        path = self._trace_dir(trace_id) / "trace.json"
        body = json.dumps({"trace_id": trace_id, "spans": spans}, ensure_ascii=False, indent=2)
        self._submit("trace", lambda: _write_bytes(path, body.encode("utf-8")))

    # ── Layout ────────────────────────────────────────────────────────────

    def _hour_dir(self, at: datetime) -> Path:
        return self._root / at.strftime("%Y-%m-%d") / at.strftime("%H")

    def _trace_dir(self, trace_id: str) -> Path:
        """The directory of a trace, named after the time of its first item."""
        with self._lock:
            if trace_id not in self._trace_dirs:
                now = datetime.now()
                name = f"{now.strftime('%H%M%S')}_{trace_id}"
                self._trace_dirs[trace_id] = self._hour_dir(now) / "traces" / name
                while len(self._trace_dirs) > _OPEN_TRACES:
                    del self._trace_dirs[next(iter(self._trace_dirs))]
            return self._trace_dirs[trace_id]

    # ── Writer thread ─────────────────────────────────────────────────────

    def _submit(self, kind: str, write: _Write) -> None:
        try:
            self._queue.put_nowait(write)
        except queue.Full:
            capture_dropped.add(1, {"kind": kind})

    def _run(self) -> None:
        self._bytes = _tree_size(self._root)
        while (write := self._queue.get()) is not None:
            try:
                self._bytes += write()
                self._prune()
            except Exception as e:  # a disk error must not end the capture
                logger.warning({"msg": "Capture write failed", "error": f"{type(e).__name__}: {e}"})

    def _prune(self) -> None:
        """Delete the oldest hours until the directory is back under CAPTURE_MAX_GB. The
        current hour is never deleted."""
        limit = self.config.CAPTURE_MAX_GB * _GB
        if self._bytes <= limit:
            return
        current = self._hour_dir(datetime.now())
        hours = sorted(p for p in self._root.glob("*/*") if p.is_dir() and p != current)
        for hour in hours:
            if self._bytes <= limit:
                return
            self._bytes -= _tree_size(hour)
            shutil.rmtree(hour, ignore_errors=True)
            logger.info({"msg": "Capture hour deleted (size cap)", "hour": str(hour)})
        if self._bytes > limit and time.monotonic() - self._last_warning_at > _WARN_EVERY_SEC:
            self._last_warning_at = time.monotonic()
            logger.warning({
                "msg": "Capture is over its size cap within the current hour",
                "gb": round(self._bytes / _GB, 2),
                "max_gb": self.config.CAPTURE_MAX_GB,
            })


class CaptureSpans(SpanProcessor):
    """Collects the spans of each trace and hands them to the store when the root span
    ends (the turn, or a document upload). A trace with no span of the backend — a
    plain HTTP request such as the health check — is not stored."""

    def __init__(self, store: CaptureStore) -> None:
        self._store = store
        # Per trace: (start time, is a child, is the backend's, record) of each finished span.
        self._open: dict[int, list[tuple[int, bool, bool, dict]]] = {}
        self._lock = threading.Lock()

    def on_end(self, span: ReadableSpan) -> None:
        trace_id = span.context.trace_id
        with self._lock:
            spans = self._open.setdefault(trace_id, [])
            spans.append(
                (span.start_time, span.parent is not None, is_own_span(span), _span_record(span))
            )
            if span.parent is not None:
                # A trace whose root never ends is forgotten once enough others came.
                while len(self._open) > _OPEN_TRACES:
                    del self._open[next(iter(self._open))]
                return
            del self._open[trace_id]
        # In the order the steps started; the root first when a child starts with it.
        if any(own for _, _, own, _ in spans):
            ordered = [record for *_, record in sorted(spans, key=lambda s: s[:2])]
            self._store.spans(trace.format_trace_id(trace_id), ordered)


def _span_record(span: ReadableSpan) -> dict:
    def iso(ns: int) -> str:
        return datetime.fromtimestamp(ns / _NS_PER_SEC).isoformat(timespec="milliseconds")

    return {
        "name": span.name,
        "span_id": trace.format_span_id(span.context.span_id),
        "parent_id": trace.format_span_id(span.parent.span_id) if span.parent else None,
        "start": iso(span.start_time),
        "end": iso(span.end_time),
        "duration_ms": round((span.end_time - span.start_time) / 1_000_000, 1),
        "error": span.status.description if span.status.status_code is StatusCode.ERROR else None,
        "attributes": dict(span.attributes or {}),
        "events": [
            {"name": event.name, "at": iso(event.timestamp), **dict(event.attributes or {})}
            for event in span.events
        ],
    }


# ── File helpers (writer thread) ──────────────────────────────────────────

def _append_line(path: Path, record: dict) -> int:
    line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as file:
        file.write(line)
    return len(line)


def _write_bytes(path: Path, data: bytes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return len(data)


def _write_wav(path: Path, pcm: bytes, sample_rate: int) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(PCM_SAMPLE_WIDTH)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return path.stat().st_size


def _tree_size(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
