"""
Who is behind a connection or a request.

`AUTH_DEV_USER_ID` set: every connection and request is that user, without sign-in
(development, tests). Otherwise the session token decides: a token that opens a session
is its user, anything else is a guest. A guest is never an error.
"""
from datetime import datetime, timezone
from typing import Optional

from backend.config import AppConfig
from backend.features.accounts.sessions import SessionService
from backend.features.accounts.store import UsersStore

_BEARER = "bearer "


def bearer_token(authorization: Optional[str]) -> Optional[str]:
    """The token of an `Authorization: Bearer <token>` header; None when the header is
    missing, of another kind or empty."""
    if not authorization or not authorization.lower().startswith(_BEARER):
        return None
    return authorization[len(_BEARER):].strip() or None


def current_user_id(
    config: AppConfig,
    users: UsersStore,
    now: Optional[datetime] = None,
    *,
    sessions: Optional[SessionService] = None,
    token: Optional[str] = None,
) -> Optional[str]:
    """The id of the user, or None for a guest. With `AUTH_DEV_USER_ID`, that user: the
    row is created on first use and its `last_seen_at` set on each call. Otherwise the
    user of the session `token` opens, if it is still open. Blocks (the database): call
    it in a thread. Raises DatabaseError when the database fails."""
    user_id = config.AUTH_DEV_USER_ID.strip()
    if user_id:
        users.touch(user_id, display_name=user_id, now=now or datetime.now(timezone.utc))
        return user_id
    if sessions is None:
        return None
    resolved = sessions.resolve(token)
    return resolved[0] if resolved else None
