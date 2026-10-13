"""
SessionService — the open sign-ins.

Signing in gives the page a random token; the database keeps only its SHA-256, with the
user. A session is kept until it is revoked (sign-out), has not been used for
AUTH_SESSION_IDLE_MINUTES, or is older than AUTH_SESSION_MAX_HOURS: the two limits are
checked on every use of the token, so a change of either applies to the sessions that
are open.

Every method blocks (the database); call them off the event loop.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from backend.config import AppConfig
from backend.features.accounts.store import AccountsStore
from backend.models import Session
from backend.utils.logging import get_logger

logger = get_logger("accounts.sessions")

_TOKEN_BYTES = 32


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def session_id(token: str) -> str:
    """How a token is named in the database: its SHA-256, never itself."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SessionService:
    def __init__(
        self, config: AppConfig, store: AccountsStore, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self.config = config
        self._store = store
        self._clock = clock

    def open(self, user_id: str) -> tuple[str, datetime]:
        """Sign the user in: the token to give the page, once, and when the session
        ends if it is not used."""
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        now = self._clock()
        session = Session(id=session_id(token), user_id=user_id, created_at=now, last_seen_at=now)
        self._store.add_session(session)
        logger.info({"msg": "Session opened", "user_id": user_id})
        return token, self._ends_at(session)

    def resolve(self, token: Optional[str]) -> Optional[tuple[str, datetime]]:
        """Who the token signs in, and when their session ends if it is not used again:
        (user id, end). None for no token, an unknown one, and a session that was
        revoked or has expired. A session that is still open is noted as used now."""
        if not token:
            return None
        session = self._store.get_session(session_id(token))
        now = self._clock()
        if session is None or session.revoked_at is not None or self._ends_at(session) <= now:
            return None
        self._store.touch_session(session.id, now)
        session.last_seen_at = now
        return session.user_id, self._ends_at(session)

    def user_of(self, token: Optional[str]) -> Optional[str]:
        """Who the token signs in, or None, without noting a use: looking at a session
        does not keep it alive."""
        if not token:
            return None
        session = self._store.get_session(session_id(token))
        if session is None or session.revoked_at is not None:
            return None
        return session.user_id if self._ends_at(session) > self._clock() else None

    def revoke(self, token: Optional[str]) -> None:
        """End the session of the token; nothing happens for a token that opens none."""
        if token and self._store.revoke_session(session_id(token), self._clock()):
            logger.info({"msg": "Session revoked"})

    def delete_ended(self) -> int:
        """Housekeeping: delete the sessions that are over. How many."""
        now = self._clock()
        return self._store.delete_ended_sessions(now - self._idle(), now - self._age())

    def _ends_at(self, session: Session) -> datetime:
        """The earlier of the two limits: not used for too long, or too old."""
        return min(session.last_seen_at + self._idle(), session.created_at + self._age())

    def _idle(self) -> timedelta:
        return timedelta(minutes=self.config.AUTH_SESSION_IDLE_MINUTES)

    def _age(self) -> timedelta:
        return timedelta(hours=self.config.AUTH_SESSION_MAX_HOURS)
