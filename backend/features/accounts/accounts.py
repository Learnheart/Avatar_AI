"""
AccountService — local accounts: a user name and a password kept here.

Creating an account and signing in with it both end in a user id; the session is opened
by the caller (`sessions.py`), as for any other way of signing in. A wrong user name and
a wrong password are refused alike and take as long; after AUTH_LOGIN_MAX_ATTEMPTS
failures on one user name within AUTH_LOGIN_WINDOW_MINUTES, the name is held until the
window has passed.

Every method blocks (the database, the password hash); call them off the event loop.
"""
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from backend.config import AppConfig
from backend.features.accounts.passwords import NOBODY, hash_password, verify_password
from backend.features.accounts.store import AccountsStore
from backend.features.memory.memory import one_line
from backend.models import User
from backend.utils.logging import get_logger

logger = get_logger("accounts")

# 3 to 32 of: letters without accent, digits, dot, underscore, hyphen. Lower case.
_USERNAME = re.compile(r"[a-z0-9._-]{3,32}")
_DISPLAY_NAME_MAX_CHARS = 64
# Longer than any passphrase, short enough that hashing it costs nothing more.
_PASSWORD_MAX_CHARS = 256


class InvalidAccount(ValueError):
    """The user name, the password or the display name cannot be accepted."""


class UsernameTaken(Exception):
    """An account with this user name exists."""


class RegistrationClosed(Exception):
    """Accounts cannot be created here (AUTH_ALLOW_REGISTRATION=0)."""


class WrongCredentials(Exception):
    """The user name or the password is wrong: which one is not said."""


class TooManyAttempts(Exception):
    """Too many failed sign-ins on this user name: it is held for a while."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normal_username(username: str) -> str:
    """The user name as it is stored and compared: without the spaces around it, in
    lower case."""
    return username.strip().lower()


class AccountService:
    def __init__(
        self, config: AppConfig, store: AccountsStore, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self.config = config
        self._store = store
        self._clock = clock

    def register(self, username: str, password: str, display_name: Optional[str] = None) -> User:
        """Create a user with a local account. Raises RegistrationClosed,
        InvalidAccount or UsernameTaken; nothing is stored then."""
        if not self.config.AUTH_ALLOW_REGISTRATION:
            raise RegistrationClosed("Accounts cannot be created here")
        username = normal_username(username)
        if not _USERNAME.fullmatch(username):
            raise InvalidAccount(
                "A user name is 3 to 32 characters: letters without accent, digits, "
                "'.', '_' or '-'"
            )
        minimum = self.config.AUTH_PASSWORD_MIN_CHARS
        if not minimum <= len(password) <= _PASSWORD_MAX_CHARS:
            raise InvalidAccount(
                f"A password is {minimum} to {_PASSWORD_MAX_CHARS} characters"
            )
        shown = one_line(display_name or "") or username
        if len(shown) > _DISPLAY_NAME_MAX_CHARS:
            raise InvalidAccount(
                f"A display name is at most {_DISPLAY_NAME_MAX_CHARS} characters"
            )
        now = self._clock()
        user = User(id=uuid.uuid4().hex, display_name=shown, created_at=now, last_seen_at=now)
        if not self._store.add_account(user, username, hash_password(password)):
            raise UsernameTaken(f"The user name '{username}' is taken")
        logger.info({"msg": "Account created", "user_id": user.id})
        return user

    def login(self, username: str, password: str) -> str:
        """The id of the user this user name and password sign in. Raises
        WrongCredentials or TooManyAttempts."""
        username = normal_username(username)
        found = self._store.get_credentials(username)
        if found is None:
            verify_password(password, NOBODY)  # as long as a wrong password
            raise WrongCredentials("Wrong user name or password")

        now = self._clock()
        window = timedelta(minutes=self.config.AUTH_LOGIN_WINDOW_MINUTES)
        counting = found.first_failure_at is not None and now - found.first_failure_at < window
        failures = found.failed_attempts if counting else 0
        if failures >= self.config.AUTH_LOGIN_MAX_ATTEMPTS:
            # Held whatever the password: a guess is not told it was right.
            raise TooManyAttempts("Too many failed sign-ins on this user name")

        if not verify_password(password, found.password_hash):
            since = found.first_failure_at if counting else now
            self._store.set_failures(username, failures + 1, since)
            logger.warning({"msg": "Sign-in refused", "failures": failures + 1})
            raise WrongCredentials("Wrong user name or password")
        if found.failed_attempts:
            self._store.set_failures(username, 0, None)
        return found.user_id
