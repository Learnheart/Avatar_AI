"""
The memory items of the application database. All the SQL of the memory is here, written
against SQLAlchemy Core. Every method takes the user id: no query reads across users.
Every method blocks; call them off the event loop.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, ForeignKey, Integer, String, Table, Text, delete, insert, select, update

from backend.features.accounts.store import users
from backend.models import MemoryItem
from backend.utils.database import Database, UTCDateTime, metadata

memory_items = Table(
    "memory_items",
    metadata,
    Column("id", String, primary_key=True),
    Column("user_id", String, ForeignKey(users.c.id), nullable=False, index=True),
    Column("category", String, nullable=False),
    Column("text", Text, nullable=False),
    Column("status", String, nullable=False),
    Column("source", String, nullable=False),
    # Set when the source is an import. A foreign key once the imports are stored.
    Column("import_id", String),
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


def _owned(user_id: str, item_id: str):
    """The item, only if it belongs to the user."""
    return (memory_items.c.id == item_id) & (memory_items.c.user_id == user_id)
