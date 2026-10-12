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
