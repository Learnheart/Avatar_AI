import json
import wave
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.config import AppConfig
from backend.core.orchestrator import Orchestrator
from backend.main import create_app
from backend.utils import capture, telemetry
from backend.utils.capture import CaptureSpans, CaptureStore
from backend.utils.telemetry import tracer
from tests.unit.fakes import (
    END,
    START,
    FakeCamera,
    FakeEngine,
    FakeTracker,
    FakeVAD,
    FakeWebSocket,
    audio_message,
    build_orchestrator,
    chunk,
    wait_until,
)

FRAME = np.full((48, 64, 3), 128, dtype=np.uint8)
TRACKING = {"present": True, "head_rotation": {"pitch": 0.1, "yaw": 0.2, "roll": 0.3}}


@pytest.fixture
def config(tmp_path) -> AppConfig:
    return AppConfig(
        GROQ_API_KEY="test-key",
        CAPTURE_DIR=str(tmp_path / "capture"),
        KNOWLEDGE_DB_PATH=str(tmp_path / "knowledge.db"),
        DATABASE_URL=f"sqlite:///{tmp_path / 'app.db'}",
        PLAYBACK_END_TIMEOUT_SEC=0.05,
        TRACKING_FPS=200,
    )


@pytest.fixture
def store(config) -> Iterator[CaptureStore]:
    store = CaptureStore(config)
    store.start()
    yield store
    store.stop()


def _files(config: AppConfig, pattern: str) -> list[Path]:
    return sorted(Path(config.CAPTURE_DIR).glob(pattern))


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_a_frame_is_stored_as_a_jpeg_with_its_tracking_line(config, store):
    store.frame(FRAME, TRACKING)
    store.stop()

    (image,) = _files(config, "*/*/frames/*.jpg")
    (tracking,) = _files(config, "*/*/tracking.jsonl")
    (line,) = _lines(tracking)
    assert cv2.imread(str(image)).shape == FRAME.shape
    assert line["frame"] == image.name
    assert line["present"] is True and line["head_rotation"]["yaw"] == 0.2
    assert datetime.fromisoformat(line["ts"])
    assert image.parent.parent == tracking.parent  # the same hour directory


def test_the_frame_rate_thins_the_images_but_not_the_tracking_lines(config, store):
    config.CAPTURE_FRAME_FPS = 0.001  # one image per 1000 s

    for _ in range(5):
        store.frame(FRAME, TRACKING)
    store.stop()

    assert len(_files(config, "*/*/frames/*.jpg")) == 1
    lines = _lines(_files(config, "*/*/tracking.jsonl")[0])
    assert [bool(line["frame"]) for line in lines] == [True, False, False, False, False]


def test_an_event_is_one_line_with_the_trace_it_happened_in(config, store):
    store.event("person_arrived")
    with tracer.start_as_current_span("turn") as span:
        store.event("speech_end", utterance_ms=1200)
    store.stop()

    arrived, speech = _lines(_files(config, "*/*/events.jsonl")[0])
    assert arrived["event"] == "person_arrived" and "trace_id" not in arrived
    assert speech["utterance_ms"] == 1200
    assert speech["trace_id"] == format(span.get_span_context().trace_id, "032x")


def test_the_audio_of_a_trace_is_a_wav_file_in_its_directory(config, store):
    pcm = np.arange(1600, dtype="<i2").tobytes()

    store.audio("abc123", "utterance", pcm, 16000)
    store.audio("abc123", "answer", pcm * 3, 48000)
    store.audio("abc123", "empty", b"", 16000)
    store.stop()

    (trace_dir,) = _files(config, "*/*/traces/*_abc123")
    assert sorted(p.name for p in trace_dir.iterdir()) == ["answer.wav", "utterance.wav"]
    with wave.open(str(trace_dir / "utterance.wav")) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        assert wav.readframes(wav.getnframes()) == pcm


def test_the_spans_of_a_trace_are_written_when_its_root_ends(config, store):
    collector = CaptureSpans(store)
    telemetry.add_span_processor(collector)
    try:
        with tracer.start_as_current_span("turn") as turn:
            with tracer.start_as_current_span("stt") as stt:
                stt.set_attribute("stt.text", "x" * 5000)  # stored whole, unlike the log
                stt.add_event("primary_backend_failed", {"stage": "stt"})
            assert _files(config, "*/*/traces/*/trace.json") == []  # the root is still open
        store.stop()
    finally:
        collector.on_end = lambda span: None  # a processor cannot be removed: silence it

    trace_id = format(turn.get_span_context().trace_id, "032x")
    (path,) = _files(config, f"*/*/traces/*_{trace_id}/trace.json")
    record = json.loads(path.read_text())
    root, child = record["spans"]
    assert record["trace_id"] == trace_id
    assert (root["name"], root["parent_id"]) == ("turn", None)
    assert (child["name"], child["parent_id"]) == ("stt", root["span_id"])
    assert child["attributes"]["stt.text"] == "x" * 5000
    assert child["events"][0]["name"] == "primary_backend_failed"
    assert child["events"][0]["stage"] == "stt"
    assert child["duration_ms"] >= 0 and child["error"] is None


def test_a_trace_without_a_span_of_the_backend_is_not_stored(config, store):
    collector = CaptureSpans(store)
    telemetry.add_span_processor(collector)
    try:
        with telemetry.trace.get_tracer("fastapi").start_as_current_span("GET /health"):
            pass
        with telemetry.trace.get_tracer("fastapi").start_as_current_span("POST /knowledge/documents"):
            with tracer.start_as_current_span("knowledge.ingest"):
                pass
        store.stop()
    finally:
        collector.on_end = lambda span: None

    (path,) = _files(config, "*/*/traces/*/trace.json")
    assert [s["name"] for s in json.loads(path.read_text())["spans"]] == [
        "POST /knowledge/documents", "knowledge.ingest",
    ]


def test_the_oldest_hours_are_deleted_over_the_size_cap_and_the_current_one_kept(config):
    root = Path(config.CAPTURE_DIR)
    old = datetime.now() - timedelta(days=2)
    for hour in ("03", "04"):
        directory = root / old.strftime("%Y-%m-%d") / hour
        directory.mkdir(parents=True)
        (directory / "tracking.jsonl").write_bytes(b"x" * 4000)
    config.CAPTURE_MAX_GB = 5000 / 1024 ** 3  # room for one old hour, not two

    store = CaptureStore(config)
    store.start()
    store.event("person_arrived")
    store.stop()

    assert [p.name for p in _files(config, f"{old:%Y-%m-%d}/*")] == ["04"]
    assert _files(config, f"{datetime.now():%Y-%m-%d}/*/events.jsonl")


def test_the_current_hour_is_kept_even_over_the_cap(config):
    config.CAPTURE_MAX_GB = 0.0

    store = CaptureStore(config)
    store.start()
    store.event("person_arrived")
    store.event("person_left")
    store.stop()

    assert len(_lines(_files(config, "*/*/events.jsonl")[0])) == 2


def test_a_full_queue_drops_the_item_and_counts_it(config, metric_points):
    def dropped() -> int:
        return sum(p.value for p in metric_points("avatar.capture.dropped"))

    config.CAPTURE_QUEUE_SIZE = 2
    store = CaptureStore(config)  # not started: nothing drains the queue
    before = dropped()

    for _ in range(5):
        store.event("person_arrived")

    assert dropped() == before + 3


def test_a_write_error_does_not_stop_the_capture(config, store, monkeypatch):
    write = capture._append_line

    def fail_once(path, record):
        monkeypatch.setattr(capture, "_append_line", write)
        raise OSError("disk full")

    monkeypatch.setattr(capture, "_append_line", fail_once)

    store.event("person_arrived")  # its write fails
    store.event("person_left")
    store.stop()

    assert [line["event"] for line in _lines(_files(config, "*/*/events.jsonl")[0])] == [
        "person_left"
    ]


# ── Wiring: what the orchestrator hands to the store ──────────────────────

async def test_a_turn_stores_its_audio_the_frames_and_the_perception_events(config, store):
    orch = Orchestrator(
        config, vad=FakeVAD(), engine=FakeEngine(), camera=FakeCamera(),
        tracker=FakeTracker(), capture=store,
    )
    await orch.start()
    try:
        await orch.handle_connect(FakeWebSocket())
        orch._camera.frame = FRAME
        await wait_until(lambda: orch._camera.frame is None)
        for marker in (START, END):
            await orch.handle_client_message(audio_message(chunk(marker)))
        await wait_until(lambda: orch._turn_task is not None and orch._turn_task.done())
    finally:
        await orch.stop()
    store.stop()

    (trace_dir,) = _files(config, "*/*/traces/*")
    assert sorted(p.name for p in trace_dir.iterdir()) == ["answer.wav", "utterance.wav"]
    with wave.open(str(trace_dir / "answer.wav")) as wav:
        assert wav.getframerate() == 48000 and wav.getnframes() == 960  # both FakeEngine chunks

    (tracking,) = _lines(_files(config, "*/*/tracking.jsonl")[0])
    assert tracking["present"] is True and tracking["state"] == "idle"
    assert tracking["head_rotation"] == {"pitch": 0.1, "yaw": 0.2, "roll": 0.3}
    assert tracking["process_ms"] >= 0
    assert len(_files(config, "*/*/frames/*.jpg")) == 1

    events = _lines(_files(config, "*/*/events.jsonl")[0])
    kinds = [e["event"] for e in events]
    assert kinds.index("person_arrived") < kinds.index("speech_start") < kinds.index("speech_end")
    assert {"old": "listening", "new": "thinking"}.items() <= next(
        e for e in events if e["event"] == "state" and e["new"] == "thinking"
    ).items()


async def test_a_frame_is_stored_only_while_a_client_is_connected(config, store):
    orch = Orchestrator(
        config, vad=FakeVAD(), engine=FakeEngine(), camera=FakeCamera(),
        tracker=FakeTracker(), capture=store,
    )
    stored: list[bool] = []  # for each frame handed to the store: was a client connected
    store_frame = store.frame

    def frame(image, tracking) -> None:
        stored.append(orch.ws_manager.is_connected)
        store_frame(image, tracking)

    store.frame = frame
    await orch.start()
    try:
        # No client: the frame is tracked (the person is noticed) but not stored.
        orch._camera.frame = FRAME
        await wait_until(lambda: orch._present)
        assert stored == []

        await orch.handle_connect(FakeWebSocket())
        orch._camera.frame = FRAME
        await wait_until(lambda: stored != [])
    finally:
        await orch.stop()
    store.stop()

    assert stored == [True]
    assert len(_lines(_files(config, "*/*/tracking.jsonl")[0])) == 1
    assert len(_files(config, "*/*/frames/*.jpg")) == 1
    # The arrival was noticed without a client, and is still an event.
    events = _lines(_files(config, "*/*/events.jsonl")[0])
    assert "person_arrived" in [e["event"] for e in events]


def test_capture_is_off_by_default(config):
    with TestClient(create_app(config, build_orchestrator(config))):
        pass

    assert not Path(config.CAPTURE_DIR).exists()


def test_capture_enabled_starts_the_store_with_the_app(config):
    config.CAPTURE_ENABLED = 1
    with TestClient(create_app(config, build_orchestrator(config))):
        assert Path(config.CAPTURE_DIR).is_dir()
