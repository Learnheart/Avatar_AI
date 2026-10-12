import pytest

from backend.config import AppConfig
from backend.providers import catalog
from backend.providers.catalog import DEVICE_BACKENDS, create_llm, create_stt
from backend.providers.llm import openai_compatible as llm_module
from backend.providers.llm import openai_responses as responses_module
from backend.providers.llm.openai_compatible import OpenAICompatibleLLM
from backend.providers.llm.openai_responses import OpenAIResponsesLLM
from backend.providers.stt import openai_compatible as stt_module
from backend.providers.stt.openai_compatible import OpenAICompatibleSTT
from backend.runtime import device as runtime_device
from backend.runtime.device import detect_device


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
    monkeypatch.setattr(runtime_device.platform, "system", lambda: system)
    monkeypatch.setattr(runtime_device.platform, "machine", lambda: machine)
    monkeypatch.setattr(runtime_device.shutil, "which", lambda name: nvidia_smi)

    assert detect_device() == expected


@pytest.mark.parametrize("device", ["metal", "cuda", "cpu"])
def test_each_device_maps_to_a_backend(device):
    assert DEVICE_BACKENDS[device] == "groq_whisper"

    stt = create_stt(AppConfig(GROQ_API_KEY="test-key", DEVICE=device))

    assert isinstance(stt, OpenAICompatibleSTT)


def test_auto_device_uses_detection(monkeypatch):
    monkeypatch.setattr(catalog, "detect_device", lambda: "cuda")
    monkeypatch.setitem(catalog.DEVICE_BACKENDS, "cuda", "fake_local")
    monkeypatch.setitem(catalog._STT_BACKENDS, "fake_local", lambda config: "local-stt")

    assert create_stt(AppConfig(GROQ_API_KEY="test-key")) == "local-stt"


def test_stt_backend_override_wins(monkeypatch):
    monkeypatch.setitem(catalog.DEVICE_BACKENDS, "cpu", "fake_local")
    monkeypatch.setitem(catalog._STT_BACKENDS, "fake_local", lambda config: "local-stt")

    stt = create_stt(
        AppConfig(GROQ_API_KEY="test-key", DEVICE="cpu", STT_BACKEND="groq_whisper")
    )

    assert isinstance(stt, OpenAICompatibleSTT)


def test_unknown_backend_raises_value_error():
    with pytest.raises(ValueError, match="nope"):
        create_stt(AppConfig(GROQ_API_KEY="test-key", STT_BACKEND="nope"))


# ── OpenAI-compatible endpoints: another API or a self-hosted server ──────

class RecordingClient:
    """Stands in for AsyncOpenAI: keeps the endpoint it was built with."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def clients(monkeypatch):
    monkeypatch.setattr(llm_module, "AsyncOpenAI", RecordingClient)
    monkeypatch.setattr(responses_module, "AsyncOpenAI", RecordingClient)
    monkeypatch.setattr(stt_module, "AsyncOpenAI", RecordingClient)


def test_default_llm_is_groq_with_its_reasoning_parameters(clients, config):
    llm = create_llm(config)

    assert isinstance(llm, OpenAICompatibleLLM)
    assert llm._client.kwargs["base_url"] == config.GROQ_BASE_URL
    assert llm._client.kwargs["api_key"] == "test-key"
    assert llm._extra_body == {
        "reasoning_effort": config.LLM_REASONING_EFFORT,
        "include_reasoning": False,
    }


def test_openai_compatible_llm_uses_its_own_endpoint_and_no_groq_parameters(clients):
    llm = create_llm(
        AppConfig(
            LLM_BACKEND="openai_compatible",
            LLM_BASE_URL="http://llm.local/v1",
            LLM_API_KEY="other-key",
        )
    )

    assert llm._client.kwargs["base_url"] == "http://llm.local/v1"
    assert llm._client.kwargs["api_key"] == "other-key"
    assert llm._extra_body is None


def test_databricks_llm_uses_the_gateway_endpoint_and_token(clients):
    llm = create_llm(
        AppConfig(
            LLM_BACKEND="databricks",
            DATABRICKS_BASE_URL="https://dbx.local/ai-gateway/mlflow/v1",
            DATABRICKS_TOKEN="dapi-token",
        )
    )

    assert isinstance(llm, OpenAIResponsesLLM)
    assert llm._client.kwargs["base_url"] == "https://dbx.local/ai-gateway/mlflow/v1"
    assert llm._client.kwargs["api_key"] == "dapi-token"


def test_openai_compatible_stt_uses_its_own_endpoint(clients):
    stt = create_stt(
        AppConfig(
            STT_BACKEND="openai_compatible",
            STT_BASE_URL="http://stt.local/v1",
            STT_API_KEY="other-key",
        )
    )

    assert isinstance(stt, OpenAICompatibleSTT)
    assert stt._client.kwargs["base_url"] == "http://stt.local/v1"
    assert stt._client.kwargs["api_key"] == "other-key"


def test_self_hosted_endpoint_without_a_key_still_builds():
    # The real client: it rejects an empty key, so the catalog must not pass one.
    config = AppConfig(
        LLM_BACKEND="openai_compatible",
        LLM_BASE_URL="http://llm.local/v1",
        STT_BACKEND="openai_compatible",
        STT_BASE_URL="http://stt.local/v1",
    )

    assert isinstance(create_llm(config), OpenAICompatibleLLM)
    assert isinstance(create_stt(config), OpenAICompatibleSTT)


def test_unknown_llm_backend_raises_value_error():
    with pytest.raises(ValueError, match="nope"):
        create_llm(AppConfig(GROQ_API_KEY="test-key", LLM_BACKEND="nope"))
