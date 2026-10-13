"""SessionService: the open sign-ins and when they end."""
from datetime import timedelta
from typing import Iterator

import pytest

from backend.features.accounts.sessions import SessionService, session_id
from backend.features.accounts.store import AccountsStore
from backend.models import User
from tests.unit.accounts_fakes import START, Clock, open_stores


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(config) -> Iterator[AccountsStore]:
    database, _, accounts = open_stores(config)
    for user_id in ("linh", "an"):
        accounts.add_account(User(user_id, user_id, START, START), user_id, "hash")
    yield accounts
    database.close()


@pytest.fixture
def sessions(config, store, clock) -> SessionService:
    config.AUTH_SESSION_IDLE_MINUTES, config.AUTH_SESSION_MAX_HOURS = 30, 12
    return SessionService(config, store, clock=clock)


def test_a_token_resolves_to_its_user_and_is_not_stored(sessions, store):
    token, expires_at = sessions.open("linh")

    assert sessions.resolve(token) == ("linh", START + timedelta(minutes=30))
    assert expires_at == START + timedelta(minutes=30)
    assert len(token) >= 43
    assert store.get_session(token) is None
    assert store.get_session(session_id(token)).user_id == "linh"


def test_two_sign_ins_are_two_sessions(sessions):
    first, _ = sessions.open("linh")
    second, _ = sessions.open("linh")

    sessions.revoke(first)

    assert first != second
    assert sessions.resolve(first) is None
    assert sessions.resolve(second)[0] == "linh"


@pytest.mark.parametrize("token", [None, "", "not-a-token"])
def test_no_token_or_an_unknown_one_resolves_to_nobody(sessions, token):
    sessions.open("linh")

    assert sessions.resolve(token) is None
    assert sessions.user_of(token) is None
    sessions.revoke(token)  # nothing to end, nothing raised


def test_a_revoked_session_resolves_to_nobody(sessions):
    token, _ = sessions.open("linh")

    sessions.revoke(token)

    assert sessions.resolve(token) is None
    assert sessions.user_of(token) is None


def test_every_use_pushes_the_end_back(sessions, clock):
    token, _ = sessions.open("linh")

    for _ in range(5):
        clock.advance(minutes=29)
        assert sessions.resolve(token) == ("linh", clock.now + timedelta(minutes=30))


def test_a_session_not_used_for_the_idle_time_has_expired(sessions, clock):
    token, _ = sessions.open("linh")

    clock.advance(minutes=30)

    assert sessions.resolve(token) is None
    assert sessions.user_of(token) is None


def test_a_session_ends_at_its_maximum_age_however_recently_it_was_used(sessions, clock):
    token, _ = sessions.open("linh")
    for _ in range(23):
        clock.advance(minutes=29)
        sessions.resolve(token)  # 11 h 07 of use

    clock.advance(minutes=29)   # 11 h 36: the end is the age, not the idle time
    assert sessions.resolve(token) == ("linh", START + timedelta(hours=12))
    clock.advance(minutes=24)   # 12 h

    assert sessions.resolve(token) is None


def test_looking_at_a_session_does_not_keep_it_alive(sessions, clock):
    token, _ = sessions.open("linh")

    for _ in range(3):
        clock.advance(minutes=9)
        assert sessions.user_of(token) == "linh"
    clock.advance(minutes=3)

    assert sessions.user_of(token) is None


def test_the_limits_follow_the_config_for_sessions_that_are_open(sessions, clock, config):
    token, _ = sessions.open("linh")
    clock.advance(minutes=45)
    assert sessions.resolve(token) is None

    config.AUTH_SESSION_IDLE_MINUTES = 60

    assert sessions.resolve(token)[0] == "linh"


def test_ended_sessions_are_deleted(sessions, store, clock):
    revoked, _ = sessions.open("linh")
    unused, _ = sessions.open("linh")
    sessions.revoke(revoked)
    clock.advance(minutes=31)
    kept, _ = sessions.open("an")

    assert sessions.delete_ended() == 2

    assert store.get_session(session_id(kept)) is not None
    assert store.get_session(session_id(unused)) is None
