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
