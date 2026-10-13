"""The application database: opened from DATABASE_URL, its tables created at startup."""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, inspect

from backend.features.accounts.store import UsersStore
from backend.features.memory.store import MemoryStore
from backend.main import create_app
from backend.models import MemoryItem
from backend.utils.database import Database, DatabaseError
from tests.unit.fakes import build_orchestrator

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
# The stores written against the application database (not the sqlite-vec index).
STORES = (
    "backend/features/accounts/store.py",
    "backend/features/memory/store.py",
    "backend/features/persona/store.py",
)


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


TABLES = {"users", "memory_items", "imports", "conversations", "turns", "persona_fields"}


def test_startup_creates_the_tables_in_an_empty_file(config, tmp_path):
    assert not (tmp_path / "app.db").exists()

    with TestClient(create_app(config, build_orchestrator(config))):
        pass

    assert _tables(config.DATABASE_URL) == TABLES


def test_a_second_start_changes_nothing_and_keeps_the_data(config):
    with TestClient(create_app(config, build_orchestrator(config))) as client:
        client.app.state.users.touch("linh", "Linh", NOW)

    with TestClient(create_app(config, build_orchestrator(config))) as client:
        user = client.app.state.users.get("linh")

    assert (user.display_name, user.created_at) == ("Linh", NOW)
    assert _tables(config.DATABASE_URL) == TABLES


def test_an_unreachable_database_fails_startup_with_the_reason(config, tmp_path):
    (tmp_path / "taken").write_text("a file where the directory should be")
    config.DATABASE_URL = f"sqlite:///{tmp_path / 'taken' / 'app.db'}"

    with pytest.raises(DatabaseError, match="Cannot open the database at sqlite:///.*taken"):
        with TestClient(create_app(config, build_orchestrator(config))):
            pass


@pytest.mark.parametrize(
    "url",
    [
        "not a url",
        "postgresql://avatar:s3cret@db.test:port/avatar",  # a port that is no number
        "postgresql://avatar:s3cret@db.test:54x32/avatar",
        "://avatar:s3cret@db.test/avatar",
    ],
)
def test_a_malformed_url_fails_with_the_reason_and_without_the_password(url):
    with pytest.raises(DatabaseError, match="DATABASE_URL is not a database URL") as error:
        Database(url).start()

    assert "s3cret" not in str(error.value)


def test_a_malformed_url_stops_startup_as_a_database_error(config):
    config.DATABASE_URL = "postgresql://avatar:s3cret@db.test:port/avatar"

    with pytest.raises(DatabaseError, match="DATABASE_URL is not a database URL"):
        with TestClient(create_app(config, build_orchestrator(config))):
            pass


def test_an_option_the_engine_refuses_fails_with_the_reason(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'app.db'}?timeout=soon")

    with pytest.raises(DatabaseError, match="Cannot open the database at sqlite:///"):
        database.start()


def test_the_password_of_the_url_is_not_in_the_error():
    # No server is contacted: the driver named in the URL does not exist.
    database = Database("postgresql+nodriver://avatar:s3cret@db.test/avatar")

    with pytest.raises(DatabaseError) as error:
        database.start()

    assert "s3cret" not in str(error.value)
    assert "db.test" in str(error.value)


def test_the_engine_is_released_when_the_tables_cannot_be_created(tmp_path, monkeypatch):
    disposed = []
    monkeypatch.setattr(Engine, "dispose", lambda self, close=True: disposed.append(self))
    (tmp_path / "app.db").write_text("this file is not a database, whatever its name says")
    database = Database(f"sqlite:///{tmp_path / 'app.db'}")

    with pytest.raises(DatabaseError, match="Cannot open the database"):
        database.start()

    assert len(disposed) == 1


def test_the_directory_of_a_sqlite_file_is_created(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'new' / 'deeper' / 'app.db'}")

    database.start()
    database.close()

    assert (tmp_path / "new" / "deeper" / "app.db").is_file()


def test_a_database_that_is_not_started_refuses_to_work(config):
    with pytest.raises(DatabaseError, match="not started"):
        UsersStore(Database(config.DATABASE_URL)).get("linh")


def test_foreign_keys_are_enforced(config):
    database = Database(config.DATABASE_URL)
    database.start()
    try:
        orphan = MemoryItem("item-1", "nobody", "identity", "Tên là Linh.", NOW, NOW)
        with pytest.raises(DatabaseError):
            MemoryStore(database).add_item(orphan)
    finally:
        database.close()


def test_timestamps_come_back_in_utc_with_their_timezone(config):
    hanoi = timezone(timedelta(hours=7))
    database = Database(config.DATABASE_URL)
    database.start()
    try:
        users = UsersStore(database)
        users.touch("linh", "Linh", datetime(2026, 10, 12, 15, 0, tzinfo=hanoi))
        user = users.get("linh")
    finally:
        database.close()

    assert user.created_at == NOW
    assert user.created_at.utcoffset() == timedelta(0)


def test_a_timestamp_without_a_timezone_is_refused(config):
    """Which instant it means cannot be known: nothing is stored."""
    database = Database(config.DATABASE_URL)
    database.start()
    try:
        users = UsersStore(database)
        with pytest.raises(DatabaseError, match="A timestamp needs a timezone"):
            users.touch("linh", "Linh", datetime(2026, 10, 12, 8, 0))
        assert users.count() == 0
    finally:
        database.close()


@pytest.mark.parametrize("path", STORES)
def test_no_store_imports_sqlite3_or_names_an_engine(path):
    source = (REPO_ROOT / path).read_text()

    assert not re.search(r"sqlite|postgres|create_engine|\bEngine\b", source, re.IGNORECASE)
