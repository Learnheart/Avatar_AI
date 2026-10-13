"""AccountService: creating a local account, proved by a code sent to its email, and
signing in with it."""
from typing import Iterator

import pytest

from backend.features.accounts.accounts import (
    AccountService,
    CodeExpired,
    CodeJustSent,
    CodeNotSent,
    EmailTaken,
    InvalidAccount,
    RegistrationClosed,
    TooManyAttempts,
    WrongCode,
    WrongCredentials,
)
from backend.features.accounts.store import AccountsStore, UsersStore
from tests.unit.accounts_fakes import PASSWORD, CapturingMailer, Clock, open_stores

LINH = "linh@example.com"


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def mailer() -> CapturingMailer:
    return CapturingMailer()


@pytest.fixture
def stores(config) -> Iterator[tuple[UsersStore, AccountsStore]]:
    database, users, accounts = open_stores(config)
    yield users, accounts
    database.close()


@pytest.fixture
def accounts(config, stores, mailer, clock) -> AccountService:
    return AccountService(config, stores[1], mailer, clock=clock)


def _create(accounts: AccountService, mailer: CapturingMailer, email: str = LINH, **fields):
    accounts.start_registration(email, PASSWORD, **fields)
    return accounts.verify_registration(email, mailer.code(email))


# ── Creating an account ───────────────────────────────────────────────────

def test_asking_for_an_account_sends_a_code_and_creates_nothing(accounts, mailer, stores, config):
    sent = accounts.start_registration("  Linh@Example.com ", PASSWORD)

    assert sent.to_dict() == {"email": LINH, "expires_in_sec": 600, "resend_in_sec": 60}
    ((to, subject, body),) = mailer.sent
    assert to == LINH and subject == config.AUTH_CODE_MAIL_SUBJECT
    assert body == config.AUTH_CODE_MAIL_BODY.format(code=mailer.code(), minutes=10)
    assert stores[0].count() == 0 and stores[1].get_credentials(LINH) is None


def test_the_code_creates_the_account_with_an_opaque_id(accounts, mailer, stores):
    user = _create(accounts, mailer, display_name="  Khánh\nLinh ")

    assert user.display_name == "Khánh Linh"
    assert user.id != LINH and len(user.id) == 32
    assert accounts.login(LINH, PASSWORD) == user.id
    assert stores[0].get(user.id).display_name == "Khánh Linh"


def test_the_email_is_compared_without_case_or_surrounding_spaces(accounts, mailer):
    accounts.start_registration("Linh@Example.COM", PASSWORD)
    user = accounts.verify_registration(" LINH@example.com ", mailer.code(LINH))

    assert accounts.login("  LINH@EXAMPLE.com ", PASSWORD) == user.id
    with pytest.raises(EmailTaken):
        accounts.start_registration("lInH@example.com", PASSWORD)


def test_without_a_display_name_the_part_before_the_at_is_shown(accounts, mailer):
    assert _create(accounts, mailer).display_name == "linh"


def test_the_password_and_the_code_are_kept_hashed(accounts, mailer, stores):
    accounts.start_registration(LINH, PASSWORD)
    waiting = accounts._pending[LINH]
    code = mailer.code()

    assert PASSWORD not in waiting.password_hash and waiting.password_hash.startswith("scrypt$")
    assert code.encode() not in waiting.code_digest and len(waiting.code_digest) == 32

    accounts.verify_registration(LINH, code)
    stored = stores[1].get_credentials(LINH).password_hash
    assert PASSWORD not in stored and stored.startswith("scrypt$")
    assert accounts._pending == {}


def test_a_code_is_six_digits_and_differs_from_one_asking_to_the_next(accounts, mailer, clock):
    codes = set()
    for _ in range(8):
        accounts.start_registration(LINH, PASSWORD)
        codes.add(mailer.code())
        clock.advance(seconds=61)

    assert all(len(code) == 6 and code.isdigit() for code in codes)
    assert len(codes) > 1


def test_spaces_in_the_code_as_it_is_typed_are_ignored(accounts, mailer):
    accounts.start_registration(LINH, PASSWORD)
    code = mailer.code()

    assert accounts.verify_registration(LINH, f" {code[:3]} {code[3:]} ").id


@pytest.mark.parametrize(
    ("email", "password", "display_name"),
    [
        ("linh", PASSWORD, None),
        ("linh@", PASSWORD, None),
        ("@example.com", PASSWORD, None),
        ("linh@example", PASSWORD, None),
        ("linh nguyen@example.com", PASSWORD, None),
        ("a@b@example.com", PASSWORD, None),
        ("x" * 250 + "@example.com", PASSWORD, None),
        (LINH, "1234567", None),
        (LINH, "x" * 257, None),
        (LINH, PASSWORD, "x" * 65),
    ],
)
def test_an_account_that_cannot_be_accepted_sends_no_code(
    accounts, mailer, email, password, display_name
):
    with pytest.raises(InvalidAccount):
        accounts.start_registration(email, password, display_name)

    assert mailer.sent == [] and accounts._pending == {}


def test_the_minimum_password_length_follows_the_config(accounts, config):
    config.AUTH_PASSWORD_MIN_CHARS = 4

    assert accounts.start_registration(LINH, "abcd").email == LINH
    with pytest.raises(InvalidAccount, match="4 to 256"):
        accounts.start_registration("an@example.com", "abc")


def test_registration_can_be_switched_off(accounts, config, mailer):
    config.AUTH_ALLOW_REGISTRATION = 0

    with pytest.raises(RegistrationClosed):
        accounts.start_registration(LINH, PASSWORD)

    assert mailer.sent == []


def test_one_email_has_one_account(accounts, mailer, stores):
    first = _create(accounts, mailer)

    with pytest.raises(EmailTaken):
        accounts.start_registration(LINH, "another password")

    assert len(mailer.sent) == 1
    assert stores[0].count() == 1 and accounts.login(LINH, PASSWORD) == first.id


def test_two_people_asking_for_the_same_email_end_in_one_account(accounts, mailer, clock, stores):
    accounts.start_registration(LINH, PASSWORD)
    first_code = mailer.code()
    clock.advance(seconds=61)
    accounts.start_registration(LINH, "the password of the second")  # replaces the first

    with pytest.raises(WrongCode):
        accounts.verify_registration(LINH, first_code)
    user = accounts.verify_registration(LINH, mailer.code())

    assert stores[0].count() == 1
    assert accounts.login(LINH, "the password of the second") == user.id
    with pytest.raises(WrongCredentials):
        accounts.login(LINH, PASSWORD)


# ── The code ──────────────────────────────────────────────────────────────

def test_a_wrong_code_creates_nothing_and_the_right_one_still_works(accounts, mailer, stores):
    accounts.start_registration(LINH, PASSWORD)
    code = mailer.code()
    wrong = "000000" if code != "000000" else "111111"

    with pytest.raises(WrongCode):
        accounts.verify_registration(LINH, wrong)
    assert stores[0].count() == 0

    assert accounts.verify_registration(LINH, code).id


def test_a_code_is_dropped_after_too_many_wrong_tries(accounts, mailer, config, stores):
    config.AUTH_OTP_MAX_ATTEMPTS = 3
    accounts.start_registration(LINH, PASSWORD)
    code = mailer.code()
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(3):
        with pytest.raises(WrongCode):
            accounts.verify_registration(LINH, wrong)

    with pytest.raises(CodeExpired):
        accounts.verify_registration(LINH, code)  # the right code, too late
    assert stores[0].count() == 0


def test_a_code_expires(accounts, mailer, clock, config):
    config.AUTH_OTP_TTL_MINUTES = 10
    accounts.start_registration(LINH, PASSWORD)
    code = mailer.code()

    clock.advance(minutes=10)

    with pytest.raises(CodeExpired):
        accounts.verify_registration(LINH, code)


def test_a_code_works_once(accounts, mailer):
    accounts.start_registration(LINH, PASSWORD)
    code = mailer.code()
    accounts.verify_registration(LINH, code)

    with pytest.raises(CodeExpired):
        accounts.verify_registration(LINH, code)


def test_no_code_waits_for_an_email_that_asked_for_none(accounts):
    with pytest.raises(CodeExpired):
        accounts.verify_registration("nobody@example.com", "123456")


def test_the_code_of_one_email_does_not_open_another(accounts, mailer):
    accounts.start_registration(LINH, PASSWORD)
    accounts.start_registration("an@example.com", PASSWORD)

    with pytest.raises(WrongCode):
        accounts.verify_registration("an@example.com", mailer.code(LINH))


def test_another_code_can_be_asked_for_only_after_a_while(accounts, mailer, clock, config):
    config.AUTH_OTP_RESEND_SEC = 60
    accounts.start_registration(LINH, PASSWORD)
    first = mailer.code()
    clock.advance(seconds=20)

    with pytest.raises(CodeJustSent) as refused:
        accounts.start_registration(LINH, PASSWORD)
    assert refused.value.seconds == 40 and len(mailer.sent) == 1
    assert accounts.verify_registration(LINH, first).id  # the first code still stands


def test_asking_again_after_the_wait_sends_a_new_code_with_a_new_delay(accounts, mailer, clock):
    accounts.start_registration(LINH, PASSWORD)
    clock.advance(seconds=60)

    accounts.start_registration(LINH, PASSWORD)
    clock.advance(minutes=9, seconds=30)  # past the end of the first code

    assert len(mailer.sent) == 2
    assert accounts.verify_registration(LINH, mailer.code()).id


def test_a_mail_that_cannot_be_sent_leaves_nothing_waiting(accounts, mailer):
    mailer.down = True

    with pytest.raises(CodeNotSent):
        accounts.start_registration(LINH, PASSWORD)

    assert accounts._pending == {}
    mailer.down = False
    assert accounts.start_registration(LINH, PASSWORD).email == LINH  # no wait either


# ── Signing in ────────────────────────────────────────────────────────────

def test_a_wrong_password_and_an_unknown_email_are_refused_alike(accounts, mailer):
    _create(accounts, mailer)

    with pytest.raises(WrongCredentials) as wrong_password:
        accounts.login(LINH, "not the password")
    with pytest.raises(WrongCredentials) as unknown:
        accounts.login("nobody@example.com", PASSWORD)

    assert str(wrong_password.value) == str(unknown.value)


def test_an_account_waiting_for_its_code_cannot_sign_in(accounts):
    accounts.start_registration(LINH, PASSWORD)

    with pytest.raises(WrongCredentials):
        accounts.login(LINH, PASSWORD)


def test_an_email_is_held_after_too_many_failures_whatever_the_password(accounts, mailer, config):
    config.AUTH_LOGIN_MAX_ATTEMPTS = 3
    _create(accounts, mailer)

    for _ in range(3):
        with pytest.raises(WrongCredentials):
            accounts.login(LINH, "guess")

    with pytest.raises(TooManyAttempts):
        accounts.login(LINH, "guess")
    with pytest.raises(TooManyAttempts):
        accounts.login(LINH, PASSWORD)


def test_a_held_email_is_free_again_once_the_window_has_passed(accounts, mailer, config, clock):
    config.AUTH_LOGIN_MAX_ATTEMPTS, config.AUTH_LOGIN_WINDOW_MINUTES = 2, 10
    user = _create(accounts, mailer)
    for _ in range(2):
        with pytest.raises(WrongCredentials):
            accounts.login(LINH, "guess")

    clock.advance(minutes=9)
    with pytest.raises(TooManyAttempts):
        accounts.login(LINH, PASSWORD)
    clock.advance(minutes=1)

    assert accounts.login(LINH, PASSWORD) == user.id


def test_a_sign_in_that_goes_through_forgets_the_failures(accounts, mailer, config, stores):
    config.AUTH_LOGIN_MAX_ATTEMPTS = 3
    user = _create(accounts, mailer)
    for _ in range(2):
        with pytest.raises(WrongCredentials):
            accounts.login(LINH, "guess")

    assert accounts.login(LINH, PASSWORD) == user.id

    assert stores[1].get_credentials(LINH).failed_attempts == 0
    for _ in range(2):
        with pytest.raises(WrongCredentials):
            accounts.login(LINH, "guess")
    assert accounts.login(LINH, PASSWORD) == user.id


def test_failures_older_than_the_window_are_not_counted(accounts, mailer, config, clock):
    config.AUTH_LOGIN_MAX_ATTEMPTS, config.AUTH_LOGIN_WINDOW_MINUTES = 2, 10
    user = _create(accounts, mailer)
    with pytest.raises(WrongCredentials):
        accounts.login(LINH, "guess")
    clock.advance(minutes=11)

    with pytest.raises(WrongCredentials):
        accounts.login(LINH, "guess")

    assert accounts.login(LINH, PASSWORD) == user.id


def test_one_email_held_leaves_the_others(accounts, mailer, config):
    config.AUTH_LOGIN_MAX_ATTEMPTS = 1
    _create(accounts, mailer)
    an = _create(accounts, mailer, "an@example.com")
    with pytest.raises(WrongCredentials):
        accounts.login(LINH, "guess")

    assert accounts.login("an@example.com", PASSWORD) == an.id
