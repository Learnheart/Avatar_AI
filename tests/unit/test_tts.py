import sys
import threading
from types import ModuleType

import numpy as np
import pytest

from backend.config import AppConfig
from backend.providers.catalog import create_tts
from backend.core.contracts import TTSError
from backend.providers.tts.zerotts import ZeroTTSBackend, float_to_pcm16

RATE = 48000
FRAME = RATE * 80 // 1000  # the library emits 80 ms frames


class FakeZeroTTS:
    """Stands in for zerotts.ZeroTTS: replays scripted float32 frames."""

    sample_rate = RATE
    frames: list = []
    error: Exception | None = None
    calls: list[tuple[str, str]] = []
    repo: str | None = None
    produced = 0
    voices = ["maichi"]

    @classmethod
    def from_pretrained(cls, repo):
        cls.repo = repo
        return cls()

    def load_voice(self, name):
        if name not in type(self).voices:
            raise FileNotFoundError(f"no voice {name!r} (available: {type(self).voices})")

    def synthesize_stream(self, text, voice):
        type(self).calls.append((text, voice))
        for frame in type(self).frames:
            type(self).produced += 1
            yield frame
        if type(self).error:
            raise type(self).error


@pytest.fixture
def fake_zerotts(monkeypatch) -> type[FakeZeroTTS]:
    module = ModuleType("zerotts")
    module.ZeroTTS = FakeZeroTTS
    module.normalize_vi_text = lambda text: f"norm({text})"
    monkeypatch.setitem(sys.modules, "zerotts", module)
    FakeZeroTTS.frames = []
    FakeZeroTTS.error = None
    FakeZeroTTS.calls = []
    FakeZeroTTS.produced = 0
    return FakeZeroTTS


@pytest.fixture
def tts(config, fake_zerotts) -> ZeroTTSBackend:
    backend = ZeroTTSBackend(config)
    backend.load()
    return backend


async def _collect(tts: ZeroTTSBackend, text: str = "Xin chào.") -> list[bytes]:
    return [chunk async for chunk in tts.synthesize_stream(text)]


def test_float32_is_converted_to_int16_and_clipped():
    pcm = float_to_pcm16(np.array([[0.0, 0.5, 1.0, -1.0, 2.0, -3.0]], dtype=np.float32))

    assert np.frombuffer(pcm, dtype="<i2").tolist() == [0, 16383, 32767, -32767, 32767, -32767]


def test_one_dimensional_frames_are_accepted():
    assert float_to_pcm16(np.zeros(10, dtype=np.float32)) == b"\x00" * 20


def test_load_reads_repo_and_sample_rate(tts, config, fake_zerotts):
    assert fake_zerotts.repo == config.ZEROTTS_REPO
    assert tts.sample_rate == RATE


def test_load_failure_raises_tts_error(config, monkeypatch):
    monkeypatch.setitem(sys.modules, "zerotts", None)  # import zerotts → ImportError

    with pytest.raises(TTSError):
        ZeroTTSBackend(config).load()


def test_load_failure_names_the_repo_and_the_hugging_face_download(config, fake_zerotts, monkeypatch):
    def unreachable(repo):
        raise OSError("<html>\n" + "<p>blocked by proxy</p>\n" * 1000)

    monkeypatch.setattr(fake_zerotts, "from_pretrained", unreachable)

    with pytest.raises(TTSError) as raised:
        ZeroTTSBackend(config).load()

    message = str(raised.value)
    assert repr(config.ZEROTTS_REPO) in message
    assert "downloaded from Hugging Face on the first start" in message
    assert "OSError" in message
    assert "\n" not in message
    assert len(message) < config.ERROR_SUMMARY_MAX_CHARS + 300


def test_load_rejects_an_unknown_voice(config, fake_zerotts):
    config.ZEROTTS_VOICE = "nobody"

    with pytest.raises(TTSError, match="ZEROTTS_VOICE 'nobody'.*maichi"):
        ZeroTTSBackend(config).load()


def test_close_shuts_the_worker_thread_down(tts):
    tts.close()

    with pytest.raises(RuntimeError):
        tts._executor.submit(lambda: None)


async def test_chunks_are_aggregated_to_tts_chunk_ms_with_even_length(tts, config, fake_zerotts):
    # 7 frames of 80 ms = 560 ms → 240 + 240 + 80 ms.
    fake_zerotts.frames = [np.full((1, FRAME), 0.25, dtype=np.float32)] * 7

    chunks = await _collect(tts)

    chunk_bytes = RATE * config.TTS_CHUNK_MS // 1000 * 2
    assert [len(chunk) for chunk in chunks] == [chunk_bytes, chunk_bytes, FRAME * 2]
    assert all(len(chunk) % 2 == 0 for chunk in chunks)
    assert b"".join(chunks) == float_to_pcm16(np.full(FRAME * 7, 0.25, dtype=np.float32))


async def test_text_is_normalised_and_a_voice_is_always_passed(tts, config, fake_zerotts):
    fake_zerotts.frames = [np.zeros((1, FRAME), dtype=np.float32)]

    await _collect(tts, "Lãi suất 5%.")

    assert fake_zerotts.calls == [("norm(Lãi suất 5%.)", config.ZEROTTS_VOICE)]


@pytest.mark.parametrize("text", ["", "   \n"])
async def test_empty_text_yields_nothing(tts, fake_zerotts, text):
    assert await _collect(tts, text) == []
    assert fake_zerotts.calls == []


async def test_synthesis_error_raises_tts_error(tts, fake_zerotts):
    fake_zerotts.frames = [np.zeros((1, FRAME), dtype=np.float32)]
    fake_zerotts.error = RuntimeError("onnx failed")

    with pytest.raises(TTSError, match="onnx failed"):
        await _collect(tts)


async def test_synthesize_before_load_raises_tts_error(config, fake_zerotts):
    with pytest.raises(TTSError):
        await _collect(ZeroTTSBackend(config))


async def test_closing_the_stream_stops_the_worker(tts, fake_zerotts):
    fake_zerotts.frames = [np.zeros((1, FRAME), dtype=np.float32)] * 5000

    stream = tts.synthesize_stream("Một câu rất dài.")
    await anext(stream)
    await stream.aclose()

    # The single TTS worker thread is free again only once the old synthesis stopped.
    idle = threading.Event()
    tts._executor.submit(idle.set)
    assert idle.wait(timeout=5)
    assert fake_zerotts.produced < 5000


def test_create_tts_picks_the_configured_backend(config):
    assert isinstance(create_tts(config), ZeroTTSBackend)


def test_create_tts_rejects_unknown_backend():
    with pytest.raises(ValueError, match="nope"):
        create_tts(AppConfig(GROQ_API_KEY="test-key", TTS_BACKEND="nope"))
