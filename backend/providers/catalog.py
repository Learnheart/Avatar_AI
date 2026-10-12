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

`databricks` (LLM and STT) calls the Databricks AI Gateway: `DATABRICKS_BASE_URL`, a
credential (`DATABRICKS_TOKEN` or `DATABRICKS_PROFILE`) and the gateway's own models,
`DATABRICKS_LLM_MODEL` (Responses API) and `DATABRICKS_STT_MODEL` (a chat model that is
sent the utterance as audio). STT is never routed there by the device: set `STT_BACKEND`.

`STT_BACKEND_BACKUP` and `LLM_BACKEND_BACKUP` name a second backend of the same tables
that takes over when the first one fails (`backend/providers/fallback.py`).

The embedding of the knowledge base is one more stage (`EMBEDDING_BACKEND`): `local_hash`
needs nothing, `openai_compatible` takes `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY` and
`EMBEDDING_MODEL`, `databricks` a workspace endpoint (`DATABRICKS_SERVING_BASE_URL`,
`DATABRICKS_EMBEDDING_MODEL`), `ollama` a local Ollama server (`OLLAMA_BASE_URL`,
`OLLAMA_EMBEDDING_MODEL`). `EMBEDDING_BACKEND_BACKUP` names its backup, which must serve
the same model as the primary.
"""
from typing import Callable

from backend.config import AUTO, DATABRICKS, LOCAL_HASH, OLLAMA, OPENAI_COMPATIBLE, AppConfig
from backend.core.contracts import LLM, STT, TTS, Embedder
from backend.providers.databricks import databricks_api_key
from backend.providers.embedding.local_hash import LocalHashEmbedder
from backend.providers.embedding.openai_compatible import OpenAICompatibleEmbedder
from backend.providers.fallback import FallbackEmbedder, FallbackLLM, FallbackSTT
from backend.providers.llm.openai_compatible import OpenAICompatibleLLM
from backend.providers.llm.openai_responses import OpenAIResponsesLLM
from backend.providers.stt.chat_audio import ChatAudioSTT
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
    DATABRICKS: lambda config: ChatAudioSTT(
        config,
        base_url=config.DATABRICKS_BASE_URL,
        api_key=databricks_api_key(config),
        model=config.DATABRICKS_STT_MODEL,
    ),
}


def create_stt(config: AppConfig) -> STT:
    """Build the STT backend: `STT_BACKEND` if set, else the one mapped to the device;
    with `STT_BACKEND_BACKUP`, backed by a second one."""
    device = detect_device() if config.DEVICE == AUTO else config.DEVICE

    def resolve(name: str) -> str:
        name = DEVICE_BACKENDS[device] if name == AUTO else name
        if name not in _STT_BACKENDS:
            raise ValueError(f"Unknown STT_BACKEND: {name}")
        return name

    name = resolve(config.STT_BACKEND)
    backup = resolve(config.STT_BACKEND_BACKUP) if config.STT_BACKEND_BACKUP else ""
    logger.info(
        {"msg": "STT backend selected", "device": device, "backend": name, "backup": backup}
    )
    stt = _STT_BACKENDS[name](config)
    if backup and backup != name:
        return FallbackSTT(config, stt, _STT_BACKENDS[backup](config))
    return stt


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
        config,
        base_url=config.DATABRICKS_BASE_URL,
        api_key=databricks_api_key(config),
        model=config.DATABRICKS_LLM_MODEL,
        reasoning_effort=config.LLM_REASONING_EFFORT,
    ),
}


def create_llm(config: AppConfig) -> LLM:
    """Build the LLM backend named by `LLM_BACKEND`; with `LLM_BACKEND_BACKUP`, backed by
    a second one."""
    name, backup = config.LLM_BACKEND, config.LLM_BACKEND_BACKUP
    for backend in filter(None, (name, backup)):
        if backend not in _LLM_BACKENDS:
            raise ValueError(f"Unknown LLM_BACKEND: {backend}")

    logger.info({"msg": "LLM backend selected", "backend": name, "backup": backup})
    llm = _LLM_BACKENDS[name](config)
    if backup and backup != name:
        return FallbackLLM(config, llm, _LLM_BACKENDS[backup](config))
    return llm


# ── TTS ───────────────────────────────────────────────────────────────────

_TTS_BACKENDS: dict[str, Callable[[AppConfig], TTS]] = {
    "zerotts": ZeroTTSBackend,
}


def create_tts(config: AppConfig) -> TTS:
    """Build the TTS backend named by `TTS_BACKEND`."""
    if config.TTS_BACKEND not in _TTS_BACKENDS:
        raise ValueError(f"Unknown TTS_BACKEND: {config.TTS_BACKEND}")
    return _TTS_BACKENDS[config.TTS_BACKEND](config)


# ── Embedding (knowledge base) ────────────────────────────────────────────

_EMBEDDING_BACKENDS: dict[str, Callable[[AppConfig], Embedder]] = {
    LOCAL_HASH: LocalHashEmbedder,
    OPENAI_COMPATIBLE: lambda config: OpenAICompatibleEmbedder(
        config,
        base_url=config.EMBEDDING_BASE_URL,
        api_key=config.EMBEDDING_API_KEY or _NO_KEY,
        model=config.EMBEDDING_MODEL,
    ),
    DATABRICKS: lambda config: OpenAICompatibleEmbedder(
        config,
        base_url=config.DATABRICKS_SERVING_BASE_URL,
        api_key=databricks_api_key(config),
        model=config.DATABRICKS_EMBEDDING_MODEL,
    ),
    OLLAMA: lambda config: OpenAICompatibleEmbedder(
        config,
        base_url=config.OLLAMA_BASE_URL,
        api_key=_NO_KEY,
        model=config.OLLAMA_EMBEDDING_MODEL,
    ),
}


def create_embedder(config: AppConfig) -> Embedder:
    """Build the embedding backend named by `EMBEDDING_BACKEND`; with
    `EMBEDDING_BACKEND_BACKUP`, backed by a second one serving the same model."""
    name, backup = config.EMBEDDING_BACKEND, config.EMBEDDING_BACKEND_BACKUP
    for backend in filter(None, (name, backup)):
        if backend not in _EMBEDDING_BACKENDS:
            raise ValueError(f"Unknown EMBEDDING_BACKEND: {backend}")

    logger.info({"msg": "Embedding backend selected", "backend": name, "backup": backup})
    embedder = _EMBEDDING_BACKENDS[name](config)
    if backup and backup != name:
        return FallbackEmbedder(config, embedder, _EMBEDDING_BACKENDS[backup](config))
    return embedder
