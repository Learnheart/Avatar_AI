"""
The users of the application database. All the SQL of the accounts is here, written
against SQLAlchemy Core. Every method blocks; call them off the event loop.
"""
import threading
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String, Table, func, insert, select, update

from backend.models import User
from backend.utils.database import Database, UTCDateTime, metadata

users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),
    Column("display_name", String, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("last_seen_at", UTCDateTime, nullable=False),
)


class UsersStore:
    def __init__(self, database: Database) -> None:
        self._database = database
        # Two first uses of the same user at once would both insert the row.
        self._lock = threading.Lock()

    def touch(self, user_id: str, display_name: str, now: datetime) -> None:
        """Note that the user was seen at `now`; the row is created on first use, with
        `display_name`."""
        with self._lock, self._database.begin() as connection:
            seen = connection.execute(
                update(users).where(users.c.id == user_id).values(last_seen_at=now)
            )
            if seen.rowcount == 0:
                connection.execute(
                    insert(users).values(
                        id=user_id, display_name=display_name, created_at=now, last_seen_at=now
                    )
                )

    def get(self, user_id: str) -> Optional[User]:
        with self._database.begin() as connection:
            row = connection.execute(select(users).where(users.c.id == user_id)).first()
        return User(**row._mapping) if row else None

    def count(self) -> int:
        with self._database.begin() as connection:
            return connection.execute(select(func.count()).select_from(users)).scalar_one()
