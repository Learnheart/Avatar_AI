"""The memory items in the application database, on a real SQLite file."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest

from backend.features.accounts.store import UsersStore
from backend.features.memory.store import MemoryStore
from backend.models import MemoryItem
from backend.utils.database import Database

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


def test_an_added_item_is_read_back_whole(store):
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
