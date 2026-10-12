"""
Builds the backend of every pipeline stage — the one place that says what runs.

Each stage has a contract (`backend/core/contracts.py`) and one file per backend. To add a backend,
write its file and register it in the stage's table below; callers do not change.

STT goes through the Device Capability Router: detect the device once at startup
(`backend/runtime/device.py`) and map it to a backend name. `DEVICE` and `STT_BACKEND` override the detection.
STT is cloud in the current stack, so every device maps to `groq_whisper`; a local backend
drops in by adding it to both STT tables.

`openai_compatible` (LLM and STT) points the same client at another hosted API or at a
self-hosted server, from `.env` alone: `LLM_BACKEND`, `LLM_BASE_URL`, `LLM_API_KEY`,
`LLM_MODEL` (and the `STT_*` equivalents).

`databricks` (LLM) calls the Databricks AI Gateway through the OpenAI Responses API:
`DATABRICKS_BASE_URL`, `DATABRICKS_TOKEN`, `LLM_MODEL`.
"""
from typing import Callable

from backend.config import AUTO, DATABRICKS, OPENAI_COMPATIBLE, AppConfig
from backend.core.contracts import LLM, STT, TTS
from backend.providers.llm.openai_compatible import OpenAICompatibleLLM
from backend.providers.llm.openai_responses import OpenAIResponsesLLM
from backend.providers.stt.openai_compatible import OpenAICompatibleSTT
from backend.providers.tts.zerotts import ZeroTTSBackend
from backend.runtime.device import detect_device
from backend.utils.logging import get_logger

logger = get_logger("providers")

# The OpenAI client refuses an empty key; a self-hosted server that needs none ignores it.
_NO_KEY = "not-needed"

# ── STT ───────────────────────────────────────────────────────────────────

DEVICE_BACKENDS: dict[str, str] = {
    "metal": "groq_whisper",
    "cuda": "groq_whisper",
    "cpu": "groq_whisper",
}

_STT_BACKENDS: dict[str, Callable[[AppConfig], STT]] = {
    "groq_whisper": lambda config: OpenAICompatibleSTT(
        config, base_url=config.GROQ_BASE_URL, api_key=config.GROQ_API_KEY
    ),
    OPENAI_COMPATIBLE: lambda config: OpenAICompatibleSTT(
        config, base_url=config.STT_BASE_URL, api_key=config.STT_API_KEY or _NO_KEY
    ),
}


def create_stt(config: AppConfig) -> STT:
    """Build the STT backend: `STT_BACKEND` if set, else the one mapped to the device."""
    device = detect_device() if config.DEVICE == AUTO else config.DEVICE
    name = DEVICE_BACKENDS[device] if config.STT_BACKEND == AUTO else config.STT_BACKEND
    if name not in _STT_BACKENDS:
        raise ValueError(f"Unknown STT_BACKEND: {name}")

    logger.info({"msg": "STT backend selected", "device": device, "backend": name})
    return _STT_BACKENDS[name](config)


# ── LLM ───────────────────────────────────────────────────────────────────

_LLM_BACKENDS: dict[str, Callable[[AppConfig], LLM]] = {
    "groq": lambda config: OpenAICompatibleLLM(
        config,
        base_url=config.GROQ_BASE_URL,
        api_key=config.GROQ_API_KEY,
        # Groq-only: keep the reasoning short and out of the answer stream.
        extra_body={
            "reasoning_effort": config.LLM_REASONING_EFFORT,
            "include_reasoning": False,
        },
    ),
    OPENAI_COMPATIBLE: lambda config: OpenAICompatibleLLM(
        config, base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY or _NO_KEY
    ),
    DATABRICKS: lambda config: OpenAIResponsesLLM(
        config, base_url=config.DATABRICKS_BASE_URL, api_key=config.DATABRICKS_TOKEN
    ),
}


def create_llm(config: AppConfig) -> LLM:
    """Build the LLM backend named by `LLM_BACKEND`."""
    if config.LLM_BACKEND not in _LLM_BACKENDS:
        raise ValueError(f"Unknown LLM_BACKEND: {config.LLM_BACKEND}")
    return _LLM_BACKENDS[config.LLM_BACKEND](config)


# ── TTS ───────────────────────────────────────────────────────────────────

_TTS_BACKENDS: dict[str, Callable[[AppConfig], TTS]] = {
    "zerotts": ZeroTTSBackend,
}


def create_tts(config: AppConfig) -> TTS:
    """Build the TTS backend named by `TTS_BACKEND`."""
    if config.TTS_BACKEND not in _TTS_BACKENDS:
        raise ValueError(f"Unknown TTS_BACKEND: {config.TTS_BACKEND}")
    return _TTS_BACKENDS[config.TTS_BACKEND](config)
