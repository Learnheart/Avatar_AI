"""
The application database: users, the memory and whatever else is per user or system-wide.

One SQLAlchemy Core engine built from `DATABASE_URL` (a SQLite file locally, PostgreSQL at
deployment). This is the only module that knows engines: a feature declares its tables on
`metadata` in its own `store.py` and reaches the database through `Database.begin()`.
There is no migration tool yet: the tables that are missing are created at startup.

Every method blocks; call them off the event loop.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import Connection, DateTime, Engine, MetaData, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.types import TypeDecorator

# Every table of the application database, declared by the stores of the features.
metadata = MetaData()

_SQLITE = "sqlite"


class DatabaseError(Exception):
    """The application database failed (unreachable, refused a statement)."""


class UTCDateTime(TypeDecorator):
    """A timezone-aware UTC timestamp on every engine (SQLite keeps no timezone)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], dialect) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("A timestamp needs a timezone")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: Optional[datetime], dialect) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _reason(error: Exception) -> str:
    """The driver's own message, without the statement and its parameters."""
    return str(getattr(error, "orig", None) or error)


class Database:
    def __init__(self, url: str) -> None:
        self._url = url
        self._engine: Optional[Engine] = None

    def start(self) -> None:
        """Connect and create the tables that are missing; existing tables and their rows
        are left as they are. Raises DatabaseError with the reason when the database
        cannot be reached."""
        try:
            url = make_url(self._url)
            shown = url.render_as_string(hide_password=True)
        except (SQLAlchemyError, ValueError) as e:
            # The parser's own message may quote a piece of the URL: it is not repeated.
            raise DatabaseError(
                "DATABASE_URL is not a database URL: expected "
                "dialect://user:password@host:port/name, the port being a number"
            ) from e
        engine: Optional[Engine] = None
        try:
            sqlite = url.get_backend_name() == _SQLITE
            if sqlite and url.database:
                Path(url.database).parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(url)
            if sqlite:
                event.listen(engine, "connect", _enable_foreign_keys)
            metadata.create_all(engine)
        except Exception as e:  # the driver, the file system, an option of the URL
            if engine is not None:
                engine.dispose()
            raise DatabaseError(f"Cannot open the database at {shown}: {_reason(e)}") from e
        self._engine = engine

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    @contextmanager
    def begin(self) -> Iterator[Connection]:
        """One transaction: committed when the block ends, rolled back when it raises.
        A failure of the database is raised as DatabaseError."""
        if self._engine is None:
            raise DatabaseError("The database is not started")
        try:
            with self._engine.begin() as connection:
                yield connection
        except SQLAlchemyError as e:
            raise DatabaseError(_reason(e)) from e


def _enable_foreign_keys(dbapi_connection, _record) -> None:
    """SQLite checks foreign keys only when asked to, on each connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
