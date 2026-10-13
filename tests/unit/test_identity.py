"""Who is behind a connection or a request: `current_user_id()`."""
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest

from backend.features.accounts.identity import current_user_id
from backend.features.accounts.store import UsersStore
from backend.utils.database import Database

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def users(config) -> Iterator[UsersStore]:
    database = Database(config.DATABASE_URL)
    database.start()
    yield UsersStore(database)
    database.close()


@pytest.mark.parametrize("configured", ["", "   "])
def test_without_a_configured_user_the_person_is_a_guest(config, users, configured):
    config.AUTH_DEV_USER_ID = configured

    assert current_user_id(config, users) is None
    assert users.count() == 0


def test_the_configured_user_gets_a_row_on_first_use(config, users):
    config.AUTH_DEV_USER_ID = "linh"

    assert current_user_id(config, users, now=NOW) == "linh"

    user = users.get("linh")
    assert (user.id, user.display_name) == ("linh", "linh")
    assert (user.created_at, user.last_seen_at) == (NOW, NOW)


def test_a_later_call_updates_last_seen_and_creates_no_second_row(config, users):
    config.AUTH_DEV_USER_ID = "linh"
    later = NOW + timedelta(hours=2)

    current_user_id(config, users, now=NOW)
    assert current_user_id(config, users, now=later) == "linh"

    user = users.get("linh")
    assert (user.created_at, user.last_seen_at) == (NOW, later)
    assert users.count() == 1


def test_the_time_of_a_call_is_now_by_default(config, users):
    config.AUTH_DEV_USER_ID = "linh"
    before = datetime.now(timezone.utc)

    current_user_id(config, users)

    assert before <= users.get("linh").last_seen_at <= datetime.now(timezone.utc)


# ── A session token ───────────────────────────────────────────────────────

from backend.features.accounts.identity import bearer_token  # noqa: E402
from backend.features.accounts.sessions import SessionService  # noqa: E402
from backend.features.accounts.store import AccountsStore  # noqa: E402
from backend.models import User  # noqa: E402
from backend.utils.database import DatabaseError  # noqa: E402


@pytest.fixture
def sessions(config, users) -> SessionService:
    store = AccountsStore(users._database)
    store.add_account(User("u1", "Linh", NOW, NOW), "linh", "hash")
    return SessionService(config, store)


def test_a_valid_token_is_its_user(config, users, sessions):
    token, _ = sessions.open("u1")

    assert current_user_id(config, users, sessions=sessions, token=token) == "u1"


@pytest.mark.parametrize("token", [None, "", "not-a-token"])
def test_no_token_or_a_bad_one_is_a_guest(config, users, sessions, token):
    sessions.open("u1")

    assert current_user_id(config, users, sessions=sessions, token=token) is None


def test_a_revoked_token_is_a_guest(config, users, sessions):
    token, _ = sessions.open("u1")
    sessions.revoke(token)

    assert current_user_id(config, users, sessions=sessions, token=token) is None


def test_the_configured_user_wins_over_any_token(config, users, sessions):
    token, _ = sessions.open("u1")
    config.AUTH_DEV_USER_ID = "dev"

    assert current_user_id(config, users, sessions=sessions, token=token) == "dev"
    assert current_user_id(config, users, sessions=sessions, token=None) == "dev"


def test_a_failing_database_is_raised_not_taken_for_a_guest(config, users, sessions):
    def down(*args):
        raise DatabaseError("database is gone")

    sessions._store.get_session = down

    with pytest.raises(DatabaseError):
        current_user_id(config, users, sessions=sessions, token="any")


@pytest.mark.parametrize(
    ("header", "token"),
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("Bearer   abc  ", "abc"),
        ("Bearer ", None),
        ("Basic abc", None),
        ("abc", None),
        ("", None),
        (None, None),
    ],
)
def test_the_token_is_read_from_a_bearer_header_only(header, token):
    assert bearer_token(header) == token
