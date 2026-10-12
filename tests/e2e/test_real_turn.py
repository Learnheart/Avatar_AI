"""
One real turn over the WebSocket against the real backend (`python -m backend.main`):
real VAD, Groq Whisper, Groq LLM and ZeroTTS. No camera is needed — the app keeps
running without one. The last test uploads a document and asks about it.
"""
import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator, Optional

import httpx
import pytest
import websockets

from backend.config import AppConfig
from backend.models import MIC_SAMPLE_RATE
from backend.providers.catalog import create_stt
from tests.e2e.helpers import E2E_MARKS, looks_vietnamese, resample_to_mic_rate

pytestmark = E2E_MARKS

REPO_ROOT = Path(__file__).resolve().parents[2]
CHUNK_SEC = 0.02
CHUNK_BYTES = int(MIC_SAMPLE_RATE * CHUNK_SEC) * 2  # 320 samples, like the frontend
TRAILING_SILENCE_SEC = 1.5
STARTUP_TIMEOUT_SEC = 900  # the first run downloads the ZeroTTS weights (~910 MB)
FIRST_AUDIO_BUDGET_SEC = 3.5
END_TO_END_BUDGET_SEC = 10.0


@pytest.fixture(scope="module")
def ws_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Start the real app through its documented entry point on a free port (HOST / PORT
    go in through the environment) and wait until /health answers."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    work_dir = tmp_path_factory.mktemp("backend")
    log_path = work_dir / "backend.log"
    env = {
        **os.environ,
        "HOST": "127.0.0.1",
        "PORT": str(port),
        # An empty knowledge base of its own, not the developer's documents.
        "KNOWLEDGE_DB_PATH": str(work_dir / "knowledge.db"),
    }
    with open(log_path, "w") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "backend.main"],
            cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_SEC
        while True:
            if process.poll() is not None:
                pytest.fail(f"backend exited during startup:\n{log_path.read_text()[-4000:]}")
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                pytest.fail(f"backend not ready in time:\n{log_path.read_text()[-4000:]}")
            time.sleep(1)
        yield f"ws://127.0.0.1:{port}/ws"
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
        print(f"\n[backend log] {log_path}")


class Session:
    """A WebSocket client that records every server message with its arrival time."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self.messages: list[tuple[float, dict]] = []  # tracking_update excluded
        self.tracking_updates = 0
        self._arrived = asyncio.Condition()
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        async for raw in self._ws:
            message = json.loads(raw)
            if message["type"] == "tracking_update":
                self.tracking_updates += 1
                continue
            async with self._arrived:
                self.messages.append((time.monotonic(), message))
                self._arrived.notify_all()

    async def wait_for(
        self, match: Callable[[dict], bool], timeout: float, after: int = 0
    ) -> tuple[int, float, dict]:
        """Wait for the first message at index >= `after` that matches. Returns (index, time, message)."""
        def find() -> Optional[tuple[int, float, dict]]:
            for index in range(after, len(self.messages)):
                at, message = self.messages[index]
                if match(message):
                    return index, at, message
            return None

        async with self._arrived:
            await asyncio.wait_for(
                self._arrived.wait_for(lambda: find() is not None), timeout=timeout
            )
            return find()

    async def stream(self, pcm16: bytes) -> float:
        """Send PCM as 20 ms audio_chunk messages paced in real time.
        Returns the time the last chunk was sent."""
        started = time.monotonic()
        for index, offset in enumerate(range(0, len(pcm16), CHUNK_BYTES)):
            delay = started + index * CHUNK_SEC - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            payload = base64.b64encode(pcm16[offset:offset + CHUNK_BYTES]).decode("ascii")
            await self._ws.send(json.dumps({"type": "audio_chunk", "audio_data": payload}))
        return time.monotonic()

    async def stream_silence(self, seconds: float) -> None:
        await self.stream(b"\x00" * (int(seconds / CHUNK_SEC) * CHUNK_BYTES))

    async def send(self, message: dict) -> None:
        await self._ws.send(json.dumps(message))

    def of_type(self, message_type: str) -> list[tuple[float, dict]]:
        return [(at, m) for at, m in self.messages if m["type"] == message_type]

    async def close(self) -> None:
        self._reader.cancel()
        await asyncio.gather(self._reader, return_exceptions=True)


@pytest.fixture
async def session(ws_url: str) -> AsyncIterator[Session]:
    async with websockets.connect(ws_url, max_size=None) as ws:
        client = Session(ws)
        await client.wait_for(lambda m: m["type"] == "connected", timeout=10)
        yield client
        await client.close()


def _is_state(state: str) -> Callable[[dict], bool]:
    return lambda m: m["type"] == "state" and m["state"] == state


async def test_full_turn_over_websocket(session: Session, utterance_pcm: bytes):
    speech_end = await session.stream(utterance_pcm)
    await session.stream_silence(TRAILING_SILENCE_SEC)

    final_index, final_at, _ = await session.wait_for(
        lambda m: m["type"] == "speech_output" and m["is_final"], timeout=30
    )
    await session.send({"type": "playback_ended"})
    await session.wait_for(_is_state("idle"), timeout=10, after=final_index)

    assert session.of_type("error") == []
    states = [m["state"] for _, m in session.of_type("state")]
    assert states == ["idle", "listening", "thinking", "speaking", "idle"]  # first: handshake

    speech = session.of_type("speech_output")
    with_audio = [(at, m) for at, m in speech if m["audio_data"]]
    assert with_audio, "no audio came back"
    mime = with_audio[0][1]["audio_mime_type"]
    assert mime.startswith("audio/pcm;rate=")
    sample_rate = int(mime.split("rate=")[1])
    pcm = b"".join(base64.b64decode(m["audio_data"]) for _, m in with_audio)
    answer_text = " ".join(m["text_content"] for _, m in speech if m["text_content"])

    first_audio = with_audio[0][0] - speech_end
    end_to_end = final_at - speech_end
    heard = await create_stt(AppConfig()).transcribe(
        resample_to_mic_rate(pcm, sample_rate), MIC_SAMPLE_RATE
    )
    print(
        f"\n[turn] first audio {first_audio:.2f}s  end to end {end_to_end:.2f}s"
        f"  audio {len(pcm) / 2 / sample_rate:.2f}s @ {sample_rate} Hz"
        f"\n[turn] answer text: {answer_text!r}"
        f"\n[turn] answer audio transcribed back: {heard.text!r}"
    )

    assert len(pcm) % 2 == 0
    assert any(pcm), "speech_output audio is silent"
    assert looks_vietnamese(answer_text)
    assert heard.text and looks_vietnamese(heard.text)
    assert session.tracking_updates > 0, "tracking_update must flow without a camera"
    assert first_audio < FIRST_AUDIO_BUDGET_SEC
    assert end_to_end < END_TO_END_BUDGET_SEC


async def test_barge_in_during_speaking(session: Session, utterance_pcm: bytes):
    await session.stream(utterance_pcm)
    await session.stream_silence(TRAILING_SILENCE_SEC)
    speaking_index, _, _ = await session.wait_for(_is_state("speaking"), timeout=30)

    # Speak again while the avatar is speaking (no playback_ended is sent).
    await session.stream(utterance_pcm)

    interrupt_index, _, _ = await session.wait_for(
        lambda m: m["type"] == "interrupt", timeout=10, after=speaking_index
    )
    await session.wait_for(_is_state("listening"), timeout=5, after=interrupt_index)
    assert session.of_type("error") == []


# ── Knowledge base: upload a document, then ask about it ──────────────────

# A made-up rate no model knows; it answers the question of the test utterance.
KNOWLEDGE_DOCUMENT = (
    "Biểu lãi suất nội bộ của ngân hàng.\n\n"
    "Lãi suất tiết kiệm kỳ hạn sáu tháng là 7,35 phần trăm một năm. "
    "Lãi suất tiết kiệm kỳ hạn mười hai tháng là 8,15 phần trăm một năm.\n\n"
    "Quy định nghỉ phép: mỗi nhân viên được nghỉ phép năm mười hai ngày."
)
KNOWLEDGE_FACT = ("7,35", "7.35", "bảy phẩy ba")


async def _ask(session: Session, utterance_pcm: bytes) -> tuple[str, float, float]:
    """One spoken question, up to the avatar being idle again.
    Returns (answer text, first audio, end to end) of this turn."""
    turn_start = len(session.messages)
    speech_end = await session.stream(utterance_pcm)
    await session.stream_silence(TRAILING_SILENCE_SEC)
    final_index, final_at, _ = await session.wait_for(
        lambda m: m["type"] == "speech_output" and m["is_final"], timeout=30, after=turn_start
    )
    await session.send({"type": "playback_ended"})
    await session.wait_for(_is_state("idle"), timeout=10, after=final_index)

    speech = [
        (at, m) for at, m in session.messages[turn_start:] if m["type"] == "speech_output"
    ]
    answer_text = " ".join(m["text_content"] for _, m in speech if m["text_content"])
    first_audio = next(at for at, m in speech if m["audio_data"]) - speech_end
    return answer_text, first_audio, final_at - speech_end


def _has_fact(answer_text: str) -> bool:
    return any(fact in answer_text.lower() for fact in KNOWLEDGE_FACT)


async def test_turn_answers_from_an_uploaded_document(
    session: Session, ws_url: str, utterance_pcm: bytes
):
    documents_url = ws_url.replace("ws://", "http://").removesuffix("/ws") + "/knowledge/documents"
    async with httpx.AsyncClient(timeout=60) as http:
        upload_started = time.monotonic()
        uploaded = await http.post(
            documents_url, files={"file": ("lai-suat.txt", KNOWLEDGE_DOCUMENT.encode("utf-8"))}
        )
        upload_sec = time.monotonic() - upload_started
        assert uploaded.status_code == 201, uploaded.text
        document = uploaded.json()
        document_url = f"{documents_url}/{document['id']}"
        try:
            assert (await http.get(documents_url)).json() == {"documents": [document]}

            # Not selected: the document is in the base but the avatar does not use it.
            unselected = await http.patch(document_url, json={"enabled": False})
            assert unselected.json()["enabled"] is False
            without_text, _, _ = await _ask(session, utterance_pcm)

            selected = await http.patch(document_url, json={"enabled": True})
            assert selected.json() == document
            answer_text, first_audio, end_to_end = await _ask(session, utterance_pcm)
        finally:
            deleted = await http.delete(document_url)
        assert deleted.status_code == 204
        assert (await http.get(documents_url)).json() == {"documents": []}

    print(
        f"\n[knowledge] upload {upload_sec:.2f}s  first audio {first_audio:.2f}s"
        f"  end to end {end_to_end:.2f}s"
        f"\n[knowledge] answer, document not selected: {without_text!r}"
        f"\n[knowledge] answer, document selected: {answer_text!r}"
    )

    assert session.of_type("error") == []
    assert not _has_fact(without_text), without_text
    assert _has_fact(answer_text), answer_text
    assert first_audio < FIRST_AUDIO_BUDGET_SEC
    assert end_to_end < END_TO_END_BUDGET_SEC
