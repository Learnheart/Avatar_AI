"""The memory items and the imports in the application database, on a real SQLite file."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest

from backend.features.accounts.store import UsersStore
from backend.features.memory.store import MemoryStore
from backend.models import MemoryImport, MemoryItem
from backend.utils.database import Database, DatabaseError

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=5)


def _open(config) -> tuple[Database, MemoryStore]:
    database = Database(config.DATABASE_URL)
    database.start()
    users = UsersStore(database)
    for user_id in ("linh", "an"):
        users.touch(user_id, user_id, NOW)
    return database, MemoryStore(database)


@pytest.fixture
def store(config) -> Iterator[MemoryStore]:
    database, memory_store = _open(config)
    yield memory_store
    database.close()


def _item(item_id: str = "item-1", user_id: str = "linh", **fields) -> MemoryItem:
    values = {"category": "identity", "text": "Tên là Linh.", "created_at": NOW, "updated_at": NOW}
    return MemoryItem(id=item_id, user_id=user_id, **{**values, **fields})


def _import(import_id: str = "import-1", user_id: str = "linh", **fields) -> MemoryImport:
    values = {
        "name": "cv.docx", "content_type": "application/pdf", "size_bytes": 1234,
        "sha256": "ab" * 32, "object_key": f"imports/{import_id}", "created_at": NOW,
    }
    return MemoryImport(id=import_id, user_id=user_id, **{**values, **fields})


def test_an_added_item_is_read_back_whole(store):
    store.add_import(_import())
    item = _item(
        status="pending", source="conversation", import_id="import-1",
        evidence="em tên là Linh", seen_count=2,
    )

    store.add_item(item)

    assert store.list_items("linh") == [item]


def test_an_item_can_point_at_the_item_it_would_replace(store):
    store.add_item(_item("old"))
    proposal = _item("new", replaces_id="old", created_at=LATER, updated_at=LATER)

    store.add_item(proposal)

    assert [item.replaces_id for item in store.list_items("linh")] == [None, "old"]


def test_items_are_listed_oldest_first_and_by_status(store):
    store.add_item(_item("second", created_at=LATER, updated_at=LATER))
    store.add_item(_item("first"))
    store.add_item(_item("proposed", status="pending", created_at=LATER + timedelta(minutes=1)))

    assert [item.id for item in store.list_items("linh")] == ["first", "second", "proposed"]
    assert [item.id for item in store.list_items("linh", "active")] == ["first", "second"]
    assert [item.id for item in store.list_items("linh", "pending")] == ["proposed"]


def test_the_list_of_a_user_without_items_is_empty(store):
    assert store.list_items("linh") == []


def test_an_update_changes_the_given_fields_only(store):
    item = _item()
    store.add_item(item)

    renamed = store.update_item("linh", "item-1", LATER, text="Tên là Khánh Linh.")
    moved = store.update_item("linh", "item-1", LATER, category="preference")

    assert renamed == replace(item, text="Tên là Khánh Linh.", updated_at=LATER)
    assert moved == replace(renamed, category="preference")
    assert store.list_items("linh") == [moved]


def test_updating_or_deleting_an_unknown_item_changes_nothing(store):
    store.add_item(_item())

    assert store.update_item("linh", "nope", LATER, text="x") is None
    assert store.delete_item("linh", "nope") is False
    assert store.list_items("linh") == [_item()]


def test_a_deleted_item_is_gone(store):
    store.add_item(_item())

    assert store.delete_item("linh", "item-1") is True
    assert store.delete_item("linh", "item-1") is False
    assert store.list_items("linh") == []


def test_one_user_never_sees_or_changes_another_s_items(store):
    mine, theirs = _item("mine", "linh"), _item("theirs", "an", text="Tên là An.")
    store.add_item(mine)
    store.add_item(theirs)

    assert store.list_items("linh") == [mine]
    assert store.list_items("an") == [theirs]
    assert store.update_item("linh", "theirs", LATER, text="Tên là Linh.") is None
    assert store.delete_item("linh", "theirs") is False
    assert store.list_items("an") == [theirs]


def test_items_survive_a_close_and_a_reopen(config):
    database, store = _open(config)
    store.add_item(_item())
    database.close()

    database, store = _open(config)
    try:
        assert store.list_items("linh") == [_item()]
    finally:
        database.close()


# ── Imports ───────────────────────────────────────────────────────────────

def _imported(item_id: str, import_id: str = "import-1", user_id: str = "linh") -> MemoryItem:
    return _item(item_id, user_id, source="import", import_id=import_id, evidence="Tôi là Linh.")


def test_an_added_import_is_read_back_whole_and_listed_with_its_item_count(store):
    record = _import()

    store.add_import(record)
    store.add_items([_imported("a"), _imported("b")])

    assert store.get_import("linh", "import-1") == record
    assert store.list_imports("linh") == [(record, 2)]


def test_imports_are_listed_oldest_first_each_with_its_own_count(store):
    second, first = _import("second", created_at=LATER), _import("first")
    store.add_import(second)
    store.add_import(first)
    store.add_items([_imported("a", "second")])
    store.add_item(_item("typed"))  # names no import

    assert store.list_imports("linh") == [(first, 0), (second, 1)]


def test_the_items_of_an_import_are_stored_together_or_not_at_all(store):
    store.add_import(_import())

    with pytest.raises(DatabaseError):
        store.add_items([_imported("a"), _imported("a")])  # the same id twice

    assert store.list_items("linh") == []
    store.add_items([])
    assert store.list_items("linh") == []


def test_an_item_cannot_name_an_import_that_does_not_exist(store):
    with pytest.raises(DatabaseError):
        store.add_item(_imported("a", "no-such-import"))

    assert store.list_items("linh") == []


def test_deleting_an_import_with_its_items_removes_both(store):
    record = _import()
    store.add_import(record)
    store.add_items([_imported("a"), _imported("b")])
    store.add_item(_item("typed"))

    assert store.delete_import("linh", "import-1", delete_items=True) is True

    assert store.get_import("linh", "import-1") is None
    assert store.list_imports("linh") == []
    assert [item.id for item in store.list_items("linh")] == ["typed"]
    assert store.delete_import("linh", "import-1", delete_items=True) is False


def test_deleting_an_import_alone_keeps_its_items_without_a_source(store):
    store.add_import(_import())
    store.add_import(_import("other"))
    store.add_items([_imported("a"), _imported("b", "other")])

    assert store.delete_import("linh", "import-1", delete_items=False) is True

    kept = {item.id: item for item in store.list_items("linh")}
    assert kept["a"] == replace(_imported("a"), import_id=None)
    assert kept["b"] == _imported("b", "other")  # the other import keeps its item
    assert [record.id for record, _ in store.list_imports("linh")] == ["other"]


def test_one_user_never_sees_or_deletes_another_s_import(store):
    record = _import()
    store.add_import(record)
    store.add_items([_imported("a")])

    assert store.get_import("an", "import-1") is None
    assert store.list_imports("an") == []
    assert store.delete_import("an", "import-1", delete_items=True) is False
    assert store.list_imports("linh") == [(record, 1)]
    assert store.list_items("linh") == [_imported("a")]


def test_imports_survive_a_close_and_a_reopen(config):
    database, store = _open(config)
    store.add_import(_import())
    database.close()

    database, store = _open(config)
    try:
        assert store.get_import("linh", "import-1") == _import()
    finally:
        database.close()
