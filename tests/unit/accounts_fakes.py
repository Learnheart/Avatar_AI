"""What the account tests share: the stores on a real SQLite file and a clock."""
from datetime import datetime, timedelta, timezone

from backend.features.accounts.store import AccountsStore, UsersStore
from backend.utils.database import Database

START = datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc)
PASSWORD = "mật khẩu đủ dài"


class Clock:
    """Stands still until it is moved."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


def open_stores(config) -> tuple[Database, UsersStore, AccountsStore]:
    database = Database(config.DATABASE_URL)
    database.start()
    return database, UsersStore(database), AccountsStore(database)
