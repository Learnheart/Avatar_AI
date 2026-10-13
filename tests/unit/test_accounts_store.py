"""Credentials, identities and sessions in the application database, on a real SQLite
file."""
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from typing import Iterator

import pytest

from backend.features.accounts.store import (
    DETACHED, LAST_WAY, NOT_LINKED, AccountsStore, UsersStore,
)
from backend.models import Identity, Session, User
from tests.unit.accounts_fakes import START, open_stores

LATER = START + timedelta(minutes=5)


@pytest.fixture
def stores(config) -> Iterator[tuple[UsersStore, AccountsStore]]:
    database, users, accounts = open_stores(config)
    yield users, accounts
    database.close()


@pytest.fixture
def users(stores) -> UsersStore:
    return stores[0]


@pytest.fixture
def store(stores) -> AccountsStore:
    return stores[1]


def _user(user_id: str = "u1", name: str = "Linh") -> User:
    return User(id=user_id, display_name=name, created_at=START, last_seen_at=START)


# ── Local accounts ────────────────────────────────────────────────────────

def test_an_account_creates_its_user_and_its_credentials(store, users):
    assert store.add_account(_user(), "linh", "hash-1") is True

    found = store.get_credentials("linh")
    assert (found.email, found.user_id, found.password_hash) == ("linh", "u1", "hash-1")
    assert (found.failed_attempts, found.first_failure_at) == (0, None)
    assert users.get("u1").display_name == "Linh"


def test_a_taken_email_creates_nothing(store, users):
    store.add_account(_user(), "linh", "hash-1")

    assert store.add_account(_user("u2", "Another"), "linh", "hash-2") is False

    assert store.get_credentials("linh").user_id == "u1"
    assert users.get("u2") is None and users.count() == 1


def test_an_unknown_email_has_no_credentials(store):
    assert store.get_credentials("nobody") is None


WINDOW = timedelta(minutes=10)


def test_the_attempts_on_an_email_are_counted_and_cleared(store):
    store.add_account(_user(), "linh", "hash-1")

    charged = [store.charge_attempt("linh", START + timedelta(minutes=n), WINDOW, 5) for n in range(3)]
    counted = store.get_credentials("linh")
    store.clear_attempts("linh")

    assert charged == [True] * 3
    assert (counted.failed_attempts, counted.first_failure_at) == (3, START)
    cleared = store.get_credentials("linh")
    assert (cleared.failed_attempts, cleared.first_failure_at) == (0, None)


def test_an_attempt_past_the_limit_is_refused_and_not_counted(store):
    store.add_account(_user(), "linh", "hash-1")

    charged = [store.charge_attempt("linh", START, WINDOW, 2) for _ in range(4)]

    assert charged == [True, True, False, False]
    assert store.get_credentials("linh").failed_attempts == 2


def test_the_count_restarts_once_the_window_of_the_first_attempt_has_passed(store):
    store.add_account(_user(), "linh", "hash-1")
    for _ in range(2):
        store.charge_attempt("linh", START, WINDOW, 2)
    almost = START + WINDOW - timedelta(seconds=1)

    assert store.charge_attempt("linh", almost, WINDOW, 2) is False
    assert store.charge_attempt("linh", START + WINDOW, WINDOW, 2) is True

    counted = store.get_credentials("linh")
    assert (counted.failed_attempts, counted.first_failure_at) == (1, START + WINDOW)


def test_an_attempt_is_charged_to_its_email_only(store):
    store.add_account(_user(), "linh", "hash-1")
    store.add_account(_user("u2", "An"), "an", "hash-2")

    assert store.charge_attempt("nobody", START, WINDOW, 2) is False
    assert store.charge_attempt("linh", START, WINDOW, 2) is True

    assert store.get_credentials("an").failed_attempts == 0


def test_attempts_charged_at_the_same_moment_are_all_counted(store):
    store.add_account(_user(), "linh", "hash-1")
    ready = threading.Barrier(32)

    def charge(_) -> bool:
        ready.wait()
        return store.charge_attempt("linh", START, WINDOW, 5)

    with ThreadPoolExecutor(max_workers=32) as pool:
        charged = list(pool.map(charge, range(32)))

    assert charged.count(True) == 5
    assert store.get_credentials("linh").failed_attempts == 5


# ── Identities ────────────────────────────────────────────────────────────

def _identity(user_id: str = "u1", provider: str = "google", subject: str = "g-1") -> Identity:
    return Identity(provider, subject, user_id, "linh@example.com", START)


def test_an_identity_can_create_its_user(store, users):
    assert store.identity_user("google", "g-1") is None

    assert store.add_identity(_identity(), _user()) == "u1"

    assert store.identity_user("google", "g-1") == "u1"
    assert users.get("u1") is not None
    assert store.list_identities("u1") == [_identity()]


def test_an_identity_somebody_has_is_not_given_to_another(store, users):
    store.add_identity(_identity(), _user())

    assert store.add_identity(_identity("u2"), _user("u2")) == "u1"

    assert users.get("u2") is None
    assert store.identity_user("google", "g-1") == "u1"


def test_an_identity_is_linked_to_an_existing_user(store):
    store.add_account(_user(), "linh", "hash-1")

    assert store.add_identity(_identity()) == "u1"
    assert store.add_identity(_identity(provider="facebook", subject="f-1")) == "u1"

    assert [found.provider for found in store.list_identities("u1")] == ["facebook", "google"]


def test_a_user_has_one_identity_per_provider(store):
    store.add_account(_user(), "linh", "hash-1")
    store.add_identity(_identity())

    assert store.add_identity(_identity(subject="g-2")) is None

    assert store.identity_user("google", "g-2") is None


def test_the_same_subject_at_two_providers_is_two_identities(store):
    store.add_identity(_identity(), _user())

    assert store.add_identity(_identity("u2", "facebook", "g-1"), _user("u2")) == "u2"


def test_an_identity_is_detached_from_its_user_only(store):
    store.add_account(_user(), "linh", "hash-1")  # a password remains to sign in with
    store.add_identity(_identity())
    store.add_identity(_identity("u2", subject="g-2"), _user("u2"))

    assert store.detach_identity("u1", "facebook") == NOT_LINKED
    assert store.detach_identity("u1", "google") == DETACHED

    assert store.list_identities("u1") == []
    assert store.identity_user("google", "g-2") == "u2"
    # The only way the other user has to sign in is not taken from them.
    assert store.detach_identity("u2", "google") == LAST_WAY
    assert store.identity_user("google", "g-2") == "u2"


# ── Sessions ──────────────────────────────────────────────────────────────

def _session(session_id: str = "s1", user_id: str = "u1", **fields) -> Session:
    return Session(**{"id": session_id, "user_id": user_id, "created_at": START,
                      "last_seen_at": START, **fields})


def test_a_session_is_read_back_and_touched(store):
    store.add_account(_user(), "linh", "hash-1")
    store.add_session(_session())

    store.touch_session("s1", LATER)

    assert store.get_session("s1") == _session(last_seen_at=LATER)
    assert store.get_session("other") is None


def test_a_session_is_revoked_once(store):
    store.add_account(_user(), "linh", "hash-1")
    store.add_session(_session())

    assert store.revoke_session("s1", LATER) is True
    assert store.revoke_session("s1", LATER + timedelta(minutes=1)) is False
    assert store.revoke_session("other", LATER) is False

    assert store.get_session("s1").revoked_at == LATER


def test_ended_sessions_are_deleted_and_the_open_ones_kept(store):
    store.add_account(_user(), "linh", "hash-1")
    hour = timedelta(hours=1)
    store.add_session(_session("open", created_at=START + 3 * hour, last_seen_at=START + 5 * hour))
    store.add_session(_session("revoked", revoked_at=START + 5 * hour,
                               created_at=START + 3 * hour, last_seen_at=START + 5 * hour))
    store.add_session(_session("unused", created_at=START + 3 * hour, last_seen_at=START + 3 * hour))
    store.add_session(_session("old", created_at=START, last_seen_at=START + 5 * hour))

    deleted = store.delete_ended_sessions(
        unused_since=START + 4 * hour, created_before=START + 2 * hour
    )

    assert deleted == 3
    assert store.get_session("open") is not None
    assert [store.get_session(name) for name in ("revoked", "unused", "old")] == [None] * 3


def test_a_session_needs_its_user(store):
    from backend.utils.database import DatabaseError

    with pytest.raises(DatabaseError):
        store.add_session(_session(user_id="nobody"))


def test_accounts_survive_a_reopen(config):
    database, _, store = open_stores(config)
    store.add_account(_user(), "linh", "hash-1")
    store.add_identity(_identity())
    store.add_session(_session())
    database.close()

    database, users, store = open_stores(config)

    assert store.get_credentials("linh").user_id == "u1"
    assert store.identity_user("google", "g-1") == "u1"
    assert store.get_session("s1") == _session()
    assert users.get("u1") == replace(_user())
    database.close()
