"""AccountService: creating a local account, proved by a code sent to its email, and
signing in with it."""
import threading
from concurrent.futures import ThreadPoolExecutor
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
    TooManyCodes,
    WrongCode,
    WrongCredentials,
)
from backend.features.accounts.store import AccountsStore, UsersStore
from backend.utils.mailer import MailError
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


def _wrong(code: str) -> str:
    """A code that is not the one that was sent."""
    return "000000" if code != "000000" else "111111"


def _create(accounts: AccountService, mailer: CapturingMailer, email: str = LINH, **fields):
    accounts.start_registration(email)
    return accounts.verify_registration(email, mailer.code(email), PASSWORD, **fields)


# ── Creating an account ───────────────────────────────────────────────────

def test_asking_for_an_account_sends_a_code_and_creates_nothing(accounts, mailer, stores, config):
    sent = accounts.start_registration("  Linh@Example.com ")

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
    accounts.start_registration("Linh@Example.COM")
    user = accounts.verify_registration(" LINH@example.com ", mailer.code(LINH), PASSWORD)

    assert accounts.login("  LINH@EXAMPLE.com ", PASSWORD) == user.id
    with pytest.raises(EmailTaken):
        accounts.start_registration("lInH@example.com")


def test_without_a_display_name_the_part_before_the_at_is_shown(accounts, mailer):
    assert _create(accounts, mailer).display_name == "linh"


def test_the_password_and_the_code_are_kept_hashed(accounts, mailer, stores):
    accounts.start_registration(LINH)
    waiting = accounts._pending[LINH]
    code = mailer.code()

    assert code.encode() not in waiting.code_digest and len(waiting.code_digest) == 32

    accounts.verify_registration(LINH, code, PASSWORD)
    stored = stores[1].get_credentials(LINH).password_hash
    assert PASSWORD not in stored and stored.startswith("scrypt$")
    assert accounts._pending == {}


def test_nothing_of_a_password_waits_between_the_two_steps(accounts, mailer, monkeypatch):
    hashed = []
    monkeypatch.setattr(
        "backend.features.accounts.accounts.hash_password",
        lambda password: hashed.append(password) or "scrypt$fake",
    )

    accounts.start_registration(LINH)

    assert hashed == []  # the route anybody can call hashes nothing
    assert set(vars(accounts._pending[LINH])) == {
        "salt", "code_digest", "sent_at", "expires_at", "attempts",
    }
    accounts.verify_registration(LINH, mailer.code(), PASSWORD)
    assert hashed == [PASSWORD]


def test_a_code_is_six_digits_and_differs_from_one_asking_to_the_next(
    accounts, mailer, clock, config
):
    config.AUTH_OTP_MAX_CODES = 8
    codes = set()
    for _ in range(8):
        accounts.start_registration(LINH)
        codes.add(mailer.code())
        clock.advance(seconds=61)

    assert all(len(code) == 6 and code.isdigit() for code in codes)
    assert len(codes) > 1


def test_spaces_in_the_code_as_it_is_typed_are_ignored(accounts, mailer):
    accounts.start_registration(LINH)
    code = mailer.code()

    assert accounts.verify_registration(LINH, f" {code[:3]} {code[3:]} ", PASSWORD).id


@pytest.mark.parametrize(
    "email",
    [
        "linh",
        "linh@",
        "@example.com",
        "linh@example",
        "linh nguyen@example.com",
        "a@b@example.com",
        "x" * 250 + "@example.com",
    ],
)
def test_an_address_that_cannot_be_accepted_sends_no_code(accounts, mailer, email):
    with pytest.raises(InvalidAccount):
        accounts.start_registration(email)

    assert mailer.sent == [] and accounts._pending == {} and accounts._mailed == {}


@pytest.mark.parametrize(
    ("password", "display_name"),
    [("1234567", None), ("x" * 257, None), (PASSWORD, "x" * 65)],
)
def test_a_password_or_a_name_that_cannot_be_accepted_uses_no_try(
    accounts, mailer, config, stores, password, display_name
):
    config.AUTH_OTP_MAX_ATTEMPTS = 1
    accounts.start_registration(LINH)
    code = mailer.code()

    for given in (code, _wrong(code)):  # refused before the code is looked at
        with pytest.raises(InvalidAccount):
            accounts.verify_registration(LINH, given, password, display_name)

    assert accounts._pending[LINH].attempts == 0 and stores[0].count() == 0
    assert accounts.verify_registration(LINH, code, PASSWORD).id  # the code still stands


def test_the_minimum_password_length_follows_the_config(accounts, mailer, config):
    config.AUTH_PASSWORD_MIN_CHARS = 4
    accounts.start_registration(LINH)

    with pytest.raises(InvalidAccount, match="4 to 256"):
        accounts.verify_registration(LINH, mailer.code(), "abc")
    assert accounts.verify_registration(LINH, mailer.code(), "abcd").id


def test_a_long_address_without_a_name_is_shown_cut(accounts, mailer):
    email = "x" * 70 + "@example.com"

    assert _create(accounts, mailer, email).display_name == "x" * 64


def test_registration_can_be_switched_off(accounts, config, mailer):
    config.AUTH_ALLOW_REGISTRATION = 0

    with pytest.raises(RegistrationClosed):
        accounts.start_registration(LINH)

    assert mailer.sent == []


def test_one_email_has_one_account(accounts, mailer, stores):
    first = _create(accounts, mailer)

    with pytest.raises(EmailTaken):
        accounts.start_registration(LINH)

    assert len(mailer.sent) == 1
    assert stores[0].count() == 1 and accounts.login(LINH, PASSWORD) == first.id


def test_two_people_asking_for_the_same_email_end_in_one_account(accounts, mailer, clock, stores):
    accounts.start_registration(LINH)  # the owner of the mailbox
    first_code = mailer.code()
    clock.advance(seconds=61)
    accounts.start_registration(LINH)  # somebody else: replaces the code, nothing more

    with pytest.raises(WrongCode):
        accounts.verify_registration(LINH, first_code, PASSWORD)
    # The owner reads the newest code and gives it back with their own password.
    user = accounts.verify_registration(LINH, mailer.code(), PASSWORD)

    assert stores[0].count() == 1
    assert accounts.login(LINH, PASSWORD) == user.id
    with pytest.raises(WrongCredentials):
        accounts.login(LINH, "the password of the second")


# ── The code ──────────────────────────────────────────────────────────────

def test_a_wrong_code_creates_nothing_and_the_right_one_still_works(accounts, mailer, stores):
    accounts.start_registration(LINH)
    code = mailer.code()
    wrong = _wrong(code)

    with pytest.raises(WrongCode):
        accounts.verify_registration(LINH, wrong, PASSWORD)
    assert stores[0].count() == 0

    assert accounts.verify_registration(LINH, code, PASSWORD).id


def test_a_code_is_dropped_after_too_many_wrong_tries(accounts, mailer, config, stores):
    config.AUTH_OTP_MAX_ATTEMPTS = 3
    accounts.start_registration(LINH)
    code = mailer.code()
    wrong = _wrong(code)

    for _ in range(3):
        with pytest.raises(WrongCode):
            accounts.verify_registration(LINH, wrong, PASSWORD)

    with pytest.raises(CodeExpired):
        accounts.verify_registration(LINH, code, PASSWORD)  # the right code, too late
    assert stores[0].count() == 0


def test_a_code_expires(accounts, mailer, clock, config):
    config.AUTH_OTP_TTL_MINUTES = 10
    accounts.start_registration(LINH)
    code = mailer.code()

    clock.advance(minutes=10)

    with pytest.raises(CodeExpired):
        accounts.verify_registration(LINH, code, PASSWORD)


def test_a_code_works_once(accounts, mailer):
    accounts.start_registration(LINH)
    code = mailer.code()
    accounts.verify_registration(LINH, code, PASSWORD)

    with pytest.raises(CodeExpired):
        accounts.verify_registration(LINH, code, PASSWORD)


def test_no_code_waits_for_an_email_that_asked_for_none(accounts):
    with pytest.raises(CodeExpired):
        accounts.verify_registration("nobody@example.com", "123456", PASSWORD)


def test_the_code_of_one_email_does_not_open_another(accounts, mailer):
    accounts.start_registration(LINH)
    accounts.start_registration("an@example.com")

    with pytest.raises(WrongCode):
        accounts.verify_registration("an@example.com", mailer.code(LINH), PASSWORD)


def test_another_code_can_be_asked_for_only_after_a_while(accounts, mailer, clock, config):
    config.AUTH_OTP_RESEND_SEC = 60
    accounts.start_registration(LINH)
    first = mailer.code()
    clock.advance(seconds=20)

    with pytest.raises(CodeJustSent) as refused:
        accounts.start_registration(LINH)
    assert refused.value.seconds == 40 and len(mailer.sent) == 1
    assert accounts.verify_registration(LINH, first, PASSWORD).id  # the first code still stands


def test_asking_again_after_the_wait_sends_a_new_code_with_a_new_delay(accounts, mailer, clock):
    accounts.start_registration(LINH)
    clock.advance(seconds=60)

    accounts.start_registration(LINH)
    clock.advance(minutes=9, seconds=30)  # past the end of the first code

    assert len(mailer.sent) == 2
    assert accounts.verify_registration(LINH, mailer.code(), PASSWORD).id


def test_a_mail_that_cannot_be_sent_leaves_nothing_waiting(accounts, mailer):
    mailer.down = True

    with pytest.raises(CodeNotSent):
        accounts.start_registration(LINH)

    assert accounts._pending == {} and accounts._mailed == {}  # not counted either
    mailer.down = False
    assert accounts.start_registration(LINH).email == LINH  # no wait either


def test_a_mail_that_fails_does_not_remove_a_newer_code(accounts, mailer, clock, config):
    """The slow mail of one request fails after another request replaced its code."""
    config.AUTH_OTP_RESEND_SEC = 60

    class FailsLate(CapturingMailer):
        def send(self, to: str, subject: str, body: str) -> None:
            if not self.sent:  # the first mail only: while it is on its way...
                clock.advance(seconds=61)
                self.sent.append(("", "", ""))
                accounts.start_registration(LINH)  # ...a newer code is sent
                raise MailError("The mail was not sent: timed out")
            super().send(to, subject, body)

    accounts._mailer = late = FailsLate()

    with pytest.raises(CodeNotSent):
        accounts.start_registration(LINH)

    assert accounts.verify_registration(LINH, late.code(LINH), PASSWORD).id


def test_requests_for_one_address_at_the_same_moment_send_one_mail(accounts, mailer):
    ready = threading.Barrier(16)

    def ask(_) -> str:
        ready.wait()
        try:
            accounts.start_registration(LINH)
        except CodeJustSent:
            return "wait"
        return "sent"

    with ThreadPoolExecutor(max_workers=16) as pool:
        told = list(pool.map(ask, range(16)))

    assert sorted(told) == ["sent"] + ["wait"] * 15
    assert len(mailer.sent) == 1
    assert accounts.verify_registration(LINH, mailer.code(), PASSWORD).id


# ── The limits on the mails ───────────────────────────────────────────────

def test_an_address_gets_a_few_codes_per_window(accounts, mailer, clock, config):
    config.AUTH_OTP_MAX_CODES, config.AUTH_OTP_WINDOW_MINUTES = 5, 60
    for _ in range(5):
        accounts.start_registration(LINH)
        clock.advance(minutes=2)  # the first was sent 10 minutes ago

    with pytest.raises(TooManyCodes) as refused:
        accounts.start_registration(LINH)
    assert refused.value.seconds == 50 * 60 and len(mailer.sent) == 5
    assert accounts.start_registration("an@example.com").email == "an@example.com"

    clock.advance(minutes=50)  # the first has left the window
    assert accounts.start_registration(LINH).email == LINH


def test_all_addresses_together_get_a_number_of_mails_per_window(accounts, mailer, clock, config):
    config.AUTH_OTP_MAX_MAILS, config.AUTH_OTP_WINDOW_MINUTES = 3, 60
    for n in range(3):
        accounts.start_registration(f"person{n}@example.com")
        clock.advance(minutes=1)

    with pytest.raises(TooManyCodes) as refused:
        accounts.start_registration(LINH)
    assert refused.value.seconds == 57 * 60 and len(mailer.sent) == 3
    assert LINH not in accounts._pending

    clock.advance(minutes=57)
    assert accounts.start_registration(LINH).email == LINH


def test_a_refused_code_leaves_the_one_sent_before(accounts, mailer, clock, config):
    config.AUTH_OTP_MAX_CODES = 1
    accounts.start_registration(LINH)
    clock.advance(seconds=61)

    with pytest.raises(TooManyCodes):
        accounts.start_registration(LINH)

    assert accounts.verify_registration(LINH, mailer.code(), PASSWORD).id


def test_the_mails_that_left_the_window_are_forgotten(accounts, clock, config):
    config.AUTH_OTP_WINDOW_MINUTES = 60
    for n in range(20):
        accounts.start_registration(f"person{n}@example.com")
    clock.advance(minutes=60)

    accounts.start_registration(LINH)

    assert list(accounts._mailed) == [LINH] and list(accounts._pending) == [LINH]


def test_wrong_tries_are_not_restarted_by_a_new_code(accounts, mailer, clock, config, stores):
    config.AUTH_OTP_MAX_ATTEMPTS = 3
    accounts.start_registration(LINH)
    for _ in range(2):
        with pytest.raises(WrongCode):
            accounts.verify_registration(LINH, _wrong(mailer.code()), PASSWORD)
    clock.advance(seconds=61)
    accounts.start_registration(LINH)
    code = mailer.code()

    with pytest.raises(WrongCode):  # the third try, not the first of three
        accounts.verify_registration(LINH, _wrong(code), PASSWORD)

    with pytest.raises(CodeExpired):
        accounts.verify_registration(LINH, code, PASSWORD)
    assert stores[0].count() == 0


# ── Signing in ────────────────────────────────────────────────────────────

def test_a_wrong_password_and_an_unknown_email_are_refused_alike(accounts, mailer):
    _create(accounts, mailer)

    with pytest.raises(WrongCredentials) as wrong_password:
        accounts.login(LINH, "not the password")
    with pytest.raises(WrongCredentials) as unknown:
        accounts.login("nobody@example.com", PASSWORD)

    assert str(wrong_password.value) == str(unknown.value)


def test_an_account_waiting_for_its_code_cannot_sign_in(accounts):
    accounts.start_registration(LINH)

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


def test_wrong_sign_ins_at_the_same_moment_are_all_counted(accounts, mailer, config, monkeypatch):
    config.AUTH_LOGIN_MAX_ATTEMPTS = 5
    _create(accounts, mailer)
    checked = []
    ready = threading.Barrier(32)
    monkeypatch.setattr(
        "backend.features.accounts.accounts.verify_password",
        lambda password, stored: checked.append(password) or False,
    )

    def guess(n: int) -> str:
        ready.wait()
        try:
            accounts.login(LINH, f"guess {n}")
        except WrongCredentials:
            return "wrong"
        except TooManyAttempts:
            return "held"
        return "in"

    with ThreadPoolExecutor(max_workers=32) as pool:
        told = list(pool.map(guess, range(32)))

    assert len(checked) == 5  # the others never reached the password
    assert sorted(told) == ["held"] * 27 + ["wrong"] * 5


def test_a_held_email_costs_no_password_check(accounts, mailer, config, monkeypatch):
    config.AUTH_LOGIN_MAX_ATTEMPTS = 2
    _create(accounts, mailer)
    for _ in range(2):
        with pytest.raises(WrongCredentials):
            accounts.login(LINH, "guess")
    checked = []
    monkeypatch.setattr(
        "backend.features.accounts.accounts.verify_password",
        lambda password, stored: checked.append(password) or True,
    )

    with pytest.raises(TooManyAttempts):
        accounts.login(LINH, PASSWORD)

    assert checked == []


def test_an_unknown_email_still_costs_one_password_check(accounts, monkeypatch):
    from backend.features.accounts.passwords import NOBODY

    checked = []
    monkeypatch.setattr(
        "backend.features.accounts.accounts.verify_password",
        lambda password, stored: checked.append((password, stored)) or False,
    )

    for _ in range(3):  # and is never held: there is nothing to count it on
        with pytest.raises(WrongCredentials):
            accounts.login("nobody@example.com", "guess")

    assert checked == [("guess", NOBODY)] * 3
