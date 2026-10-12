import math
from types import SimpleNamespace

import openai
import pytest

from backend.config import AppConfig
from backend.core.contracts import EmbeddingError
from backend.providers.catalog import create_embedder
from backend.providers.databricks import ProfileToken
from backend.providers.embedding import openai_compatible
from backend.providers.embedding.local_hash import LocalHashEmbedder
from backend.providers.embedding.openai_compatible import OpenAICompatibleEmbedder
from backend.providers.fallback import FallbackEmbedder


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# ── local_hash ────────────────────────────────────────────────────────────

async def test_local_hash_vectors_have_the_configured_size_and_unit_length():
    embedder = LocalHashEmbedder(AppConfig(EMBEDDING_HASH_DIM=64))

    vectors = await embedder.embed(["Lãi suất tiết kiệm", "Quy định nghỉ phép năm"])

    assert [len(v) for v in vectors] == [64, 64]
    assert all(math.isclose(cosine(v, v), 1.0, rel_tol=1e-6) for v in vectors)
    assert embedder.signature == "local_hash:64"


async def test_local_hash_is_deterministic_and_ignores_case(config):
    first = await LocalHashEmbedder(config).embed(["Nghỉ phép năm"])
    second = await LocalHashEmbedder(config).embed(["nghỉ PHÉP năm"])

    assert first == second


async def test_local_hash_ranks_a_text_sharing_words_above_an_unrelated_one(config):
    query, related, unrelated = await LocalHashEmbedder(config).embed([
        "nhân viên được nghỉ phép năm bao nhiêu ngày",
        "Mỗi nhân viên được nghỉ phép năm mười hai ngày.",
        "Lãi suất tiết kiệm kỳ hạn sáu tháng là năm phần trăm.",
    ])

    assert cosine(query, related) > cosine(query, unrelated)


async def test_local_hash_of_a_text_without_words_is_the_zero_vector(config):
    assert await LocalHashEmbedder(config).embed(["", " ... "]) == [
        [0.0] * config.EMBEDDING_HASH_DIM
    ] * 2


# ── openai_compatible ─────────────────────────────────────────────────────

class FakeAsyncOpenAI:
    """Stands in for openai.AsyncOpenAI: records each embeddings request and answers one
    vector per text, [length of the text], returned in reverse order."""

    def __init__(self, **options) -> None:
        self.options = options
        self.requests: list[dict] = []
        self.error: Exception | None = None
        self.embeddings = SimpleNamespace(create=self._create)

    async def _create(self, **request):
        self.requests.append(request)
        if self.error:
            raise self.error
        data = [
            SimpleNamespace(index=index, embedding=[float(len(text))])
            for index, text in enumerate(request["input"])
        ]
        return SimpleNamespace(data=data[::-1])


@pytest.fixture
def embedder(monkeypatch) -> OpenAICompatibleEmbedder:
    monkeypatch.setattr(openai_compatible, "AsyncOpenAI", FakeAsyncOpenAI)
    return OpenAICompatibleEmbedder(
        AppConfig(EMBEDDING_BATCH_SIZE=2, EMBEDDING_TIMEOUT_SEC=7.0),
        base_url="http://embed.test/v1",
        api_key="key",
        model="embed-model",
    )


async def test_texts_are_sent_in_batches_and_vectors_keep_their_order(embedder):
    vectors = await embedder.embed(["a", "bb", "ccc"])

    client = embedder._client
    assert vectors == [[1.0], [2.0], [3.0]]
    assert [r["input"] for r in client.requests] == [["a", "bb"], ["ccc"]]
    assert all(r["model"] == "embed-model" for r in client.requests)
    assert all(r["encoding_format"] == "float" for r in client.requests)
    assert client.options["base_url"] == "http://embed.test/v1"
    assert client.options["timeout"] == 7.0
    assert embedder.signature == "openai_compatible:embed-model"


async def test_no_text_makes_no_request(embedder):
    assert await embedder.embed([]) == []
    assert embedder._client.requests == []


async def test_provider_error_raises_embedding_error(embedder):
    embedder._client.error = openai.OpenAIError("boom")

    with pytest.raises(EmbeddingError, match="Embedding request failed"):
        await embedder.embed(["a"])


# ── catalog ───────────────────────────────────────────────────────────────

def test_local_hash_is_the_default_backend(config):
    assert isinstance(create_embedder(config), LocalHashEmbedder)


def test_openai_compatible_backend_uses_the_embedding_endpoint(monkeypatch):
    monkeypatch.setattr(openai_compatible, "AsyncOpenAI", FakeAsyncOpenAI)

    embedder = create_embedder(AppConfig(
        EMBEDDING_BACKEND="openai_compatible",
        EMBEDDING_BASE_URL="http://embed.test/v1",
        EMBEDDING_MODEL="embed-model",
    ))

    assert embedder._client.options["base_url"] == "http://embed.test/v1"
    assert embedder._client.options["api_key"] == "not-needed"
    assert embedder.signature == "openai_compatible:embed-model"


def test_databricks_backend_uses_the_serving_url_and_the_profile(monkeypatch):
    monkeypatch.setattr(openai_compatible, "AsyncOpenAI", FakeAsyncOpenAI)

    embedder = create_embedder(AppConfig(
        EMBEDDING_BACKEND="databricks",
        DATABRICKS_SERVING_BASE_URL="https://ws.test/serving-endpoints",
        DATABRICKS_PROFILE="dev",
    ))

    assert embedder._client.options["base_url"] == "https://ws.test/serving-endpoints"
    assert isinstance(embedder._client.options["api_key"], ProfileToken)
    assert embedder.signature == "openai_compatible:databricks-qwen3-embedding-0-6b"


def test_unknown_embedding_backend_raises_value_error():
    with pytest.raises(ValueError, match="nope"):
        create_embedder(AppConfig(EMBEDDING_BACKEND="nope"))


def test_ollama_backend_uses_the_local_server_and_its_model(monkeypatch):
    monkeypatch.setattr(openai_compatible, "AsyncOpenAI", FakeAsyncOpenAI)

    embedder = create_embedder(AppConfig(EMBEDDING_BACKEND="ollama"))

    assert embedder._client.options["base_url"] == "http://localhost:11434/v1"
    assert embedder._client.options["api_key"] == "not-needed"
    assert embedder.signature == "openai_compatible:qwen3-embedding:0.6b"


def test_a_named_backup_wraps_the_primary_and_keeps_its_signature(monkeypatch):
    monkeypatch.setattr(openai_compatible, "AsyncOpenAI", FakeAsyncOpenAI)

    embedder = create_embedder(AppConfig(
        EMBEDDING_BACKEND="databricks",
        DATABRICKS_SERVING_BASE_URL="https://ws.test/serving-endpoints",
        DATABRICKS_PROFILE="dev",
        EMBEDDING_BACKEND_BACKUP="ollama",
        OLLAMA_BASE_URL="http://ollama.test/v1",
    ))

    assert isinstance(embedder, FallbackEmbedder)
    assert embedder.signature == "openai_compatible:databricks-qwen3-embedding-0-6b"
    assert embedder._backup._client.options["base_url"] == "http://ollama.test/v1"


@pytest.mark.parametrize("backup", ["", "local_hash"])
def test_an_empty_or_identical_backup_builds_the_primary_alone(backup):
    embedder = create_embedder(AppConfig(EMBEDDING_BACKEND_BACKUP=backup))

    assert isinstance(embedder, LocalHashEmbedder)


def test_unknown_embedding_backup_raises_value_error():
    with pytest.raises(ValueError, match="nope"):
        create_embedder(AppConfig(EMBEDDING_BACKEND_BACKUP="nope"))
