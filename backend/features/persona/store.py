"""
The persona fields of the application database: the personality a user gave the avatar,
one row per field that is set. All the SQL of the persona is here, written against
SQLAlchemy Core. Every method takes the user id: no query reads across users. Every
method blocks; call them off the event loop.
"""
from datetime import datetime
from typing import Mapping

from sqlalchemy import Column, ForeignKey, String, Table, Text, delete, insert, select

from backend.features.accounts.store import users
from backend.utils.database import Database, UTCDateTime, metadata

persona_fields = Table(
    "persona_fields",
    metadata,
    Column("user_id", String, ForeignKey(users.c.id), primary_key=True),
    # One of PERSONA_FIELDS (backend/models.py).
    Column("field", String, primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", UTCDateTime, nullable=False),
)


class PersonaStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get(self, user_id: str) -> dict[str, str]:
        """The fields the user set, by name; {} when none is."""
        query = select(persona_fields.c.field, persona_fields.c.value).where(
            persona_fields.c.user_id == user_id
        )
        with self._database.begin() as connection:
            return {row.field: row.value for row in connection.execute(query)}

    def replace(self, user_id: str, fields: Mapping[str, str], now: datetime) -> None:
        """Make `fields` the whole persona of the user, in one transaction: a field
        that is not in it is gone. {} removes the persona."""
        with self._database.begin() as connection:
            connection.execute(delete(persona_fields).where(persona_fields.c.user_id == user_id))
            if fields:
                connection.execute(
                    insert(persona_fields),
                    [
                        {"user_id": user_id, "field": name, "value": value, "updated_at": now}
                        for name, value in fields.items()
                    ],
                )
