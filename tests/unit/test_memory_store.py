"""The memory items, the imports and the recorded conversations in the application
database, on a real SQLite file."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest

from backend.features.accounts.store import UsersStore
from sqlalchemy import select

from backend.features.memory.store import MemoryStore, conversations, turns
from backend.models import MemoryImport, MemoryItem, StatedAgain, StoredTurn
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
    assert store.delete_item("linh", "nope", LATER) is False
    assert store.list_items("linh") == [_item()]


def test_a_deleted_item_is_gone(store):
    store.add_item(_item())

    assert store.delete_item("linh", "item-1", LATER) is True
    assert store.delete_item("linh", "item-1", LATER) is False
    assert store.list_items("linh") == []


def test_one_user_never_sees_or_changes_another_s_items(store):
    mine, theirs = _item("mine", "linh"), _item("theirs", "an", text="Tên là An.")
    store.add_item(mine)
    store.add_item(theirs)

    assert store.list_items("linh") == [mine]
    assert store.list_items("an") == [theirs]
    assert store.update_item("linh", "theirs", LATER, text="Tên là Linh.") is None
    assert store.delete_item("linh", "theirs", LATER) is False
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


# ── Proposals, approval and rejection ─────────────────────────────────────

def _proposed(item_id: str = "heard", user_id: str = "linh", **fields) -> MemoryItem:
    values = {
        "category": "preference", "text": "Tôi thích trà.", "status": "pending",
        "source": "conversation", "evidence": "tôi thích trà", "created_at": LATER,
        "updated_at": LATER,
    }
    return _item(item_id, user_id, **{**values, **fields})


def test_items_are_listed_by_several_statuses(store):
    store.add_item(_item("typed"))
    store.add_item(_proposed("heard"))
    store.add_item(_proposed("refused", status="rejected", text="Tôi thích cà phê."))

    assert [item.id for item in store.list_items("linh", "active", "pending")] == ["typed", "heard"]
    assert [item.id for item in store.list_items("linh", "rejected")] == ["refused"]
    assert len(store.list_items("linh")) == 3  # the extractor is shown all of them


def test_approving_a_proposed_item_puts_it_in_use(store):
    store.add_item(_proposed())
    at = LATER + timedelta(minutes=1)

    approved = store.approve_item("linh", "heard", at)

    assert approved == replace(_proposed(), status="active", updated_at=at)
    assert store.list_items("linh") == [approved]


def test_approving_can_come_with_a_corrected_text_and_category(store):
    store.add_item(_proposed())

    approved = store.approve_item("linh", "heard", LATER, text="Tôi thích trà xanh.", category="personality")

    assert (approved.status, approved.text, approved.category) == (
        "active", "Tôi thích trà xanh.", "personality"
    )


def test_approving_a_replacement_removes_the_replaced_item_for_good(store):
    store.add_item(_item("old", text="Tôi tên là Lan."))
    store.add_item(_item("kept", category="addressing", text="Gọi tôi là chị."))
    store.add_item(_proposed("new", category="identity", text="Tôi tên là Linh.", replaces_id="old"))
    # A second proposal on the same item: it must not be left pointing at nothing.
    store.add_item(_proposed("other", category="identity", text="Tôi tên là Lin.", replaces_id="old"))

    approved = store.approve_item("linh", "new", LATER)

    assert (approved.status, approved.replaces_id) == ("active", None)
    left = {item.id: item for item in store.list_items("linh")}
    assert set(left) == {"kept", "new", "other"}
    assert (left["other"].status, left["other"].replaces_id) == ("pending", None)


def test_approving_an_item_that_is_active_already_changes_nothing(store):
    store.add_item(_item())

    assert store.approve_item("linh", "item-1", LATER) == _item()
    assert store.list_items("linh") == [_item()]


def test_editing_a_proposed_item_without_approving_it_leaves_it_proposed(store):
    store.add_item(_item("old"))
    store.add_item(_proposed(replaces_id="old"))

    edited = store.update_item("linh", "heard", LATER, text="Tôi thích trà xanh.")

    assert (edited.status, edited.replaces_id) == ("pending", "old")
    assert len(store.list_items("linh", "active")) == 1


def test_a_rejected_or_foreign_item_can_be_neither_approved_nor_edited(store):
    refused = _proposed("refused", status="rejected")
    theirs = _proposed("theirs", "an")
    store.add_item(refused)
    store.add_item(theirs)

    assert store.approve_item("linh", "refused", LATER) is None
    assert store.update_item("linh", "refused", LATER, text="x") is None
    assert store.approve_item("linh", "theirs", LATER) is None
    assert store.list_items("linh") == [refused]
    assert store.list_items("an") == [theirs]


@pytest.mark.parametrize("status", ["pending", "active"])
def test_deleting_an_item_heard_in_a_conversation_keeps_it_as_rejected(store, status):
    store.add_item(_item("old"))
    store.add_item(_proposed(status=status, replaces_id="old" if status == "pending" else None))
    at = LATER + timedelta(minutes=1)

    assert store.delete_item("linh", "heard", at) is True

    (tombstone,) = store.list_items("linh", "rejected")
    assert tombstone == replace(_proposed(), status="rejected", updated_at=at)
    assert [item.id for item in store.list_items("linh", "active", "pending")] == ["old"]
    assert store.delete_item("linh", "heard", at) is False  # gone, for the profile page


@pytest.mark.parametrize("source", ["manual", "import"])
def test_deleting_a_typed_or_imported_item_removes_it_for_good(store, source):
    store.add_item(_item(source=source))

    assert store.delete_item("linh", "item-1", LATER) is True

    assert store.list_items("linh") == []


@pytest.mark.parametrize("source", ["manual", "conversation"])
def test_a_proposal_on_a_deleted_item_stays_as_a_plain_proposal(store, source):
    store.add_item(_item("old", source=source))
    store.add_item(_proposed(replaces_id="old"))

    assert store.delete_item("linh", "old", LATER) is True

    (proposal,) = store.list_items("linh", "pending")
    assert proposal == _proposed()  # replaces nothing any more


def test_a_proposal_on_an_item_deleted_with_its_import_stays_as_a_plain_proposal(store):
    store.add_import(_import())
    store.add_items([_imported("a")])
    store.add_item(_proposed(replaces_id="a"))

    assert store.delete_import("linh", "import-1", delete_items=True) is True

    assert store.list_items("linh") == [_proposed()]


# ── Conversations and their turns ─────────────────────────────────────────

def _say(store, conversation_id="talk", user_id="linh", text="xin chào", at=NOW, **fields):
    values = {"answer_text": "Chào chị.", "stt_confidence": 0.9, "interrupted": False}
    values.update(fields)
    return store.add_turn(user_id, conversation_id, text, now=at, **values)


def test_turns_are_read_back_in_the_order_of_recording(store):
    assert _say(store, text="một") is True
    assert _say(store, text="hai", answer_text="", stt_confidence=None, interrupted=True, at=LATER)

    assert store.list_turns("linh", "talk") == [
        StoredTurn("một", "Chào chị.", 0.9),
        StoredTurn("hai", "", None),
    ]
    with store._database.begin() as connection:
        rows = connection.execute(select(turns).order_by(turns.c.position)).all()
    assert [(row.position, row.interrupted, row.created_at) for row in rows] == [
        (1, False, NOW), (2, True, LATER)
    ]


def test_the_first_turn_opens_the_conversation_to_be_read(store):
    assert store.unconsolidated_conversations(LATER) == []

    _say(store)
    _say(store, at=LATER)

    assert store.unconsolidated_conversations(LATER) == [("linh", "talk")]


def test_one_user_never_reads_or_writes_another_s_conversation(store):
    _say(store)

    assert _say(store, user_id="an") is False  # the id of someone else's conversation
    assert store.list_turns("an", "talk") == []
    assert store.close_conversation("an", "talk", LATER) is False
    assert store.consolidate("an", "talk", LATER) is None
    assert len(store.list_turns("linh", "talk")) == 1


def test_turns_and_conversations_survive_a_close_and_a_reopen(config):
    database, store = _open(config)
    _say(store)
    database.close()

    database, store = _open(config)
    try:
        assert store.list_turns("linh", "talk") == [StoredTurn("xin chào", "Chào chị.", 0.9)]
        assert store.unconsolidated_conversations(LATER) == [("linh", "talk")]
    finally:
        database.close()


def test_consolidating_deletes_the_turns_and_stores_what_was_read_together(store):
    store.add_item(_proposed("known", text="Tôi thích trà xanh."))
    _say(store)
    _say(store, "other")

    assert store.close_conversation("linh", "talk", LATER) is True
    assert store.consolidate(
        "linh", "talk", LATER, proposed=[_proposed()], stated_again=[StatedAgain("known", "pending", True)]
    ) == ["known"]

    assert store.list_turns("linh", "talk") == []
    assert len(store.list_turns("linh", "other")) == 1  # another conversation keeps its turns
    assert store.unconsolidated_conversations(LATER) == [("linh", "other")]
    items = {item.id: item for item in store.list_items("linh")}
    assert items["heard"] == _proposed()
    assert (items["known"].seen_count, items["known"].status, items["known"].updated_at) == (
        2, "active", LATER
    )


def test_a_consolidated_conversation_is_read_and_written_no_more(store):
    _say(store)
    store.consolidate("linh", "talk", LATER)

    assert store.close_conversation("linh", "talk", LATER) is False
    assert store.consolidate("linh", "talk", LATER, proposed=[_proposed()]) is None
    assert _say(store, at=LATER) is False
    assert store.list_items("linh") == []
    assert store.list_turns("linh", "talk") == []


def test_a_failed_consolidation_keeps_the_turns_and_stores_nothing(store):
    _say(store)

    with pytest.raises(DatabaseError):
        store.consolidate("linh", "talk", LATER, proposed=[_proposed("a"), _proposed("a")])

    assert store.list_items("linh") == []
    assert len(store.list_turns("linh", "talk")) == 1
    assert store.unconsolidated_conversations(LATER) == [("linh", "talk")]


def test_a_proposal_to_replace_an_item_that_is_gone_is_stored_as_a_plain_proposal(store):
    store.add_item(_item("old"))
    _say(store)

    store.consolidate(
        "linh", "talk", LATER,
        proposed=[_proposed("a", replaces_id="old"), _proposed("b", replaces_id="gone")],
    )

    assert {item.id: item.replaces_id for item in store.list_items("linh", "pending")} == {
        "a": "old", "b": None
    }


def test_turns_past_the_ttl_are_deleted_and_their_conversation_is_done(store):
    old, recent = NOW - timedelta(days=8), NOW - timedelta(days=1)
    _say(store, "forgotten", at=old)
    _say(store, "half", text="cũ", at=old)
    _say(store, "half", text="mới", at=recent)
    _say(store, "fresh", user_id="an", at=recent)

    assert store.expire_turns(NOW - timedelta(days=7), NOW) == 2

    assert store.list_turns("linh", "forgotten") == []
    assert [turn.user_text for turn in store.list_turns("linh", "half")] == ["mới"]
    assert len(store.list_turns("an", "fresh")) == 1
    assert store.unconsolidated_conversations(NOW) == [("linh", "half"), ("an", "fresh")]


def test_only_the_conversations_started_until_a_moment_are_left_to_read(store):
    _say(store, "before", at=NOW)
    _say(store, "after", at=LATER)

    assert store.unconsolidated_conversations(NOW) == [("linh", "before")]


@pytest.mark.parametrize(
    "meanwhile, left",
    [
        (lambda store: store.delete_item("linh", "heard", LATER), ("rejected", 1)),
        (lambda store: store.approve_item("linh", "heard", LATER), ("active", 1)),
        (lambda store: None, ("active", 2)),
    ],
    ids=["rejected meanwhile", "approved meanwhile", "untouched"],
)
def test_an_item_stated_again_is_counted_only_while_it_is_what_was_read(store, meanwhile, left):
    store.add_item(_proposed())
    _say(store)
    meanwhile(store)

    counted = store.consolidate(
        "linh", "talk", LATER, stated_again=[StatedAgain("heard", "pending", True)]
    )

    (item,) = store.list_items("linh")
    assert (item.status, item.seen_count) == left
    assert counted == (["heard"] if left == ("active", 2) else [])


def test_an_item_stated_again_is_counted_from_what_the_row_holds(store):
    store.add_item(_proposed(seen_count=4))
    _say(store)

    store.consolidate("linh", "talk", LATER, stated_again=[StatedAgain("heard", "pending", False)])

    (item,) = store.list_items("linh")
    assert (item.status, item.seen_count, item.updated_at) == ("pending", 5, LATER)


@pytest.mark.parametrize("closed_first", [True, False])
def test_consolidating_keeps_the_closing_time_or_sets_it(store, closed_first):
    _say(store)
    if closed_first:
        store.close_conversation("linh", "talk", LATER)
    at = LATER + timedelta(minutes=1)

    assert store.consolidate("linh", "talk", at) == []

    with store._database.begin() as connection:
        row = connection.execute(select(conversations)).one()
    assert (row.closed_at, row.consolidated_at) == (LATER if closed_first else at, at)
