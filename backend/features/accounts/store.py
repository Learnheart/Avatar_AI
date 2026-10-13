"""
The users of the application database, how they sign in (local credentials, identities
at external providers) and their open sessions. All the SQL of the accounts is here,
written against SQLAlchemy Core. Every method blocks; call them off the event loop.
"""
import threading
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    String,
    Table,
    delete,
    func,
    insert,
    or_,
    select,
    update,
)

from backend.models import Credentials, Identity, Session, User
from backend.utils.database import Database, UTCDateTime, metadata

users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),
    Column("display_name", String, nullable=False),
    Column("created_at", UTCDateTime, nullable=False),
    Column("last_seen_at", UTCDateTime, nullable=False),
)

# A local account: the user name is not the user id, and lives here only.
credentials = Table(
    "credentials",
    metadata,
    Column("username", String, primary_key=True),
    Column("user_id", String, ForeignKey(users.c.id), nullable=False, unique=True),
    # scrypt, with its salt and its parameters (features/accounts/passwords.py).
    Column("password_hash", String, nullable=False),
    Column("failed_attempts", Integer, nullable=False),
    Column("first_failure_at", UTCDateTime),
    Column("updated_at", UTCDateTime, nullable=False),
)

# A user as an external provider knows them. The email is kept to be shown, never to
# find a user.
identities = Table(
    "identities",
    metadata,
    Column("provider", String, primary_key=True),
    Column("subject", String, primary_key=True),
    Column("user_id", String, ForeignKey(users.c.id), nullable=False, index=True),
    Column("email", String),
    Column("created_at", UTCDateTime, nullable=False),
)

# An open sign-in. The id is the SHA-256 of the token: the token is not stored.
sessions = Table(
    "sessions",
    metadata,
    Column("id", String, primary_key=True),
    Column("user_id", String, ForeignKey(users.c.id), nullable=False, index=True),
    Column("created_at", UTCDateTime, nullable=False),
    Column("last_seen_at", UTCDateTime, nullable=False),
    Column("revoked_at", UTCDateTime),
)


class UsersStore:
    def __init__(self, database: Database) -> None:
        self._database = database
        # Two first uses of the same user at once would both insert the row.
        self._lock = threading.Lock()

    def touch(self, user_id: str, display_name: str, now: datetime) -> None:
        """Note that the user was seen at `now`; the row is created on first use, with
        `display_name`."""
        with self._lock, self._database.begin() as connection:
            seen = connection.execute(
                update(users).where(users.c.id == user_id).values(last_seen_at=now)
            )
            if seen.rowcount == 0:
                connection.execute(
                    insert(users).values(
                        id=user_id, display_name=display_name, created_at=now, last_seen_at=now
                    )
                )

    def get(self, user_id: str) -> Optional[User]:
        with self._database.begin() as connection:
            row = connection.execute(select(users).where(users.c.id == user_id)).first()
        return User(**row._mapping) if row else None

    def count(self) -> int:
        with self._database.begin() as connection:
            return connection.execute(select(func.count()).select_from(users)).scalar_one()


class AccountsStore:
    """Credentials, identities and sessions. A user is created together with the first
    way they sign in, in one transaction."""

    def __init__(self, database: Database) -> None:
        self._database = database
        # Two creations of the same user name or identity at once would both insert it.
        self._lock = threading.Lock()

    # ── Local accounts ────────────────────────────────────────────────────

    def add_account(self, user: User, username: str, password_hash: str) -> bool:
        """Create the user and their local account. False, and nothing written, when
        the user name is taken."""
        with self._lock, self._database.begin() as connection:
            taken = connection.execute(
                select(credentials.c.username).where(credentials.c.username == username)
            ).first()
            if taken is not None:
                return False
            connection.execute(insert(users).values(**vars(user)))
            connection.execute(
                insert(credentials).values(
                    username=username,
                    user_id=user.id,
                    password_hash=password_hash,
                    failed_attempts=0,
                    updated_at=user.created_at,
                )
            )
        return True

    def get_credentials(self, username: str) -> Optional[Credentials]:
        query = select(
            credentials.c.username,
            credentials.c.user_id,
            credentials.c.password_hash,
            credentials.c.failed_attempts,
            credentials.c.first_failure_at,
        ).where(credentials.c.username == username)
        with self._database.begin() as connection:
            row = connection.execute(query).first()
        return Credentials(**row._mapping) if row else None

    def has_credentials(self, user_id: str) -> bool:
        with self._database.begin() as connection:
            return connection.execute(
                select(credentials.c.username).where(credentials.c.user_id == user_id)
            ).first() is not None

    def set_failures(
        self, username: str, failed_attempts: int, first_failure_at: Optional[datetime]
    ) -> None:
        """The failed sign-ins counted on a user name, and since when; 0 and None once
        a sign-in went through or the window has passed."""
        with self._database.begin() as connection:
            connection.execute(
                update(credentials)
                .where(credentials.c.username == username)
                .values(failed_attempts=failed_attempts, first_failure_at=first_failure_at)
            )

    # ── Identities at external providers ──────────────────────────────────

    def identity_user(self, provider: str, subject: str) -> Optional[str]:
        """The id of the user this identity belongs to, or None when nobody has it."""
        with self._database.begin() as connection:
            row = connection.execute(
                select(identities.c.user_id).where(_identity(provider, subject))
            ).first()
        return row.user_id if row else None

    def add_identity(self, identity: Identity, new_user: Optional[User] = None) -> Optional[str]:
        """Attach the identity to its user, created with it when `new_user` is given.
        The id of the user the identity belongs to afterwards: another one than
        `identity.user_id` when somebody had it already (nothing is written then), and
        None when the user has another identity of that provider already."""
        with self._lock, self._database.begin() as connection:
            owner = connection.execute(
                select(identities.c.user_id).where(_identity(identity.provider, identity.subject))
            ).first()
            if owner is not None:
                return owner.user_id
            if new_user is not None:
                connection.execute(insert(users).values(**vars(new_user)))
            else:
                other = connection.execute(
                    select(identities.c.subject).where(
                        (identities.c.user_id == identity.user_id)
                        & (identities.c.provider == identity.provider)
                    )
                ).first()
                if other is not None:
                    return None
            connection.execute(insert(identities).values(**vars(identity)))
        return identity.user_id

    def list_identities(self, user_id: str) -> list[Identity]:
        """The identities of the user, oldest first."""
        query = (
            select(identities)
            .where(identities.c.user_id == user_id)
            .order_by(identities.c.created_at, identities.c.provider)
        )
        with self._database.begin() as connection:
            return [Identity(**row._mapping) for row in connection.execute(query)]

    def delete_identity(self, user_id: str, provider: str) -> bool:
        """Detach the user's identity at the provider; False when they have none."""
        with self._database.begin() as connection:
            deleted = connection.execute(
                delete(identities).where(
                    (identities.c.user_id == user_id) & (identities.c.provider == provider)
                )
            )
        return deleted.rowcount > 0

    # ── Sessions ──────────────────────────────────────────────────────────

    def add_session(self, session: Session) -> None:
        with self._database.begin() as connection:
            connection.execute(insert(sessions).values(**vars(session)))

    def get_session(self, session_id: str) -> Optional[Session]:
        with self._database.begin() as connection:
            row = connection.execute(select(sessions).where(sessions.c.id == session_id)).first()
        return Session(**row._mapping) if row else None

    def touch_session(self, session_id: str, now: datetime) -> None:
        """Note that the session was used at `now`."""
        with self._database.begin() as connection:
            connection.execute(
                update(sessions).where(sessions.c.id == session_id).values(last_seen_at=now)
            )

    def revoke_session(self, session_id: str, now: datetime) -> bool:
        """End the session (the first time only); False when there is none to end."""
        with self._database.begin() as connection:
            revoked = connection.execute(
                update(sessions)
                .where((sessions.c.id == session_id) & sessions.c.revoked_at.is_(None))
                .values(revoked_at=now)
            )
        return revoked.rowcount > 0

    def delete_ended_sessions(self, unused_since: datetime, created_before: datetime) -> int:
        """Housekeeping, for every user: delete the sessions that are over (revoked,
        not used since `unused_since`, or created before `created_before`). How many."""
        with self._database.begin() as connection:
            deleted = connection.execute(
                delete(sessions).where(
                    or_(
                        sessions.c.revoked_at.is_not(None),
                        sessions.c.last_seen_at < unused_since,
                        sessions.c.created_at < created_before,
                    )
                )
            )
        return deleted.rowcount


def _identity(provider: str, subject: str):
    return (identities.c.provider == provider) & (identities.c.subject == subject)
