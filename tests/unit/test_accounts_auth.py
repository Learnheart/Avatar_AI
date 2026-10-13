"""AccountService: creating a local account and signing in with it."""
from typing import Iterator

import pytest

from backend.features.accounts.accounts import (
    AccountService,
    InvalidAccount,
    RegistrationClosed,
    TooManyAttempts,
    UsernameTaken,
    WrongCredentials,
)
from backend.features.accounts.store import AccountsStore, UsersStore
from tests.unit.accounts_fakes import PASSWORD, Clock, open_stores


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def stores(config) -> Iterator[tuple[UsersStore, AccountsStore]]:
    database, users, accounts = open_stores(config)
    yield users, accounts
    database.close()


@pytest.fixture
def accounts(config, stores, clock) -> AccountService:
    return AccountService(config, stores[1], clock=clock)


def test_an_account_is_created_with_an_opaque_id_and_signs_in(accounts, stores):
    user = accounts.register("Linh.Nguyen", PASSWORD, "  Khánh\nLinh ")

    assert user.display_name == "Khánh Linh"
    assert user.id != "linh.nguyen" and len(user.id) == 32
    assert accounts.login("linh.nguyen", PASSWORD) == user.id
    assert stores[0].get(user.id).display_name == "Khánh Linh"


def test_the_user_name_is_compared_without_case_or_surrounding_spaces(accounts):
    user = accounts.register("Linh", PASSWORD)

    assert accounts.login("  LINH ", PASSWORD) == user.id
    with pytest.raises(UsernameTaken):
        accounts.register("lInH", PASSWORD)


def test_without_a_display_name_the_user_name_is_shown(accounts):
    assert accounts.register("linh", PASSWORD).display_name == "linh"


def test_the_password_is_stored_hashed(accounts, stores):
    accounts.register("linh", PASSWORD)

    stored = stores[1].get_credentials("linh").password_hash
    assert PASSWORD not in stored and stored.startswith("scrypt$")


@pytest.mark.parametrize(
    ("username", "password", "display_name"),
    [
        ("ab", PASSWORD, None),
        ("x" * 33, PASSWORD, None),
        ("linh nguyen", PASSWORD, None),
        ("linh@home", PASSWORD, None),
        ("lính", PASSWORD, None),
        ("linh", "1234567", None),
        ("linh", "x" * 257, None),
        ("linh", PASSWORD, "x" * 65),
    ],
)
def test_an_account_that_cannot_be_accepted_stores_nothing(
    accounts, stores, username, password, display_name
):
    with pytest.raises(InvalidAccount):
        accounts.register(username, password, display_name)

    assert stores[0].count() == 0


def test_the_minimum_password_length_follows_the_config(accounts, config):
    config.AUTH_PASSWORD_MIN_CHARS = 4

    assert accounts.register("linh", "abcd").id
    with pytest.raises(InvalidAccount, match="4 to 256"):
        accounts.register("anh", "abc")


def test_registration_can_be_switched_off(accounts, config, stores):
    config.AUTH_ALLOW_REGISTRATION = 0

    with pytest.raises(RegistrationClosed):
        accounts.register("linh", PASSWORD)

    assert stores[0].count() == 0


def test_a_wrong_password_and_an_unknown_user_name_are_refused_alike(accounts):
    accounts.register("linh", PASSWORD)

    with pytest.raises(WrongCredentials) as wrong_password:
        accounts.login("linh", "not the password")
    with pytest.raises(WrongCredentials) as unknown_name:
        accounts.login("nobody", PASSWORD)

    assert str(wrong_password.value) == str(unknown_name.value)


def test_a_user_name_is_held_after_too_many_failures_whatever_the_password(accounts, config):
    config.AUTH_LOGIN_MAX_ATTEMPTS = 3
    accounts.register("linh", PASSWORD)

    for _ in range(3):
        with pytest.raises(WrongCredentials):
            accounts.login("linh", "guess")

    with pytest.raises(TooManyAttempts):
        accounts.login("linh", "guess")
    with pytest.raises(TooManyAttempts):
        accounts.login("linh", PASSWORD)


def test_a_held_user_name_is_free_again_once_the_window_has_passed(accounts, config, clock):
    config.AUTH_LOGIN_MAX_ATTEMPTS, config.AUTH_LOGIN_WINDOW_MINUTES = 2, 10
    user = accounts.register("linh", PASSWORD)
    for _ in range(2):
        with pytest.raises(WrongCredentials):
            accounts.login("linh", "guess")

    clock.advance(minutes=9)
    with pytest.raises(TooManyAttempts):
        accounts.login("linh", PASSWORD)
    clock.advance(minutes=1)

    assert accounts.login("linh", PASSWORD) == user.id


def test_a_sign_in_that_goes_through_forgets_the_failures(accounts, config, stores):
    config.AUTH_LOGIN_MAX_ATTEMPTS = 3
    user = accounts.register("linh", PASSWORD)
    for _ in range(2):
        with pytest.raises(WrongCredentials):
            accounts.login("linh", "guess")

    assert accounts.login("linh", PASSWORD) == user.id

    assert stores[1].get_credentials("linh").failed_attempts == 0
    for _ in range(2):
        with pytest.raises(WrongCredentials):
            accounts.login("linh", "guess")
    assert accounts.login("linh", PASSWORD) == user.id


def test_failures_older_than_the_window_are_not_counted(accounts, config, clock):
    config.AUTH_LOGIN_MAX_ATTEMPTS, config.AUTH_LOGIN_WINDOW_MINUTES = 2, 10
    user = accounts.register("linh", PASSWORD)
    with pytest.raises(WrongCredentials):
        accounts.login("linh", "guess")
    clock.advance(minutes=11)

    with pytest.raises(WrongCredentials):
        accounts.login("linh", "guess")

    assert accounts.login("linh", PASSWORD) == user.id


def test_one_user_name_held_leaves_the_others(accounts, config):
    config.AUTH_LOGIN_MAX_ATTEMPTS = 1
    accounts.register("linh", PASSWORD)
    an = accounts.register("anh", PASSWORD)
    with pytest.raises(WrongCredentials):
        accounts.login("linh", "guess")

    assert accounts.login("anh", PASSWORD) == an.id
