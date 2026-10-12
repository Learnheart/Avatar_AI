from typing import Iterator

import pytest

from backend.features.knowledge.store import SqliteVecStore
from backend.models import KnowledgeDocument

SIGNATURE = "fake:3"


def document(name: str, doc_id: str = "") -> KnowledgeDocument:
    return KnowledgeDocument(doc_id or name, name, 10, 0, "2026-10-12T00:00:00+00:00")


@pytest.fixture
def store(tmp_path) -> Iterator[SqliteVecStore]:
    store = SqliteVecStore(str(tmp_path / "nested" / "knowledge.db"))
    store.open()
    yield store
    store.close()


def test_a_new_store_is_empty(store):
    assert store.list_documents() == []
    assert store.count_chunks() == 0
    assert store.signature == ""
    assert store.search([1.0, 0.0, 0.0], 3) == []


def test_an_added_document_is_listed_with_its_chunk_count(store):
    store.add_document(document("a.txt"), ["one", "two"], [[1, 0, 0], [0, 1, 0]], SIGNATURE)

    (listed,) = store.list_documents()
    assert (listed.id, listed.name, listed.size_bytes, listed.chunk_count) == (
        "a.txt", "a.txt", 10, 2
    )
    assert store.count_chunks() == 2
    assert store.signature == SIGNATURE


def test_search_returns_the_nearest_chunks_best_first(store):
    store.add_document(document("a.txt"), ["east", "north"], [[1, 0, 0], [0, 1, 0]], SIGNATURE)
    store.add_document(document("b.txt"), ["up"], [[0, 0, 1]], SIGNATURE)

    passages = store.search([0.9, 0.1, 0.0], 2)

    assert [(p.text, p.source) for p in passages] == [("east", "a.txt"), ("north", "a.txt")]
    assert passages[0].score == pytest.approx(0.994, abs=1e-3)
    assert passages[0].score > passages[1].score


def test_delete_removes_the_document_its_chunks_and_its_vectors(store):
    store.add_document(document("a.txt"), ["east"], [[1, 0, 0]], SIGNATURE)
    store.add_document(document("b.txt"), ["north"], [[0, 1, 0]], SIGNATURE)

    assert store.delete_document("a.txt") is True
    assert store.delete_document("a.txt") is False

    assert [d.name for d in store.list_documents()] == ["b.txt"]
    assert [p.text for p in store.search([1, 0, 0], 5)] == ["north"]


def test_a_document_of_the_same_name_is_replaced(store):
    store.add_document(document("a.txt", "old"), ["east"], [[1, 0, 0]], SIGNATURE)
    store.add_document(document("a.txt", "new"), ["north"], [[0, 1, 0]], SIGNATURE)

    assert [d.id for d in store.list_documents()] == ["new"]
    assert [p.text for p in store.search([1, 0, 0], 5)] == ["north"]


def test_the_data_survives_a_reopen(tmp_path):
    path = str(tmp_path / "knowledge.db")
    first = SqliteVecStore(path)
    first.open()
    first.add_document(document("a.txt"), ["east"], [[1, 0, 0]], SIGNATURE)
    first.close()

    second = SqliteVecStore(path)
    second.open()
    try:
        assert [d.name for d in second.list_documents()] == ["a.txt"]
        assert [p.text for p in second.search([1, 0, 0], 1)] == ["east"]
        assert second.signature == SIGNATURE
    finally:
        second.close()


def test_replace_vectors_changes_the_dimension_and_the_signature(store):
    store.add_document(document("a.txt"), ["east", "north"], [[1, 0, 0], [0, 1, 0]], SIGNATURE)
    ids = [chunk_id for chunk_id, _ in store.chunk_texts()]
    assert [text for _, text in store.chunk_texts()] == ["east", "north"]

    store.replace_vectors("fake:2", [(ids[0], [1, 0]), (ids[1], [0, 1])])

    assert store.signature == "fake:2"
    assert [p.text for p in store.search([0, 1], 1)] == ["north"]


def test_replace_vectors_of_an_emptied_store_lets_a_new_dimension_in(store):
    store.add_document(document("a.txt"), ["east"], [[1, 0, 0]], SIGNATURE)
    store.delete_document("a.txt")

    store.replace_vectors("fake:2", [])
    store.add_document(document("b.txt"), ["north"], [[0, 1]], "fake:2")

    assert [p.text for p in store.search([0, 1], 1)] == ["north"]
