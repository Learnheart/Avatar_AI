"""
The memory items, the imports and the recorded conversations of the application database.
All the SQL of the memory is here, written against SQLAlchemy Core. Every method takes
the user id: no query reads across users. The two exceptions are the housekeeping of the
startup, which names no user and gives back ids only: `expire_turns` and
`unconsolidated_conversations`. Every method blocks; call them off the event loop.
"""
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    delete,
    func,
    insert,
    select,
    update,
)

from backend.features.accounts.store import users
from backend.models import (
    MEMORY_SOURCE_CONVERSATION,
    MEMORY_STATUS_ACTIVE,
    MEMORY_STATUS_PENDING,
    MEMORY_STATUS_REJECTED,
    MemoryImport,
    MemoryItem,
    StatedAgain,
    StoredTurn,
)
from backend.utils.database import Database, UTCDateTime, metadata

# What a user imported to describe themselves. The original bytes are not here:
# `object_key` points into the object store.
imports = Table(
    "imports",
    metadata,
    Column("id", String, primary_key=True),
    Column("user_id", String, ForeignKey(users.c.id), nullable=False, index=True),
    Column("name", String, nullable=False),
    Column("content_type", String, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("sha256", String, nullable=False),
    Column("object_key", String, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
)

memory_items = Table(
    "memory_items",
    metadata,
    Column("id", String, primary_key=True),
    Column("user_id", String, ForeignKey(users.c.id), nullable=False, index=True),
    Column("category", String, nullable=False),
    Column("text", Text, nullable=False),
    Column("status", String, nullable=False),
    Column("source", String, nullable=False),
    # Set when the item was read from an import that is still kept.
    Column("import_id", String, ForeignKey(imports.c.id), index=True),
    # Set on a proposal to replace another item.
    Column("replaces_id", String, ForeignKey("memory_items.id")),
    Column("evidence", Text),
    Column("seen_count", Integer, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
)

# A conversation of a logged-in user: the row is written with its first recorded turn.
conversations = Table(
    "conversations",
    metadata,
    Column("id", String, primary_key=True),
    Column("user_id", String, ForeignKey(users.c.id), nullable=False, index=True),
    Column("started_at", UTCDateTime, nullable=False),
    Column("closed_at", UTCDateTime),
    # Set once the conversation was read into memory items; its turns are deleted then.
    Column("consolidated_at", UTCDateTime),
)

# The turns of a conversation, kept until it is consolidated.
turns = Table(
    "turns",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "conversation_id", String, ForeignKey(conversations.c.id), nullable=False, index=True
    ),
    Column("position", Integer, nullable=False),
    Column("user_text", Text, nullable=False),
    Column("answer_text", Text, nullable=False),
    Column("stt_confidence", Float),
    Column("interrupted", Boolean, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
)


def _item(row) -> MemoryItem:
    return MemoryItem(**row._mapping)


class MemoryStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    def add_item(self, item: MemoryItem) -> None:
        with self._database.begin() as connection:
            connection.execute(insert(memory_items).values(**vars(item)))

    def list_items(self, user_id: str, *statuses: str) -> list[MemoryItem]:
        """The items of the user, oldest first; only those of `statuses` when some are
        given (the rejected ones included otherwise)."""
        query = select(memory_items).where(memory_items.c.user_id == user_id)
        if statuses:
            query = query.where(memory_items.c.status.in_(statuses))
        query = query.order_by(memory_items.c.created_at, memory_items.c.id)
        with self._database.begin() as connection:
            return [_item(row) for row in connection.execute(query)]

    def update_item(
        self,
        user_id: str,
        item_id: str,
        updated_at: datetime,
        text: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Optional[MemoryItem]:
        """Change the text and / or the category of an item of the user. None when the
        user has no such item, a rejected one included."""
        with self._database.begin() as connection:
            return _change(connection, user_id, item_id, updated_at, text, category)

    def approve_item(
        self,
        user_id: str,
        item_id: str,
        now: datetime,
        text: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Optional[MemoryItem]:
        """Put a proposed (pending) item of the user in use, with a corrected text and /
        or category when they are given. The item it proposed to replace is removed for
        good, in the same transaction. An item that is active already is only given the
        corrections. None when the user has no such item, a rejected one included."""
        with self._database.begin() as connection:
            row = connection.execute(select(memory_items).where(_listed(user_id, item_id))).first()
            if row is None:
                return None
            if row.status != MEMORY_STATUS_PENDING:
                return _change(connection, user_id, item_id, now, text, category)
            connection.execute(
                update(memory_items)
                .where(_owned(user_id, item_id))
                .values(status=MEMORY_STATUS_ACTIVE, replaces_id=None, updated_at=now)
            )
            if row.replaces_id is not None:
                _forget_proposals_on(connection, user_id, memory_items.c.id == row.replaces_id)
                connection.execute(delete(memory_items).where(_owned(user_id, row.replaces_id)))
            return _change(connection, user_id, item_id, now, text, category)

    def delete_item(self, user_id: str, item_id: str, now: datetime) -> bool:
        """Remove an item of the user; False when the user has no such item, a rejected
        one included. An item gathered from a conversation is kept as `rejected`, so that
        it is never proposed again; any other item is deleted for good. A proposal to
        replace the item stays, as a plain proposal."""
        with self._database.begin() as connection:
            row = connection.execute(select(memory_items).where(_listed(user_id, item_id))).first()
            if row is None:
                return False
            _forget_proposals_on(connection, user_id, memory_items.c.id == item_id)
            if row.source == MEMORY_SOURCE_CONVERSATION:
                _reject(connection, user_id, item_id, now)
            else:
                _remove(connection, user_id, item_id)
        return True

    # ── Imports ───────────────────────────────────────────────────────────

    def add_import(self, record: MemoryImport) -> None:
        with self._database.begin() as connection:
            connection.execute(insert(imports).values(**vars(record)))

    def add_items(self, items: list[MemoryItem]) -> None:
        """All the items or none of them."""
        if not items:
            return
        with self._database.begin() as connection:
            connection.execute(insert(memory_items), [vars(item) for item in items])

    def list_imports(self, user_id: str) -> list[tuple[MemoryImport, int]]:
        """The imports of the user, oldest first, each with the number of the user's
        items that name it."""
        item_count = (
            select(func.count())
            .select_from(memory_items)
            .where(
                (memory_items.c.import_id == imports.c.id)
                & (memory_items.c.user_id == user_id)
            )
            .scalar_subquery()
        )
        query = (
            select(imports, item_count.label("item_count"))
            .where(imports.c.user_id == user_id)
            .order_by(imports.c.created_at, imports.c.id)
        )
        with self._database.begin() as connection:
            rows = [dict(row._mapping) for row in connection.execute(query)]
        listed = []
        for row in rows:
            count = row.pop("item_count")
            listed.append((MemoryImport(**row), count))
        return listed

    def get_import(self, user_id: str, import_id: str) -> Optional[MemoryImport]:
        """None when the user has no such import."""
        with self._database.begin() as connection:
            row = connection.execute(
                select(imports).where(_owned_import(user_id, import_id))
            ).first()
        return MemoryImport(**row._mapping) if row else None

    def delete_import(self, user_id: str, import_id: str, delete_items: bool) -> bool:
        """Remove the import of the user; False when the user has no such import. Its
        items are deleted with it, or kept without a source."""
        its_items = (memory_items.c.import_id == import_id) & (memory_items.c.user_id == user_id)
        with self._database.begin() as connection:
            owned = connection.execute(
                select(imports.c.id).where(_owned_import(user_id, import_id))
            ).first()
            if owned is None:
                return False
            if delete_items:
                _forget_proposals_on(connection, user_id, its_items)
                connection.execute(delete(memory_items).where(its_items))
            else:
                connection.execute(update(memory_items).where(its_items).values(import_id=None))
            connection.execute(delete(imports).where(_owned_import(user_id, import_id)))
        return True

    # ── Conversations and their turns ─────────────────────────────────────

    def add_turn(
        self,
        user_id: str,
        conversation_id: str,
        user_text: str,
        answer_text: str,
        stt_confidence: Optional[float],
        interrupted: bool,
        now: datetime,
    ) -> bool:
        """Keep one turn of a conversation of the user, after those already kept; the
        first one writes the row of the conversation. False, and nothing written, when
        the conversation is another user's or was consolidated already."""
        with self._database.begin() as connection:
            row = connection.execute(
                select(conversations).where(conversations.c.id == conversation_id)
            ).first()
            if row is None:
                connection.execute(
                    insert(conversations).values(
                        id=conversation_id, user_id=user_id, started_at=now
                    )
                )
            elif row.user_id != user_id or row.consolidated_at is not None:
                return False
            last = connection.execute(
                select(func.max(turns.c.position)).where(
                    turns.c.conversation_id == conversation_id
                )
            ).scalar()
            connection.execute(
                insert(turns).values(
                    conversation_id=conversation_id,
                    position=(last or 0) + 1,
                    user_text=user_text,
                    answer_text=answer_text,
                    stt_confidence=stt_confidence,
                    interrupted=interrupted,
                    created_at=now,
                )
            )
        return True

    def close_conversation(self, user_id: str, conversation_id: str, now: datetime) -> bool:
        """Note that the conversation is over (the first time only). False when the user
        has no such conversation, or when it was consolidated already: nothing is left
        to read."""
        with self._database.begin() as connection:
            row = connection.execute(
                select(conversations).where(_owned_conversation(user_id, conversation_id))
            ).first()
            if row is None or row.consolidated_at is not None:
                return False
            if row.closed_at is None:
                connection.execute(
                    update(conversations)
                    .where(_owned_conversation(user_id, conversation_id))
                    .values(closed_at=now)
                )
        return True

    def list_turns(self, user_id: str, conversation_id: str) -> list[StoredTurn]:
        """The kept turns of a conversation of the user, in the order of recording."""
        query = (
            select(turns.c.user_text, turns.c.answer_text, turns.c.stt_confidence)
            .join(conversations, conversations.c.id == turns.c.conversation_id)
            .where(_owned_conversation(user_id, conversation_id))
            .order_by(turns.c.position)
        )
        with self._database.begin() as connection:
            return [StoredTurn(**row._mapping) for row in connection.execute(query)]

    def consolidate(
        self,
        user_id: str,
        conversation_id: str,
        now: datetime,
        proposed: Sequence[MemoryItem] = (),
        stated_again: Sequence[StatedAgain] = (),
    ) -> Optional[list[str]]:
        """What was read from a conversation of the user, all of it or nothing: the
        `proposed` items are added, the items `stated_again` are counted once more, the
        turns are deleted and the conversation is marked consolidated. An item is
        counted, and made active when it was to be, only if it still has the status it
        was read with: what the person did to it meanwhile (approved, rejected,
        deleted) stands. The ids of the items that were counted; None, and nothing
        written, when the user has no such conversation or it was consolidated
        already."""
        with self._database.begin() as connection:
            # First, and decided by the update itself: of two transactions on one
            # conversation only one gets past this point.
            marked = connection.execute(
                update(conversations)
                .where(
                    _owned_conversation(user_id, conversation_id)
                    & conversations.c.consolidated_at.is_(None)
                )
                .values(
                    closed_at=func.coalesce(conversations.c.closed_at, now),
                    consolidated_at=now,
                )
            )
            if marked.rowcount == 0:
                return None
            if proposed:
                # An item that was active when the conversation was read may be gone by
                # now: a proposal to replace it becomes a plain proposal.
                replaceable = {
                    found.id
                    for found in connection.execute(
                        select(memory_items.c.id).where(
                            (memory_items.c.user_id == user_id)
                            & (memory_items.c.status == MEMORY_STATUS_ACTIVE)
                        )
                    )
                }
                connection.execute(
                    insert(memory_items),
                    [
                        {
                            **vars(item),
                            "replaces_id": (
                                item.replaces_id if item.replaces_id in replaceable else None
                            ),
                        }
                        for item in proposed
                    ],
                )
            counted = []
            for stated in stated_again:
                changes = {"seen_count": memory_items.c.seen_count + 1, "updated_at": now}
                if stated.promote:
                    changes["status"] = MEMORY_STATUS_ACTIVE
                written = connection.execute(
                    update(memory_items)
                    .where(
                        _owned(user_id, stated.item_id)
                        & (memory_items.c.status == stated.status)
                    )
                    .values(**changes)
                )
                if written.rowcount:
                    counted.append(stated.item_id)
            connection.execute(delete(turns).where(turns.c.conversation_id == conversation_id))
        return counted

    def expire_turns(self, before: datetime, now: datetime) -> int:
        """Housekeeping, for every user: delete the turns recorded before `before`, and
        mark consolidated the conversations left without any turn (there is nothing to
        read in them any more). The number of turns deleted."""
        has_turns = (
            select(turns.c.id).where(turns.c.conversation_id == conversations.c.id).exists()
        )
        with self._database.begin() as connection:
            deleted = connection.execute(delete(turns).where(turns.c.created_at < before))
            connection.execute(
                update(conversations)
                .where(conversations.c.consolidated_at.is_(None) & ~has_turns)
                .values(
                    closed_at=func.coalesce(conversations.c.closed_at, now),
                    consolidated_at=now,
                )
            )
            return deleted.rowcount

    def unconsolidated_conversations(self, started_until: datetime) -> list[tuple[str, str]]:
        """Housekeeping, for every user: the (user id, conversation id) of the
        conversations started up to `started_until` and not consolidated yet, closed or
        not, oldest first."""
        query = (
            select(conversations.c.user_id, conversations.c.id)
            .where(
                conversations.c.consolidated_at.is_(None)
                & (conversations.c.started_at <= started_until)
            )
            .order_by(conversations.c.started_at, conversations.c.id)
        )
        with self._database.begin() as connection:
            return [(row.user_id, row.id) for row in connection.execute(query)]


def _change(
    connection,
    user_id: str,
    item_id: str,
    updated_at: datetime,
    text: Optional[str],
    category: Optional[str],
) -> Optional[MemoryItem]:
    """Give an item of the user the text and / or the category that are given, and
    read it back; with neither, nothing is written. None when the user has no such
    item, a rejected one included."""
    changes = {}
    if text is not None:
        changes["text"] = text
    if category is not None:
        changes["category"] = category
    if changes:
        connection.execute(
            update(memory_items)
            .where(_listed(user_id, item_id))
            .values(updated_at=updated_at, **changes)
        )
    row = connection.execute(select(memory_items).where(_listed(user_id, item_id))).first()
    return _item(row) if row else None


def _reject(connection, user_id: str, item_id: str, now: datetime) -> None:
    """Keep the item as a tombstone: not listed, not used, never proposed again."""
    connection.execute(
        update(memory_items)
        .where(_owned(user_id, item_id))
        .values(status=MEMORY_STATUS_REJECTED, replaces_id=None, updated_at=now)
    )


def _remove(connection, user_id: str, item_id: str) -> None:
    """Delete the item for good."""
    connection.execute(delete(memory_items).where(_owned(user_id, item_id)))


def _owned(user_id: str, item_id: str):
    """The item, only if it belongs to the user."""
    return (memory_items.c.id == item_id) & (memory_items.c.user_id == user_id)


def _listed(user_id: str, item_id: str):
    """The item, only if it belongs to the user and was not rejected: a rejected item
    is a tombstone the profile page neither shows nor reaches."""
    return _owned(user_id, item_id) & (memory_items.c.status != MEMORY_STATUS_REJECTED)


def _forget_proposals_on(connection, user_id: str, removed) -> None:
    """The proposals to replace the items matching `removed`, which are about to go,
    become plain proposals: they point at nothing."""
    targets = [
        row.id
        for row in connection.execute(
            select(memory_items.c.id).where((memory_items.c.user_id == user_id) & removed)
        )
    ]
    if targets:
        connection.execute(
            update(memory_items)
            .where(
                (memory_items.c.user_id == user_id) & memory_items.c.replaces_id.in_(targets)
            )
            .values(replaces_id=None)
        )


def _owned_conversation(user_id: str, conversation_id: str):
    """The conversation, only if it belongs to the user."""
    return (conversations.c.id == conversation_id) & (conversations.c.user_id == user_id)


def _owned_import(user_id: str, import_id: str):
    """The import, only if it belongs to the user."""
    return (imports.c.id == import_id) & (imports.c.user_id == user_id)
