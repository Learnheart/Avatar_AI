import pytest

from backend.config import AppConfig


def test_defaults_load():
    config = AppConfig()

    assert config.GROQ_BASE_URL == "https://api.groq.com/openai/v1"
    assert config.DEVICE == "auto"
    assert config.STT_BACKEND == "auto"
    assert config.STT_MODEL == "whisper-large-v3-turbo"
    assert config.STT_LANGUAGE == "vi"
    assert config.STT_TIMEOUT_SEC == 8
    assert config.STT_MAX_UTTERANCE_SEC == 30
    assert config.LLM_MODEL == "openai/gpt-oss-20b"
    assert config.LLM_REASONING_EFFORT == "low"
    assert config.LLM_MAX_TOKENS == 512
    assert config.LLM_TIMEOUT_SEC == 8
    assert config.LLM_FALLBACK_TEXT
    assert config.TTS_BACKEND == "zerotts"
    assert config.ZEROTTS_REPO == "zeroweight-ai/ZeroTTS"
    assert config.ZEROTTS_VOICE == "maichi"
    assert config.TTS_CHUNK_MS == 240
    assert config.TTS_TIMEOUT_SEC == 10
    assert config.VAD_MIN_SILENCE_MS == 500
    assert config.VAD_SPEECH_PAD_MS == 100
    assert config.VAD_PREROLL_MS == 300
    assert config.TURN_TIMEOUT_SEC == 30
    assert config.PLAYBACK_END_TIMEOUT_SEC == 2
    assert config.LOG_LEVEL == "INFO"
    assert config.TRACKING_FPS == 30
    assert config.CONVERSATION_ABSENCE_SEC == 30
    assert config.CONVERSATION_IDLE_SEC == 120
    assert config.MEMORY_HISTORY_MAX_TOKENS == 2000
    assert config.MEMORY_HISTORY_CHARS_PER_TOKEN == 3.0
    assert config.KNOWLEDGE_QUERY_HISTORY_TURNS == 1
    assert config.STT_NO_SPEECH_MAX_PROB == 1.0
    assert config.DATABASE_URL == "sqlite:///backend/data/app.db"
    assert config.AUTH_DEV_USER_ID == ""
    assert config.MEMORY_PROMPT_MAX_ITEMS == 20
    assert config.MEMORY_TIMEOUT_SEC == 1.0
    assert config.MEMORY_ITEM_MAX_CHARS == 200
    assert config.OBJECT_STORE_DIR == "backend/data/objects"
    assert config.LLM_COMPLETE_TIMEOUT_SEC == 30
    assert config.LLM_COMPLETE_MAX_TOKENS == 2048
    assert config.MEMORY_IMPORT_MAX_FILE_MB == 5
    assert config.MEMORY_IMPORT_MAX_CHARS == 20000
    assert config.MEMORY_PROMOTE_SEEN_COUNT == 2
    assert config.MEMORY_MIN_STT_CONFIDENCE == 0.6
    assert config.MEMORY_TRANSCRIPT_TTL_DAYS == 7
    assert config.MEMORY_CONSOLIDATE_MAX_CHARS == 20000
    assert config.MEMORY_EXTRACT_MAX_REJECTED == 50


def test_missing_groq_api_key_fails_validation():
    with pytest.raises(ValueError, match="groq_api_key"):
        AppConfig().validate()


def test_groq_api_key_is_read_from_lowercase_name(monkeypatch):
    monkeypatch.setenv("groq_api_key", "gsk-from-env")

    config = AppConfig()

    assert config.GROQ_API_KEY == "gsk-from-env"
    config.validate()


def test_env_overrides_apply(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "some/other-model")
    monkeypatch.setenv("TTS_CHUNK_MS", "120")
    monkeypatch.setenv("VAD_THRESHOLD", "0.7")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    config = AppConfig()

    assert config.LLM_MODEL == "some/other-model"
    assert config.TTS_CHUNK_MS == 120
    assert config.VAD_THRESHOLD == 0.7
    assert config.LOG_LEVEL == "DEBUG"


def test_unparseable_number_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("TTS_CHUNK_MS", "not-a-number")

    assert AppConfig().TTS_CHUNK_MS == 240


@pytest.mark.parametrize(
    "overrides",
    [{"DEVICE": "quantum"}, {"LOG_LEVEL": "LOUD"}, {"TRACKING_FPS": 0}, {"TTS_CHUNK_MS": 0}],
)
def test_invalid_values_fail_validation(overrides):
    with pytest.raises(ValueError):
        AppConfig(GROQ_API_KEY="test-key", **overrides).validate()


@pytest.mark.parametrize(
    "overrides",
    [
        {"CONVERSATION_ABSENCE_SEC": 0},
        {"CONVERSATION_ABSENCE_SEC": -5},
        {"CONVERSATION_IDLE_SEC": 0},
        {"CONVERSATION_IDLE_SEC": -1},
        {"CONVERSATION_ABSENCE_SEC": float("nan")},
        {"CONVERSATION_ABSENCE_SEC": float("inf")},
        {"CONVERSATION_IDLE_SEC": float("nan")},
        {"CONVERSATION_IDLE_SEC": float("inf")},
        {"MEMORY_HISTORY_MAX_TOKENS": 0},
        {"MEMORY_HISTORY_CHARS_PER_TOKEN": 0},
        {"MEMORY_HISTORY_CHARS_PER_TOKEN": -1.0},
        {"MEMORY_HISTORY_CHARS_PER_TOKEN": float("nan")},
        {"MEMORY_HISTORY_CHARS_PER_TOKEN": float("inf")},
        {"KNOWLEDGE_QUERY_HISTORY_TURNS": -1},
        {"STT_NO_SPEECH_MAX_PROB": 1.5},
        {"STT_NO_SPEECH_MAX_PROB": -0.1},
        {"MEMORY_PROMPT_MAX_ITEMS": 0},
        {"MEMORY_ITEM_MAX_CHARS": 0},
        {"MEMORY_TIMEOUT_SEC": 0},
        {"MEMORY_TIMEOUT_SEC": -1},
        {"MEMORY_TIMEOUT_SEC": float("nan")},
        {"MEMORY_TIMEOUT_SEC": float("inf")},
        {"PERSONA_TIMEOUT_SEC": 0},
        {"AUTH_PASSWORD_MIN_CHARS": 0},
        {"AUTH_LOGIN_MAX_ATTEMPTS": 0},
        {"AUTH_SESSION_IDLE_MINUTES": 0},
        {"AUTH_SESSION_IDLE_MINUTES": float("nan")},
        {"AUTH_SESSION_MAX_HOURS": -1},
        {"AUTH_SESSION_MAX_HOURS": float("inf")},
        {"AUTH_LOGIN_WINDOW_MINUTES": 0},
        {"AUTH_SESSION_CHECK_SEC": 0},
        {"AUTH_SESSION_CLEANUP_MINUTES": 0},
        {"AUTH_PROVIDER_TIMEOUT_SEC": 0},
        {"AUTH_OTP_TTL_MINUTES": 0},
        {"AUTH_OTP_MAX_ATTEMPTS": 0},
        {"AUTH_OTP_RESEND_SEC": 0},
        {"AUTH_OTP_WINDOW_MINUTES": 0},
        {"AUTH_OTP_WINDOW_MINUTES": float("inf")},
        {"AUTH_OTP_MAX_CODES": 0},
        {"AUTH_OTP_MAX_MAILS": 0},
        {"SMTP_TIMEOUT_SEC": 0},
        {"MAIL_BACKEND": "pigeon"},
        {"PERSONA_TIMEOUT_SEC": float("nan")},
        {"PERSONA_TIMEOUT_SEC": float("inf")},
        {"PERSONA_FIELD_MAX_CHARS": 0},
        {"PERSONA_IMPORT_MAX_CHARS": 0},
        {"DATABASE_URL": " "},
        {"OBJECT_STORE_DIR": " "},
        {"LLM_COMPLETE_TIMEOUT_SEC": 0},
        {"LLM_COMPLETE_TIMEOUT_SEC": -1},
        {"LLM_COMPLETE_TIMEOUT_SEC": float("nan")},
        {"LLM_COMPLETE_TIMEOUT_SEC": float("inf")},
        {"LLM_COMPLETE_MAX_TOKENS": 0},
        {"MEMORY_IMPORT_MAX_FILE_MB": 0},
        {"MEMORY_IMPORT_MAX_FILE_MB": -1},
        {"MEMORY_IMPORT_MAX_FILE_MB": float("nan")},
        {"MEMORY_IMPORT_MAX_CHARS": 0},
        {"MEMORY_PROMOTE_SEEN_COUNT": 0},
        {"MEMORY_MIN_STT_CONFIDENCE": -0.1},
        {"MEMORY_MIN_STT_CONFIDENCE": 1.1},
        {"MEMORY_MIN_STT_CONFIDENCE": float("nan")},
        {"MEMORY_TRANSCRIPT_TTL_DAYS": 0},
        {"MEMORY_TRANSCRIPT_TTL_DAYS": -1},
        {"MEMORY_TRANSCRIPT_TTL_DAYS": float("inf")},
        {"MEMORY_CONSOLIDATE_MAX_CHARS": 0},
        {"MEMORY_EXTRACT_MAX_REJECTED": -1},
    ],
)
def test_invalid_conversation_settings_fail_validation(overrides):
    (key,) = overrides
    with pytest.raises(ValueError, match=key):
        AppConfig(GROQ_API_KEY="test-key", **overrides).validate()


@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        ({"AUTH_GOOGLE_CLIENT_ID": "id"}, "AUTH_GOOGLE_CLIENT_SECRET"),
        ({"AUTH_FACEBOOK_CLIENT_ID": "id"}, "AUTH_FACEBOOK_CLIENT_SECRET"),
        ({"AUTH_GOOGLE_CLIENT_ID": "id", "AUTH_GOOGLE_CLIENT_SECRET": "s"}, "AUTH_PUBLIC_URL"),
        ({"AUTH_FACEBOOK_CLIENT_ID": "id", "AUTH_FACEBOOK_CLIENT_SECRET": "s",
          "AUTH_PUBLIC_URL": "http://localhost:8000"}, "AUTH_PAGE_URL"),
    ],
)
def test_a_sign_in_provider_needs_its_secret_and_the_two_addresses(overrides, missing):
    with pytest.raises(ValueError, match=missing):
        AppConfig(GROQ_API_KEY="test-key", **overrides).validate()


@pytest.mark.parametrize(
    ("overrides", "named"),
    [
        ({"MAIL_BACKEND": "smtp"}, "SMTP_HOST"),
        ({"MAIL_BACKEND": "smtp", "SMTP_HOST": "smtp.example.com"}, "SMTP_FROM"),
        ({"MAIL_BACKEND": "smtp", "SMTP_HOST": "smtp.example.com",
          "SMTP_FROM": "avatar@example.com", "SMTP_SECURITY": "tls13"}, "SMTP_SECURITY"),
    ],
)
def test_sending_by_smtp_needs_a_server_and_a_sender(overrides, named):
    with pytest.raises(ValueError, match=named):
        AppConfig(GROQ_API_KEY="test-key", **overrides).validate()


def test_a_configured_smtp_server_is_accepted_and_its_password_is_not_shown():
    config = AppConfig(
        GROQ_API_KEY="test-key", MAIL_BACKEND="smtp", SMTP_HOST="smtp.example.com",
        SMTP_FROM="avatar@example.com", SMTP_USERNAME="avatar", SMTP_PASSWORD="hunter22",
    )

    config.validate()
    assert "hunter22" not in repr(config)
    assert AppConfig(GROQ_API_KEY="test-key").MAIL_BACKEND == "log"


def test_a_configured_sign_in_provider_is_accepted_and_its_secret_is_not_shown():
    config = AppConfig(
        GROQ_API_KEY="test-key",
        AUTH_GOOGLE_CLIENT_ID="id",
        AUTH_GOOGLE_CLIENT_SECRET="secret",
        AUTH_PUBLIC_URL="http://localhost:8000",
        AUTH_PAGE_URL="http://localhost:3000",
    )

    config.validate()
    assert "secret" not in repr(config)


def test_the_addresses_of_the_sign_in_are_read_without_their_last_slash(monkeypatch):
    monkeypatch.setenv("AUTH_PUBLIC_URL", "http://localhost:8000/")
    monkeypatch.setenv("AUTH_PAGE_URL", "http://localhost:3000//")

    config = AppConfig(GROQ_API_KEY="test-key")

    assert (config.AUTH_PUBLIC_URL, config.AUTH_PAGE_URL) == (
        "http://localhost:8000", "http://localhost:3000"
    )


def test_the_edges_of_the_conversation_settings_are_accepted():
    AppConfig(
        GROQ_API_KEY="test-key",
        MEMORY_HISTORY_MAX_TOKENS=1,
        KNOWLEDGE_QUERY_HISTORY_TURNS=0,
        STT_NO_SPEECH_MAX_PROB=0.0,
        MEMORY_PROMPT_MAX_ITEMS=1,
        MEMORY_ITEM_MAX_CHARS=1,
        LLM_COMPLETE_MAX_TOKENS=1,
        MEMORY_IMPORT_MAX_CHARS=1,
        MEMORY_IMPORT_MAX_FILE_MB=0.001,
        MEMORY_PROMOTE_SEEN_COUNT=1,
        MEMORY_MIN_STT_CONFIDENCE=0.0,
        MEMORY_TRANSCRIPT_TTL_DAYS=0.01,
        MEMORY_CONSOLIDATE_MAX_CHARS=1,
        MEMORY_EXTRACT_MAX_REJECTED=0,
    ).validate()
    AppConfig(GROQ_API_KEY="test-key", MEMORY_MIN_STT_CONFIDENCE=1.0).validate()


def test_the_database_url_never_appears_in_repr():
    config = AppConfig(DATABASE_URL="postgresql://avatar:s3cret@db.test/avatar")

    assert "s3cret" not in repr(config)


def test_groq_api_key_never_appears_in_repr_or_str():
    config = AppConfig(GROQ_API_KEY="gsk-sentinel-secret")

    assert "gsk-sentinel-secret" not in repr(config)
    assert "gsk-sentinel-secret" not in str(config)


def test_groq_api_key_is_not_required_when_no_stage_uses_groq():
    AppConfig(
        LLM_BACKEND="openai_compatible",
        LLM_BASE_URL="http://llm.local/v1",
        STT_BACKEND="openai_compatible",
        STT_BASE_URL="http://stt.local/v1",
    ).validate()


@pytest.mark.parametrize("groq_stage", [{"LLM_BACKEND": "groq"}, {"STT_BACKEND": "auto"}])
def test_groq_api_key_is_required_when_one_stage_uses_groq(groq_stage):
    backends = {
        "LLM_BACKEND": "openai_compatible",
        "LLM_BASE_URL": "http://llm.local/v1",
        "STT_BACKEND": "openai_compatible",
        "STT_BASE_URL": "http://stt.local/v1",
    }

    with pytest.raises(ValueError, match="groq_api_key"):
        AppConfig(**{**backends, **groq_stage}).validate()


@pytest.mark.parametrize(("stage", "key"), [("LLM", "LLM_BASE_URL"), ("STT", "STT_BASE_URL")])
def test_openai_compatible_backend_requires_its_base_url(stage, key):
    with pytest.raises(ValueError, match=key):
        AppConfig(
            GROQ_API_KEY="test-key", **{f"{stage}_BACKEND": "openai_compatible"}
        ).validate()


_DATABRICKS = {
    "LLM_BACKEND": "databricks",
    "DATABRICKS_BASE_URL": "https://dbx.local/ai-gateway/mlflow/v1",
    "DATABRICKS_TOKEN": "dapi-token",
}


def test_databricks_llm_needs_no_groq_key_when_stt_is_not_groq():
    AppConfig(
        **_DATABRICKS, STT_BACKEND="openai_compatible", STT_BASE_URL="http://stt.local/v1"
    ).validate()


@pytest.mark.parametrize("stage", ["LLM_BACKEND", "STT_BACKEND"])
@pytest.mark.parametrize("key", ["DATABRICKS_BASE_URL", "DATABRICKS_TOKEN"])
def test_databricks_backend_requires_its_url_and_a_credential(stage, key):
    settings = {**_DATABRICKS, "LLM_BACKEND": "groq", stage: "databricks", key: ""}

    with pytest.raises(ValueError, match=key):
        AppConfig(GROQ_API_KEY="test-key", **settings).validate()


def test_databricks_profile_replaces_the_token_and_no_groq_key_is_needed():
    AppConfig(
        **{**_DATABRICKS, "DATABRICKS_TOKEN": ""},
        STT_BACKEND="databricks",
        DATABRICKS_PROFILE="my-profile",
    ).validate()


def test_endpoint_keys_never_appear_in_repr():
    config = AppConfig(
        LLM_API_KEY="llm-sentinel", STT_API_KEY="stt-sentinel", DATABRICKS_TOKEN="dbx-sentinel"
    )

    assert "dbx-sentinel" not in repr(config)

    assert "llm-sentinel" not in repr(config)
    assert "stt-sentinel" not in repr(config)


def test_prompts_cannot_be_overridden_from_the_environment(monkeypatch):
    defaults = AppConfig()
    monkeypatch.setenv("SYSTEM_PROMPT", "prompt from env")
    monkeypatch.setenv("LLM_FALLBACK_TEXT", "fallback from env")

    config = AppConfig()

    assert config.SYSTEM_PROMPT == defaults.SYSTEM_PROMPT != "prompt from env"
    assert config.LLM_FALLBACK_TEXT == defaults.LLM_FALLBACK_TEXT != "fallback from env"


@pytest.mark.parametrize("stage", ["LLM", "STT"])
def test_backup_backend_needs_its_settings_like_a_primary(stage):
    backup = {f"{stage}_BACKEND_BACKUP": "databricks"}

    with pytest.raises(ValueError, match="DATABRICKS_BASE_URL"):
        AppConfig(GROQ_API_KEY="test-key", **backup).validate()

    AppConfig(
        GROQ_API_KEY="test-key",
        DATABRICKS_BASE_URL="https://dbx.local/ai-gateway/mlflow/v1",
        DATABRICKS_PROFILE="my-profile",
        **backup,
    ).validate()


def test_groq_as_a_backup_requires_the_groq_key():
    with pytest.raises(ValueError, match="groq_api_key"):
        AppConfig(
            **_DATABRICKS,
            STT_BACKEND="databricks",
            LLM_BACKEND_BACKUP="groq",
        ).validate()


# ── Knowledge base ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("overrides", "key"),
    [
        ({"EMBEDDING_BACKEND": "openai_compatible", "EMBEDDING_MODEL": "m"}, "EMBEDDING_BASE_URL"),
        (
            {"EMBEDDING_BACKEND": "openai_compatible", "EMBEDDING_BASE_URL": "http://e.test"},
            "EMBEDDING_MODEL",
        ),
        ({"EMBEDDING_BACKEND": "databricks", "DATABRICKS_PROFILE": "dev"}, "DATABRICKS_SERVING_BASE_URL"),
        (
            {
                "EMBEDDING_BACKEND": "databricks",
                "DATABRICKS_SERVING_BASE_URL": "https://ws.test/serving-endpoints",
            },
            "DATABRICKS_TOKEN",
        ),
        ({"KNOWLEDGE_CHUNK_CHARS": 100, "KNOWLEDGE_CHUNK_OVERLAP_CHARS": 100}, "KNOWLEDGE_CHUNK"),
    ],
)
def test_knowledge_settings_are_validated(overrides, key):
    with pytest.raises(ValueError, match=key):
        AppConfig(GROQ_API_KEY="test-key", **overrides).validate()


def test_the_default_embedding_needs_no_setting():
    AppConfig(GROQ_API_KEY="test-key").validate()


def test_an_embedding_backup_needs_its_settings_like_a_primary():
    with pytest.raises(ValueError, match="DATABRICKS_SERVING_BASE_URL"):
        AppConfig(GROQ_API_KEY="test-key", EMBEDDING_BACKEND_BACKUP="databricks").validate()

    AppConfig(GROQ_API_KEY="test-key", EMBEDDING_BACKEND_BACKUP="ollama").validate()
