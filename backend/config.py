"""AppConfig — the one configuration object every module receives.

This file holds the secrets and the values that depend on the machine or the provider.
Behaviour parameters are in `backend/tuning.py` and texts in `backend/prompts.py`;
AppConfig inherits both, so a module reads any of them as `config.X`. Every key of this
file and of `tuning.py` can be set in the environment (`.env`); only `groq_api_key` has
to be. Texts cannot: `prompts.py` is their only source.
"""
import math
from dataclasses import dataclass

from dotenv import load_dotenv

from backend.prompts import Prompts
from backend.tuning import Tuning
from backend.utils.env import env_int, env_str

load_dotenv()

DEVICES = ("metal", "cuda", "cpu")
AUTO = "auto"
# Backend name for any OpenAI-compatible endpoint other than Groq (LLM and STT).
OPENAI_COMPATIBLE = "openai_compatible"
# Backend name of the Databricks AI Gateway (LLM and STT).
DATABRICKS = "databricks"
# Embedding backend computed locally, with no model (providers/embedding/local_hash.py).
LOCAL_HASH = "local_hash"
# Embedding backend of a local Ollama server.
OLLAMA = "ollama"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
# How a mail is sent: to the backend log (nothing leaves the machine), or by SMTP.
MAIL_LOG, MAIL_SMTP = "log", "smtp"
# How the connection to the SMTP server is protected.
SMTP_STARTTLS, SMTP_SSL, SMTP_PLAIN = "starttls", "ssl", "none"


@dataclass
class AppConfig(Tuning, Prompts):
    # ── Server ──────────────────────────────────────────────────────────
    HOST: str = env_str("HOST", "0.0.0.0")
    PORT: int = env_int("PORT", 8000)
    LOG_LEVEL: str = env_str("LOG_LEVEL", "INFO", normalize=str.upper)

    # ── Observability ───────────────────────────────────────────────────
    SERVICE_NAME: str = env_str("SERVICE_NAME", "avatar-ai")
    # Root of an OTLP/HTTP backend (`http://host:4318`); traces and metrics are sent to
    # its /v1/traces and /v1/metrics. Empty = nothing is sent, spans are only logged.
    OTLP_ENDPOINT: str = env_str("OTLP_ENDPOINT", "")
    # Headers of the OTLP requests, `key=value,key=value` (an API key of the backend).
    OTLP_HEADERS: str = env_str("OTLP_HEADERS", repr=False)

    # Full capture of camera frames, utterance and answer audio and turn traces on local
    # disk (backend/utils/capture.py). 1 = on. It stores faces and voices: off by default.
    CAPTURE_ENABLED: int = env_int("CAPTURE_ENABLED", 0)
    CAPTURE_DIR: str = env_str("CAPTURE_DIR", "backend/data/capture")

    # ── Groq (STT + LLM) ────────────────────────────────────────────────
    # The env name is lowercase `groq_api_key` (CLAUDE.md §3); the SDK default is not used.
    GROQ_API_KEY: str = env_str("groq_api_key", repr=False)
    GROQ_BASE_URL: str = env_str("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

    # ── Device Capability Router ────────────────────────────────────────
    DEVICE: str = env_str("DEVICE", AUTO, normalize=str.lower)

    # ── Models and backends ─────────────────────────────────────────────
    # Backend names are the entries of `backend/providers/catalog.py`.
    # auto (picked from the device) | groq_whisper | openai_compatible | databricks
    STT_BACKEND: str = env_str("STT_BACKEND", AUTO, normalize=str.lower)
    STT_MODEL: str = env_str("STT_MODEL", "whisper-large-v3-turbo")
    # The backend that takes over when the one above fails; empty = no backup.
    STT_BACKEND_BACKUP: str = env_str("STT_BACKEND_BACKUP", "", normalize=str.lower)
    # groq | openai_compatible | databricks
    LLM_BACKEND: str = env_str("LLM_BACKEND", "groq", normalize=str.lower)
    LLM_MODEL: str = env_str("LLM_MODEL", "openai/gpt-oss-20b")
    LLM_BACKEND_BACKUP: str = env_str("LLM_BACKEND_BACKUP", "", normalize=str.lower)
    TTS_BACKEND: str = env_str("TTS_BACKEND", "zerotts", normalize=str.lower)
    # A Hugging Face repo id, or a local directory holding the weights.
    ZEROTTS_REPO: str = env_str("ZEROTTS_REPO", "zeroweight-ai/ZeroTTS")

    # ── Endpoints of the `openai_compatible` backends ───────────────────
    # Another hosted API (OpenRouter, ...) or a self-hosted server (vLLM, Ollama, ...).
    # The key may stay empty for a server that needs none.
    LLM_BASE_URL: str = env_str("LLM_BASE_URL", "")
    LLM_API_KEY: str = env_str("LLM_API_KEY", repr=False)
    STT_BASE_URL: str = env_str("STT_BASE_URL", "")
    STT_API_KEY: str = env_str("STT_API_KEY", repr=False)

    # ── Databricks AI Gateway (LLM_BACKEND / STT_BACKEND = databricks) ──
    # The gateway root, `https://<workspace>/ai-gateway/mlflow/v1`.
    DATABRICKS_BASE_URL: str = env_str("DATABRICKS_BASE_URL", "")
    # Credential: a static token, or else the Databricks CLI profile to take one from
    # (`databricks auth login --profile <name>`).
    DATABRICKS_TOKEN: str = env_str("DATABRICKS_TOKEN", repr=False)
    DATABRICKS_PROFILE: str = env_str("DATABRICKS_PROFILE", "")
    # The gateway's own models; LLM_MODEL and STT_MODEL belong to the other backends.
    DATABRICKS_LLM_MODEL: str = env_str(
        "DATABRICKS_LLM_MODEL", "nonprod_ai.tsfai.glm-5-3-flash-dev-tai"
    )
    DATABRICKS_STT_MODEL: str = env_str(
        "DATABRICKS_STT_MODEL", "nonprod_ai.tsfai.gemini-3-6-flash-dev-tai"
    )

    # ── Knowledge base (uploaded documents) ─────────────────────────────
    # The SQLite file holding the documents, their chunks and the vectors.
    KNOWLEDGE_DB_PATH: str = env_str("KNOWLEDGE_DB_PATH", "backend/data/knowledge.db")
    # local_hash (no model, matches words) | openai_compatible | databricks | ollama
    EMBEDDING_BACKEND: str = env_str("EMBEDDING_BACKEND", LOCAL_HASH, normalize=str.lower)
    # The backend that takes over when the one above fails; empty = no backup. It must
    # serve the same model: its vectors are compared with the primary's in the index.
    EMBEDDING_BACKEND_BACKUP: str = env_str("EMBEDDING_BACKEND_BACKUP", "", normalize=str.lower)
    # Used when EMBEDDING_BACKEND=openai_compatible: any `/embeddings` API.
    EMBEDDING_BASE_URL: str = env_str("EMBEDDING_BASE_URL", "")
    EMBEDDING_API_KEY: str = env_str("EMBEDDING_API_KEY", repr=False)
    EMBEDDING_MODEL: str = env_str("EMBEDDING_MODEL", "")
    # Used when EMBEDDING_BACKEND=databricks: the workspace's serving endpoints root,
    # `https://<workspace>/serving-endpoints` (not the AI Gateway URL), with the
    # Databricks credential above.
    DATABRICKS_SERVING_BASE_URL: str = env_str("DATABRICKS_SERVING_BASE_URL", "")
    DATABRICKS_EMBEDDING_MODEL: str = env_str(
        "DATABRICKS_EMBEDDING_MODEL", "databricks-qwen3-embedding-0-6b"
    )
    # Used when an embedding backend is `ollama`: the server's OpenAI-compatible root.
    # The default model is the one the Databricks endpoint above serves.
    OLLAMA_BASE_URL: str = env_str("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    OLLAMA_EMBEDDING_MODEL: str = env_str("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    # Origins allowed to call the HTTP API from a browser, comma-separated; * = any.
    CORS_ALLOW_ORIGINS: str = env_str("CORS_ALLOW_ORIGINS", "*")

    # ── Application database (users, memory) ────────────────────────────
    # A SQLAlchemy URL. A SQLite file locally; PostgreSQL at deployment. It may hold a
    # password, so it stays out of repr(config).
    DATABASE_URL: str = env_str("DATABASE_URL", "sqlite:///backend/data/app.db", repr=False)
    # ── Sign-in ─────────────────────────────────────────────────────────
    # Development and tests: the id of the user every connection and request belongs
    # to, without sign-in. Empty = the session token decides (no token = a guest).
    AUTH_DEV_USER_ID: str = env_str("AUTH_DEV_USER_ID", "")
    # How the code that proves an email address is sent: `log` writes the mail to the
    # backend log (a machine without a mail server; it proves nothing about who owns
    # the address), `smtp` sends it.
    MAIL_BACKEND: str = env_str("MAIL_BACKEND", MAIL_LOG, normalize=str.lower)
    SMTP_HOST: str = env_str("SMTP_HOST", "")
    SMTP_PORT: int = env_int("SMTP_PORT", 587)
    SMTP_USERNAME: str = env_str("SMTP_USERNAME", "")
    SMTP_PASSWORD: str = env_str("SMTP_PASSWORD", repr=False)
    # The address the mails come from.
    SMTP_FROM: str = env_str("SMTP_FROM", "")
    # starttls (port 587), ssl (port 465) or none.
    SMTP_SECURITY: str = env_str("SMTP_SECURITY", SMTP_STARTTLS, normalize=str.lower)
    # External providers. One whose client id is empty is not offered. The application
    # is registered at the provider, with `<AUTH_PUBLIC_URL>/auth/<provider>/callback`
    # as its redirect URI.
    AUTH_GOOGLE_CLIENT_ID: str = env_str("AUTH_GOOGLE_CLIENT_ID", "")
    AUTH_GOOGLE_CLIENT_SECRET: str = env_str("AUTH_GOOGLE_CLIENT_SECRET", repr=False)
    AUTH_FACEBOOK_CLIENT_ID: str = env_str("AUTH_FACEBOOK_CLIENT_ID", "")
    AUTH_FACEBOOK_CLIENT_SECRET: str = env_str("AUTH_FACEBOOK_CLIENT_SECRET", repr=False)
    # The address of this backend as the browser reaches it (the provider sends the
    # person back to it), and the address of the page they return to. No trailing slash.
    AUTH_PUBLIC_URL: str = env_str("AUTH_PUBLIC_URL", "", normalize=lambda url: url.rstrip("/"))
    AUTH_PAGE_URL: str = env_str("AUTH_PAGE_URL", "", normalize=lambda url: url.rstrip("/"))
    # Where each provider is asked: the person is sent to `AUTHORIZE`, the code is
    # exchanged at `TOKEN`, and `USERINFO` says who they are.
    AUTH_GOOGLE_AUTHORIZE_URL: str = env_str(
        "AUTH_GOOGLE_AUTHORIZE_URL", "https://accounts.google.com/o/oauth2/v2/auth"
    )
    AUTH_GOOGLE_TOKEN_URL: str = env_str(
        "AUTH_GOOGLE_TOKEN_URL", "https://oauth2.googleapis.com/token"
    )
    AUTH_GOOGLE_USERINFO_URL: str = env_str(
        "AUTH_GOOGLE_USERINFO_URL", "https://openidconnect.googleapis.com/v1/userinfo"
    )
    AUTH_GOOGLE_SCOPE: str = env_str("AUTH_GOOGLE_SCOPE", "openid email profile")
    AUTH_FACEBOOK_AUTHORIZE_URL: str = env_str(
        "AUTH_FACEBOOK_AUTHORIZE_URL", "https://www.facebook.com/v19.0/dialog/oauth"
    )
    AUTH_FACEBOOK_TOKEN_URL: str = env_str(
        "AUTH_FACEBOOK_TOKEN_URL", "https://graph.facebook.com/v19.0/oauth/access_token"
    )
    AUTH_FACEBOOK_USERINFO_URL: str = env_str(
        "AUTH_FACEBOOK_USERINFO_URL", "https://graph.facebook.com/v19.0/me?fields=id,name,email"
    )
    AUTH_FACEBOOK_SCOPE: str = env_str("AUTH_FACEBOOK_SCOPE", "public_profile email")
    # The directory of the object store: the original bytes of what users import. Who
    # owns an object is recorded in the application database only.
    OBJECT_STORE_DIR: str = env_str("OBJECT_STORE_DIR", "backend/data/objects")

    # ── Camera ──────────────────────────────────────────────────────────
    CAMERA_INDEX: int = env_int("CAMERA_INDEX", 0)
    CAMERA_URL: str = env_str("CAMERA_URL", "")
    CAMERA_ROTATE: int = env_int("CAMERA_ROTATE", 0)
    CAMERA_WIDTH: int = env_int("CAMERA_WIDTH", 1280)
    CAMERA_HEIGHT: int = env_int("CAMERA_HEIGHT", 720)

    def validate(self) -> None:
        # A backup backend needs its settings as much as the primary does.
        llm_backends = {self.LLM_BACKEND, self.LLM_BACKEND_BACKUP} - {""}
        stt_backends = {self.STT_BACKEND, self.STT_BACKEND_BACKUP} - {""}
        # `auto` STT resolves to Groq Whisper on every device (providers/catalog.py).
        uses_groq = "groq" in llm_backends or stt_backends & {AUTO, "groq_whisper"}
        if uses_groq and not self.GROQ_API_KEY:
            raise ValueError("groq_api_key is required. Set it in .env file.")
        if OPENAI_COMPATIBLE in llm_backends and not self.LLM_BASE_URL:
            raise ValueError("LLM_BASE_URL is required when LLM_BACKEND=openai_compatible.")
        if DATABRICKS in llm_backends | stt_backends:
            if not self.DATABRICKS_BASE_URL:
                raise ValueError("DATABRICKS_BASE_URL is required by the databricks backends.")
            if not (self.DATABRICKS_TOKEN or self.DATABRICKS_PROFILE):
                raise ValueError(
                    "DATABRICKS_TOKEN or DATABRICKS_PROFILE is required by the databricks backends."
                )
        if OPENAI_COMPATIBLE in stt_backends and not self.STT_BASE_URL:
            raise ValueError("STT_BASE_URL is required when STT_BACKEND=openai_compatible.")
        embedding_backends = {self.EMBEDDING_BACKEND, self.EMBEDDING_BACKEND_BACKUP} - {""}
        if OPENAI_COMPATIBLE in embedding_backends:
            if not (self.EMBEDDING_BASE_URL and self.EMBEDDING_MODEL):
                raise ValueError(
                    "EMBEDDING_BASE_URL and EMBEDDING_MODEL are required when "
                    "EMBEDDING_BACKEND=openai_compatible."
                )
        if DATABRICKS in embedding_backends:
            if not self.DATABRICKS_SERVING_BASE_URL:
                raise ValueError(
                    "DATABRICKS_SERVING_BASE_URL is required when EMBEDDING_BACKEND=databricks."
                )
            if not (self.DATABRICKS_TOKEN or self.DATABRICKS_PROFILE):
                raise ValueError(
                    "DATABRICKS_TOKEN or DATABRICKS_PROFILE is required by the databricks backends."
                )
        if self.KNOWLEDGE_CHUNK_OVERLAP_CHARS >= self.KNOWLEDGE_CHUNK_CHARS:
            raise ValueError("KNOWLEDGE_CHUNK_OVERLAP_CHARS must be below KNOWLEDGE_CHUNK_CHARS.")
        if self.KNOWLEDGE_QUERY_HISTORY_TURNS < 0:
            raise ValueError("KNOWLEDGE_QUERY_HISTORY_TURNS must not be negative.")
        # NaN or infinite: the rule would never fire and the conversation never close.
        for key in ("CONVERSATION_ABSENCE_SEC", "CONVERSATION_IDLE_SEC"):
            value = getattr(self, key)
            if not (math.isfinite(value) and value > 0):
                raise ValueError(
                    "CONVERSATION_ABSENCE_SEC and CONVERSATION_IDLE_SEC must be positive "
                    "and finite."
                )
        if self.MEMORY_HISTORY_MAX_TOKENS < 1:
            raise ValueError("MEMORY_HISTORY_MAX_TOKENS must be at least 1.")
        ratio = self.MEMORY_HISTORY_CHARS_PER_TOKEN
        if not (math.isfinite(ratio) and ratio > 0):
            raise ValueError("MEMORY_HISTORY_CHARS_PER_TOKEN must be positive and finite.")
        if self.MEMORY_PROMPT_MAX_ITEMS < 1:
            raise ValueError("MEMORY_PROMPT_MAX_ITEMS must be at least 1.")
        if self.MEMORY_ITEM_MAX_CHARS < 1:
            raise ValueError("MEMORY_ITEM_MAX_CHARS must be at least 1.")
        if self.MAIL_BACKEND not in (MAIL_LOG, MAIL_SMTP):
            raise ValueError(f"Invalid MAIL_BACKEND: {self.MAIL_BACKEND}")
        if self.MAIL_BACKEND == MAIL_SMTP:
            if not (self.SMTP_HOST and self.SMTP_FROM):
                raise ValueError("SMTP_HOST and SMTP_FROM are required when MAIL_BACKEND=smtp.")
            if self.SMTP_SECURITY not in (SMTP_STARTTLS, SMTP_SSL, SMTP_PLAIN):
                raise ValueError(f"Invalid SMTP_SECURITY: {self.SMTP_SECURITY}")
        for key in ("AUTH_PASSWORD_MIN_CHARS", "AUTH_LOGIN_MAX_ATTEMPTS", "AUTH_OTP_MAX_ATTEMPTS"):
            if getattr(self, key) < 1:
                raise ValueError(f"{key} must be at least 1.")
        for key in (
            "AUTH_SESSION_IDLE_MINUTES",
            "AUTH_SESSION_MAX_HOURS",
            "AUTH_LOGIN_WINDOW_MINUTES",
            "AUTH_SESSION_CHECK_SEC",
            "AUTH_PROVIDER_TIMEOUT_SEC",
            "AUTH_OTP_TTL_MINUTES",
            "AUTH_OTP_RESEND_SEC",
            "SMTP_TIMEOUT_SEC",
        ):
            value = getattr(self, key)
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"{key} must be positive and finite.")
        for provider in ("GOOGLE", "FACEBOOK"):
            if not getattr(self, f"AUTH_{provider}_CLIENT_ID"):
                continue
            if not getattr(self, f"AUTH_{provider}_CLIENT_SECRET"):
                raise ValueError(
                    f"AUTH_{provider}_CLIENT_SECRET is required with AUTH_{provider}_CLIENT_ID."
                )
            if not (self.AUTH_PUBLIC_URL and self.AUTH_PAGE_URL):
                raise ValueError(
                    "AUTH_PUBLIC_URL and AUTH_PAGE_URL are required by the external "
                    "sign-in providers."
                )
        for key in ("MEMORY_TIMEOUT_SEC", "PERSONA_TIMEOUT_SEC"):
            value = getattr(self, key)
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"{key} must be positive and finite.")
        if not self.DATABASE_URL.strip():
            raise ValueError("DATABASE_URL must not be empty.")
        if not self.OBJECT_STORE_DIR.strip():
            raise ValueError("OBJECT_STORE_DIR must not be empty.")
        for key in (
            "LLM_COMPLETE_TIMEOUT_SEC", "MEMORY_IMPORT_MAX_FILE_MB", "MEMORY_TRANSCRIPT_TTL_DAYS"
        ):
            value = getattr(self, key)
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"{key} must be positive and finite.")
        for key in (
            "LLM_COMPLETE_MAX_TOKENS",
            "MEMORY_IMPORT_MAX_CHARS",
            "MEMORY_PROMOTE_SEEN_COUNT",
            "MEMORY_CONSOLIDATE_MAX_CHARS",
            "PERSONA_FIELD_MAX_CHARS",
            "PERSONA_IMPORT_MAX_CHARS",
        ):
            if getattr(self, key) < 1:
                raise ValueError(f"{key} must be at least 1.")
        if self.MEMORY_EXTRACT_MAX_REJECTED < 0:
            raise ValueError("MEMORY_EXTRACT_MAX_REJECTED must not be negative.")
        if not 0.0 <= self.MEMORY_MIN_STT_CONFIDENCE <= 1.0:
            raise ValueError("MEMORY_MIN_STT_CONFIDENCE must be between 0 and 1.")
        if not 0.0 <= self.STT_NO_SPEECH_MAX_PROB <= 1.0:
            raise ValueError("STT_NO_SPEECH_MAX_PROB must be between 0 and 1.")
        if self.DEVICE != AUTO and self.DEVICE not in DEVICES:
            raise ValueError(f"Invalid DEVICE: {self.DEVICE}")
        if self.LOG_LEVEL not in LOG_LEVELS:
            raise ValueError(f"Invalid LOG_LEVEL: {self.LOG_LEVEL}")
        if self.TRACKING_FPS <= 0:
            raise ValueError("TRACKING_FPS must be positive.")
        if self.TTS_CHUNK_MS <= 0:
            raise ValueError("TTS_CHUNK_MS must be positive.")
