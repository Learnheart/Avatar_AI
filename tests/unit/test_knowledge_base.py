from typing import AsyncIterator

import pytest

from backend.config import AppConfig
from backend.core.contracts import Embedder, EmbeddingError, KnowledgeError
from backend.features.knowledge.knowledge_base import EmptyDocument, KnowledgeBase
from backend.features.knowledge.loaders import UnsupportedDocument
from backend.features.knowledge.store import SqliteVecStore
from backend.providers.embedding.local_hash import LocalHashEmbedder

LEAVE = "Mỗi nhân viên được nghỉ phép năm mười hai ngày.".encode("utf-8")
RATES = "Lãi suất tiết kiệm kỳ hạn sáu tháng là năm phần trăm.".encode("utf-8")


class CountingEmbedder(Embedder):
    """Wraps the local embedder; counts the calls and can be made to fail."""

    def __init__(self, config: AppConfig, signature: str = "") -> None:
        self._inner = LocalHashEmbedder(config)
        self._signature = signature or self._inner.signature
        self.calls: list[list[str]] = []
        self.error: Exception | None = None

    @property
    def signature(self) -> str:
        return self._signature

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        if self.error:
            raise self.error
        return await self._inner.embed(texts)


@pytest.fixture
def embedder(config) -> CountingEmbedder:
    return CountingEmbedder(config)


@pytest.fixture
async def knowledge(config, embedder) -> AsyncIterator[KnowledgeBase]:
    base = KnowledgeBase(config, embedder, SqliteVecStore(config.KNOWLEDGE_DB_PATH))
    await base.start()
    yield base
    await base.close()


async def test_an_uploaded_document_is_found_by_a_question(knowledge):
    document = await knowledge.add_document("leave.txt", LEAVE)
    await knowledge.add_document("rates.txt", RATES)

    passages = await knowledge.search("nhân viên được nghỉ phép năm bao nhiêu ngày")

    assert (document.name, document.chunk_count, document.size_bytes) == (
        "leave.txt", 1, len(LEAVE)
    )
    assert passages[0].text == LEAVE.decode("utf-8")
    assert passages[0].source == "leave.txt"
    assert {d.name for d in await knowledge.list_documents()} == {"leave.txt", "rates.txt"}


async def test_search_returns_at_most_top_k_passages(config, knowledge):
    config.KNOWLEDGE_TOP_K = 1
    await knowledge.add_document("leave.txt", LEAVE)
    await knowledge.add_document("rates.txt", RATES)

    assert len(await knowledge.search("nghỉ phép")) == 1


async def test_passages_below_the_minimum_score_are_dropped(config, knowledge):
    await knowledge.add_document("rates.txt", RATES)
    config.KNOWLEDGE_MIN_SCORE = 0.2

    assert await knowledge.search("hôm nay thời tiết đẹp quá") == []
    assert await knowledge.search("lãi suất tiết kiệm kỳ hạn sáu tháng")


async def test_an_empty_base_answers_without_embedding_the_question(knowledge, embedder):
    assert await knowledge.search("nghỉ phép") == []
    assert embedder.calls == []


async def test_uploading_the_same_name_replaces_the_document(knowledge):
    await knowledge.add_document("policy.txt", LEAVE)
    await knowledge.add_document("policy.txt", RATES)

    (document,) = await knowledge.list_documents()
    passages = await knowledge.search("nghỉ phép lãi suất")
    assert document.size_bytes == len(RATES)
    assert [p.text for p in passages] == [RATES.decode("utf-8")]


async def test_only_the_file_name_of_an_uploaded_path_is_kept(knowledge):
    document = await knowledge.add_document("C:\\docs\\..\\leave.txt", LEAVE)

    assert document.name == "leave.txt"


async def test_a_deleted_document_is_no_longer_found(knowledge):
    document = await knowledge.add_document("leave.txt", LEAVE)

    assert await knowledge.delete_document(document.id) is True
    assert await knowledge.delete_document(document.id) is False
    assert await knowledge.search("nghỉ phép") == []


async def test_a_file_without_text_is_rejected(knowledge):
    with pytest.raises(EmptyDocument, match="blank.txt"):
        await knowledge.add_document("blank.txt", b"  \n ")
    assert await knowledge.list_documents() == []


async def test_an_unsupported_file_is_rejected(knowledge):
    with pytest.raises(UnsupportedDocument):
        await knowledge.add_document("photo.png", b"\x89PNG")


async def test_an_embedding_failure_on_upload_stores_nothing(knowledge, embedder):
    embedder.error = EmbeddingError("provider down")

    with pytest.raises(KnowledgeError, match="provider down"):
        await knowledge.add_document("leave.txt", LEAVE)
    assert await knowledge.list_documents() == []


async def test_an_embedding_failure_on_search_raises_knowledge_error(knowledge, embedder):
    await knowledge.add_document("leave.txt", LEAVE)
    embedder.error = EmbeddingError("provider down")

    with pytest.raises(KnowledgeError, match="provider down"):
        await knowledge.search("nghỉ phép")


async def test_a_long_document_is_cut_into_chunks(config, knowledge):
    config.KNOWLEDGE_CHUNK_CHARS, config.KNOWLEDGE_CHUNK_OVERLAP_CHARS = 60, 0
    text = "\n\n".join([LEAVE.decode("utf-8"), RATES.decode("utf-8")])

    document = await knowledge.add_document("all.md", text.encode("utf-8"))

    assert document.chunk_count == 2


async def test_another_embedder_reindexes_the_stored_chunks_at_start(config, knowledge):
    await knowledge.add_document("leave.txt", LEAVE)
    await knowledge.close()

    config.EMBEDDING_HASH_DIM = 256
    other = CountingEmbedder(config)
    reopened = KnowledgeBase(config, other, SqliteVecStore(config.KNOWLEDGE_DB_PATH))
    await reopened.start()

    assert other.calls == [[LEAVE.decode("utf-8")]]
    assert [p.source for p in await reopened.search("nghỉ phép năm")] == ["leave.txt"]


async def test_the_same_embedder_does_not_reindex_at_start(config, knowledge, embedder):
    await knowledge.add_document("leave.txt", LEAVE)
    await knowledge.close()
    embedder.calls.clear()

    reopened = KnowledgeBase(config, embedder, SqliteVecStore(config.KNOWLEDGE_DB_PATH))
    await reopened.start()

    assert embedder.calls == []


async def test_a_failed_reindex_fails_the_start(config, knowledge):
    await knowledge.add_document("leave.txt", LEAVE)
    await knowledge.close()

    other = CountingEmbedder(config, signature="other:model")
    other.error = EmbeddingError("provider down")
    reopened = KnowledgeBase(config, other, SqliteVecStore(config.KNOWLEDGE_DB_PATH))

    with pytest.raises(KnowledgeError, match="could not be re-indexed"):
        await reopened.start()
