"""
The memory items and the imports of the application database. All the SQL of the memory is
here, written against SQLAlchemy Core. Every method takes the user id: no query reads
across users. Every method blocks; call them off the event loop.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column,
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
from backend.models import MemoryImport, MemoryItem
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


def _item(row) -> MemoryItem:
    return MemoryItem(**row._mapping)


class MemoryStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    def add_item(self, item: MemoryItem) -> None:
        with self._database.begin() as connection:
            connection.execute(insert(memory_items).values(**vars(item)))

    def list_items(self, user_id: str, status: Optional[str] = None) -> list[MemoryItem]:
        """The items of the user, oldest first; only those of `status` when it is given."""
        query = select(memory_items).where(memory_items.c.user_id == user_id)
        if status is not None:
            query = query.where(memory_items.c.status == status)
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
        user has no such item."""
        changes = {"updated_at": updated_at}
        if text is not None:
            changes["text"] = text
        if category is not None:
            changes["category"] = category
        with self._database.begin() as connection:
            connection.execute(
                update(memory_items).where(_owned(user_id, item_id)).values(**changes)
            )
            row = connection.execute(select(memory_items).where(_owned(user_id, item_id))).first()
        return _item(row) if row else None

    def delete_item(self, user_id: str, item_id: str) -> bool:
        """False when the user has no such item."""
        with self._database.begin() as connection:
            deleted = connection.execute(delete(memory_items).where(_owned(user_id, item_id)))
            return deleted.rowcount > 0


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
                connection.execute(delete(memory_items).where(its_items))
            else:
                connection.execute(update(memory_items).where(its_items).values(import_id=None))
            connection.execute(delete(imports).where(_owned_import(user_id, import_id)))
        return True


def _owned(user_id: str, item_id: str):
    """The item, only if it belongs to the user."""
    return (memory_items.c.id == item_id) & (memory_items.c.user_id == user_id)


def _owned_import(user_id: str, import_id: str):
    """The import, only if it belongs to the user."""
    return (imports.c.id == import_id) & (imports.c.user_id == user_id)
