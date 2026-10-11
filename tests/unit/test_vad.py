import sys
from types import ModuleType

import numpy as np
import pytest

from backend.models import VADEvent
from backend.core.perception.vad import VADModule

WINDOW_BYTES = 512 * 2


class FakeIterator:
    """Stands in for silero_vad.VADIterator: replays scripted results, one per window."""

    def __init__(self, model, **kwargs):
        self.kwargs = kwargs
        self.script: list = []
        self.windows: list[np.ndarray] = []
        self.resets = 0

    def __call__(self, window, return_seconds=False):
        self.windows.append(window)
        return self.script.pop(0) if self.script else None

    def reset_states(self):
        self.resets += 1


@pytest.fixture
def vad(config, monkeypatch) -> VADModule:
    """A loaded VADModule whose torch and silero_vad imports are fakes."""
    torch = ModuleType("torch")
    torch.from_numpy = lambda array: array
    silero = ModuleType("silero_vad")
    silero.load_silero_vad = lambda: object()
    silero.VADIterator = FakeIterator
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "silero_vad", silero)

    module = VADModule(config)
    module.load()
    return module


def _pcm(samples: int) -> bytes:
    return np.full(samples, 1000, dtype="<i2").tobytes()


def test_load_passes_tunables_from_config(vad, config):
    assert vad._iterator.kwargs == {
        "threshold": config.VAD_THRESHOLD,
        "sampling_rate": 16000,
        "min_silence_duration_ms": config.VAD_MIN_SILENCE_MS,
        "speech_pad_ms": config.VAD_SPEECH_PAD_MS,
    }


def test_start_and_end_in_one_call_are_both_returned_in_order(vad):
    vad._iterator.script = [{"start": 0}, None, {"end": 1024}]

    events = vad.process_chunk(_pcm(512 * 3))

    assert events == [VADEvent.SPEECH_START, VADEvent.SPEECH_END]


def test_silence_returns_no_events(vad):
    assert vad.process_chunk(_pcm(512)) == []


def test_odd_length_chunk_is_ignored(vad):
    assert vad.process_chunk(b"\x01" * (WINDOW_BYTES + 1)) == []
    assert vad._iterator.windows == []


def test_empty_chunk_is_ignored(vad):
    assert vad.process_chunk(b"") == []


def test_unloaded_module_returns_no_events(config):
    assert VADModule(config).process_chunk(_pcm(512)) == []


def test_remainder_is_buffered_across_calls(vad):
    # 320-sample mic chunks (20 ms): the first window completes on the second call.
    vad._iterator.script = [{"start": 0}]

    assert vad.process_chunk(_pcm(320)) == []
    assert vad._iterator.windows == []
    assert vad.process_chunk(_pcm(320)) == [VADEvent.SPEECH_START]
    assert len(vad._iterator.windows) == 1
    assert len(vad._iterator.windows[0]) == 512

    # 128 samples are left over; 384 more complete the second window.
    vad.process_chunk(_pcm(384))
    assert len(vad._iterator.windows) == 2


def test_samples_are_normalised_to_float32(vad):
    vad.process_chunk(np.full(512, -32768, dtype="<i2").tobytes())

    window = vad._iterator.windows[0]
    assert window.dtype == np.float32
    assert np.all(window == -1.0)


def test_inference_error_is_skipped_not_raised(vad, monkeypatch):
    def boom(window, return_seconds=False):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(vad, "_iterator", boom)

    assert vad.process_chunk(_pcm(512)) == []


def test_reset_clears_the_remainder_and_the_model_state(vad):
    vad.process_chunk(_pcm(320))

    vad.reset()
    vad.process_chunk(_pcm(320))

    assert vad._iterator.resets == 1
    assert vad._iterator.windows == []
