"""
AccountService — local accounts: an email address and a password kept here.

Creating an account takes two steps, so that an account proves its email: the email is
given and a six-digit code is sent to that address; the account exists once the code is
given back together with the password, within AUTH_OTP_TTL_MINUTES and
AUTH_OTP_MAX_ATTEMPTS tries. The password comes with the code and never before:
whoever reads the mailbox sets it, and somebody else asking for the same address can
only replace the code. What waits for its code lives in RAM (the code hashed, nothing
of a password): this is one process, and a restart only means asking for a new code.
One email has one account.

Mails are limited, in RAM too: an address gets AUTH_OTP_MAX_CODES codes and all
addresses together AUTH_OTP_MAX_MAILS mails per AUTH_OTP_WINDOW_MINUTES.

Signing in ends in a user id; the session is opened by the caller (`sessions.py`), as
for any other way of signing in. A wrong email and a wrong password are refused alike
and take as long; after AUTH_LOGIN_MAX_ATTEMPTS attempts on one email within
AUTH_LOGIN_WINDOW_MINUTES, it is held until the window has passed. An attempt is
counted in the database before the password is looked at, so the limit holds for
attempts made at the same moment.

Every method blocks (the database, the password hash, the mail); call them off the
event loop.
"""
import hashlib
import hmac
import re
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from backend.config import AppConfig
from backend.features.accounts.passwords import NOBODY, hash_password, verify_password
from backend.features.accounts.store import AccountsStore
from backend.features.memory.memory import one_line
from backend.models import User
from backend.utils.logging import get_logger
from backend.utils.mailer import MailError, Mailer

logger = get_logger("accounts")

# Something before an @, something after it with a dot: the code sent to the address
# is what proves it, not this.
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_EMAIL_MAX_CHARS = 254
_DISPLAY_NAME_MAX_CHARS = 64
# Longer than any passphrase, short enough that hashing it costs nothing more.
_PASSWORD_MAX_CHARS = 256
_CODE_DIGITS = 6


class InvalidAccount(ValueError):
    """The email, the password or the display name cannot be accepted."""


class EmailTaken(Exception):
    """An account with this email exists."""


class RegistrationClosed(Exception):
    """Accounts cannot be created here (AUTH_ALLOW_REGISTRATION=0)."""


class CodeJustSent(Exception):
    """A code was sent to this email a moment ago: `seconds` until another can be."""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"A code was just sent: wait {seconds} s before asking again")
        self.seconds = seconds


class TooManyCodes(Exception):
    """As many codes were sent as the window allows (to this email, or to all of them):
    `seconds` until another can be."""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"Too many codes were sent: wait {seconds} s before asking again")
        self.seconds = seconds


class CodeNotSent(Exception):
    """The mail carrying the code could not be sent: nothing waits for a code."""


class WrongCode(Exception):
    """The code is not the one that was sent."""


class CodeExpired(Exception):
    """No code waits for this email any more (too late, too many tries, or none was
    asked for): a new one has to be asked for."""


class WrongCredentials(Exception):
    """The email or the password is wrong: which one is not said."""


class TooManyAttempts(Exception):
    """Too many failed sign-ins on this email: it is held for a while."""


@dataclass
class _Pending:
    """A code waiting to be given back by the email it was sent to. Nothing of the
    account is here: the password and the name come with the code."""
    salt: bytes
    code_digest: bytes
    sent_at: datetime
    expires_at: datetime
    attempts: int = 0


@dataclass(frozen=True)
class CodeSent:
    """What the page is told once a code was sent."""
    email: str
    expires_in_sec: int
    resend_in_sec: int

    def to_dict(self) -> dict:
        return {
            "email": self.email,
            "expires_in_sec": self.expires_in_sec,
            "resend_in_sec": self.resend_in_sec,
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normal_email(email: str) -> str:
    """The email as it is stored and compared: without the spaces around it, in lower
    case."""
    return email.strip().lower()


def _digest(salt: bytes, code: str) -> bytes:
    return hashlib.sha256(salt + code.encode("utf-8")).digest()


def _seconds(left: timedelta) -> int:
    """A wait as the page is told it: whole seconds, never zero."""
    return max(1, round(left.total_seconds()))


class AccountService:
    def __init__(
        self,
        config: AppConfig,
        store: AccountsStore,
        mailer: Mailer,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.config = config
        self._store = store
        self._mailer = mailer
        self._clock = clock
        self._pending: dict[str, _Pending] = {}  # by email
        # When a code was mailed to each email within the window, oldest first.
        self._mailed: dict[str, list[datetime]] = {}
        self._lock = threading.Lock()            # the methods run in threads

    # ── Creating an account ───────────────────────────────────────────────

    def start_registration(self, email: str) -> CodeSent:
        """Send a code to the email; the account is created by `verify_registration`.
        Asking again replaces the code, once AUTH_OTP_RESEND_SEC have passed; the wrong
        tries made on the replaced code stay counted on the new one. Raises
        RegistrationClosed, InvalidAccount, EmailTaken, CodeJustSent, TooManyCodes or
        CodeNotSent. After CodeNotSent nothing waits for a code; after the others the
        code sent before, if any, still stands."""
        if not self.config.AUTH_ALLOW_REGISTRATION:
            raise RegistrationClosed("Accounts cannot be created here")
        email = normal_email(email)
        if len(email) > _EMAIL_MAX_CHARS or not _EMAIL.fullmatch(email):
            raise InvalidAccount("This is not an email address")
        if self._store.get_credentials(email) is not None:
            raise EmailTaken(f"The email '{email}' has an account")

        now = self._clock()
        resend = timedelta(seconds=self.config.AUTH_OTP_RESEND_SEC)
        ttl = timedelta(minutes=self.config.AUTH_OTP_TTL_MINUTES)
        window = timedelta(minutes=self.config.AUTH_OTP_WINDOW_MINUTES)
        code = f"{secrets.randbelow(10 ** _CODE_DIGITS):0{_CODE_DIGITS}d}"
        salt = secrets.token_bytes(16)
        # The wait and the limits are checked and the code recorded in one critical
        # section: of the requests made at the same moment, one sends a mail.
        with self._lock:
            self._forget_expired(now)
            waiting = self._pending.get(email)
            if waiting is not None and now - waiting.sent_at < resend:
                raise CodeJustSent(_seconds(resend - (now - waiting.sent_at)))
            here = self._mailed.get(email, [])
            if len(here) >= self.config.AUTH_OTP_MAX_CODES:
                logger.warning({"msg": "Code refused: too many for one email"})
                raise TooManyCodes(_seconds(here[0] + window - now))
            everywhere = [at for times in self._mailed.values() for at in times]
            if len(everywhere) >= self.config.AUTH_OTP_MAX_MAILS:
                logger.warning({"msg": "Code refused: too many mails"})
                raise TooManyCodes(_seconds(min(everywhere) + window - now))
            pending = _Pending(
                salt=salt,
                code_digest=_digest(salt, code),
                sent_at=now,
                expires_at=now + ttl,
                # The tries made on the replaced code stay counted. What bounds the
                # guessing: AUTH_OTP_MAX_CODES codes per address per window, each
                # with AUTH_OTP_MAX_ATTEMPTS tries.
                attempts=waiting.attempts if waiting is not None else 0,
            )
            self._pending[email] = pending
            self._mailed.setdefault(email, []).append(now)
        try:
            self._mailer.send(
                email,
                self.config.AUTH_CODE_MAIL_SUBJECT,
                self.config.AUTH_CODE_MAIL_BODY.format(
                    code=code, minutes=round(ttl.total_seconds() / 60)
                ),
            )
        except MailError as e:
            with self._lock:
                # Only what this request wrote: a later one may have replaced it.
                if self._pending.get(email) is pending:
                    del self._pending[email]
                times = self._mailed.get(email, [])
                if now in times:  # a mail that was not sent is not counted
                    times.remove(now)
                    if not times:
                        del self._mailed[email]
            logger.error({"msg": "Code not sent", "error": str(e)})
            raise CodeNotSent("The code could not be sent") from e
        logger.info({"msg": "Code sent for a new account"})
        return CodeSent(email, round(ttl.total_seconds()), round(resend.total_seconds()))

    def verify_registration(
        self, email: str, code: str, password: str, display_name: Optional[str] = None
    ) -> User:
        """Create the account whose code this is, with the password and the name given
        with it. Raises InvalidAccount (the password or the name cannot be accepted: no
        try is used), WrongCode (try again), CodeExpired (ask for a new code) or
        EmailTaken; nothing is stored then."""
        email = normal_email(email)
        minimum = self.config.AUTH_PASSWORD_MIN_CHARS
        if not minimum <= len(password) <= _PASSWORD_MAX_CHARS:
            raise InvalidAccount(f"A password is {minimum} to {_PASSWORD_MAX_CHARS} characters")
        shown = one_line(display_name or "")
        if len(shown) > _DISPLAY_NAME_MAX_CHARS:
            raise InvalidAccount(
                f"A display name is at most {_DISPLAY_NAME_MAX_CHARS} characters"
            )
        # Without a name, the part before the @, cut: the address got its code already.
        shown = shown or email.split("@", 1)[0][:_DISPLAY_NAME_MAX_CHARS]

        now = self._clock()
        with self._lock:
            self._forget_expired(now)
            waiting = self._pending.get(email)
            if waiting is None:
                raise CodeExpired("No code waits for this email: ask for a new one")
            given = "".join(code.split())
            if not hmac.compare_digest(_digest(waiting.salt, given), waiting.code_digest):
                waiting.attempts += 1
                if waiting.attempts >= self.config.AUTH_OTP_MAX_ATTEMPTS:
                    # A code is guessed in a million tries at most: it gets a few.
                    del self._pending[email]
                    logger.warning({"msg": "Code dropped after too many wrong tries"})
                raise WrongCode("This is not the code that was sent")
            del self._pending[email]

        # Hashed outside the lock, and only for who gave the code back.
        password_hash = hash_password(password)
        user = User(id=uuid.uuid4().hex, display_name=shown, created_at=now, last_seen_at=now)
        if not self._store.add_account(user, email, password_hash):
            raise EmailTaken(f"The email '{email}' has an account")
        logger.info({"msg": "Account created", "user_id": user.id})
        return user

    def _forget_expired(self, now: datetime) -> None:
        """Drop the codes that are too old, and the mails that left the window, so that
        neither grows without bound. Called with the lock held."""
        for email in [e for e, waiting in self._pending.items() if waiting.expires_at <= now]:
            del self._pending[email]
        oldest = now - timedelta(minutes=self.config.AUTH_OTP_WINDOW_MINUTES)
        for email, times in list(self._mailed.items()):
            kept = [at for at in times if at > oldest]
            if kept:
                self._mailed[email] = kept
            else:
                del self._mailed[email]

    # ── Signing in ────────────────────────────────────────────────────────

    def login(self, email: str, password: str) -> str:
        """The id of the user this email and password sign in. Raises WrongCredentials
        or TooManyAttempts."""
        email = normal_email(email)
        found = self._store.get_credentials(email)
        if found is None:
            verify_password(password, NOBODY)  # as long as a wrong password
            raise WrongCredentials("Wrong email or password")

        # Charged before the password is looked at, in one statement: attempts made at
        # the same moment are all counted. Held whatever the password: a guess is not
        # told it was right.
        window = timedelta(minutes=self.config.AUTH_LOGIN_WINDOW_MINUTES)
        if not self._store.charge_attempt(
            email, self._clock(), window, self.config.AUTH_LOGIN_MAX_ATTEMPTS
        ):
            raise TooManyAttempts("Too many failed sign-ins on this email")

        if not verify_password(password, found.password_hash):
            logger.warning({"msg": "Sign-in refused"})
            raise WrongCredentials("Wrong email or password")
        self._store.clear_attempts(email)
        return found.user_id
