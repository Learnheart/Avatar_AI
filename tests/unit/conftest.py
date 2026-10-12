"""Shared fixtures: unit tests never read the developer's .env values."""
from dataclasses import fields

import pytest

from backend.config import AppConfig

_ENV_KEYS = [f.name for f in fields(AppConfig)] + ["groq_api_key"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def config(tmp_path) -> AppConfig:
    # The knowledge base of a test lives in its own temporary directory.
    return AppConfig(GROQ_API_KEY="test-key", KNOWLEDGE_DB_PATH=str(tmp_path / "knowledge.db"))
