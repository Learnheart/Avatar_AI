"""
Who is behind a connection or a request.

Until the login exists, the user is the one named by `AUTH_DEV_USER_ID` (empty = a guest,
the default). The login replaces the body of `current_user_id` and nothing else.
"""
from datetime import datetime, timezone
from typing import Optional

from backend.config import AppConfig
from backend.features.accounts.store import UsersStore


def current_user_id(
    config: AppConfig, users: UsersStore, now: Optional[datetime] = None
) -> Optional[str]:
    """The id of the user, or None for a guest. The user's row is created on first use
    and its `last_seen_at` set on each call. Blocks (the database): call it in a thread.
    Raises DatabaseError when the database fails."""
    user_id = config.AUTH_DEV_USER_ID.strip()
    if not user_id:
        return None
    users.touch(user_id, display_name=user_id, now=now or datetime.now(timezone.utc))
    return user_id
