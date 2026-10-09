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
