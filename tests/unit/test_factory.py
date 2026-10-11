import pytest

from backend.config import AppConfig
from backend.conversation import factory
from backend.conversation.stt.groq_whisper import GroqWhisperSTT
from backend.conversation.factory import DEVICE_BACKENDS, create_stt, detect_device


def test_detect_device_returns_a_known_device():
    assert detect_device() in ("metal", "cuda", "cpu")


@pytest.mark.parametrize(
    ("system", "machine", "nvidia_smi", "expected"),
    [
        ("Darwin", "arm64", None, "metal"),
        ("Windows", "AMD64", "C:/nvidia-smi.exe", "cuda"),
        ("Linux", "x86_64", None, "cpu"),
        ("Darwin", "x86_64", None, "cpu"),
    ],
)
def test_detect_device(monkeypatch, system, machine, nvidia_smi, expected):
    monkeypatch.setattr(factory.platform, "system", lambda: system)
    monkeypatch.setattr(factory.platform, "machine", lambda: machine)
    monkeypatch.setattr(factory.shutil, "which", lambda name: nvidia_smi)

    assert detect_device() == expected


@pytest.mark.parametrize("device", ["metal", "cuda", "cpu"])
def test_each_device_maps_to_a_backend(device):
    assert DEVICE_BACKENDS[device] == "groq_whisper"

    stt = create_stt(AppConfig(GROQ_API_KEY="test-key", DEVICE=device))

    assert isinstance(stt, GroqWhisperSTT)


def test_auto_device_uses_detection(monkeypatch):
    monkeypatch.setattr(factory, "detect_device", lambda: "cuda")
    monkeypatch.setitem(factory.DEVICE_BACKENDS, "cuda", "fake_local")
    monkeypatch.setitem(factory._STT_BACKENDS, "fake_local", lambda config: "local-stt")

    assert create_stt(AppConfig(GROQ_API_KEY="test-key")) == "local-stt"


def test_stt_backend_override_wins(monkeypatch):
    monkeypatch.setitem(factory.DEVICE_BACKENDS, "cpu", "fake_local")
    monkeypatch.setitem(factory._STT_BACKENDS, "fake_local", lambda config: "local-stt")

    stt = create_stt(
        AppConfig(GROQ_API_KEY="test-key", DEVICE="cpu", STT_BACKEND="groq_whisper")
    )

    assert isinstance(stt, GroqWhisperSTT)


def test_unknown_backend_raises_value_error():
    with pytest.raises(ValueError, match="nope"):
        create_stt(AppConfig(GROQ_API_KEY="test-key", STT_BACKEND="nope"))
