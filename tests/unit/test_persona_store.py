"""The persona fields in the application database, on a real SQLite file."""
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest

from backend.features.accounts.store import UsersStore
from backend.features.persona.store import PersonaStore
from backend.utils.database import Database, DatabaseError

NOW = datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=5)
MAI = {"name": "Mai", "addressing": "xưng em, gọi anh"}


def _open(config) -> tuple[Database, PersonaStore]:
    database = Database(config.DATABASE_URL)
    database.start()
    users = UsersStore(database)
    for user_id in ("linh", "an"):
        users.touch(user_id, user_id, NOW)
    return database, PersonaStore(database)


@pytest.fixture
def store(config) -> Iterator[PersonaStore]:
    database, persona_store = _open(config)
    yield persona_store
    database.close()


def test_a_user_starts_without_persona(store):
    assert store.get("linh") == {}


def test_the_saved_fields_are_read_back(store):
    store.replace("linh", MAI, NOW)

    assert store.get("linh") == MAI


def test_saving_again_replaces_the_whole_persona(store):
    store.replace("linh", MAI, NOW)

    store.replace("linh", {"name": "Lan", "avoid": "Không nói về chính trị"}, LATER)

    assert store.get("linh") == {"name": "Lan", "avoid": "Không nói về chính trị"}


def test_saving_nothing_removes_the_persona(store):
    store.replace("linh", MAI, NOW)

    store.replace("linh", {}, LATER)

    assert store.get("linh") == {}


def test_one_user_never_reads_or_changes_another_s_persona(store):
    store.replace("linh", MAI, NOW)
    store.replace("an", {"name": "Bông"}, NOW)

    store.replace("an", {}, LATER)

    assert store.get("linh") == MAI
    assert store.get("an") == {}


def test_the_persona_survives_a_reopen(config):
    database, store = _open(config)
    store.replace("linh", MAI, NOW)
    database.close()

    database, store = _open(config)

    assert store.get("linh") == MAI
    database.close()


def test_a_persona_needs_its_user(store):
    with pytest.raises(DatabaseError):
        store.replace("nobody", MAI, NOW)


def test_a_failed_save_leaves_the_persona_as_it_was(store):
    store.replace("linh", MAI, NOW)

    with pytest.raises(DatabaseError):
        store.replace("linh", {"name": None}, LATER)

    assert store.get("linh") == MAI
