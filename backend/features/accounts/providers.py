"""
ExternalSignIn — signing in with an account kept elsewhere (Google, Facebook).

The OAuth 2 authorization-code flow with PKCE: the browser is sent to the provider, the
provider sends it back with a code, the code is exchanged for an access token, and the
provider is asked once who the person is. Nothing of the provider is kept after that: no
access token, no refresh token. An identity is the pair (provider, the person's id
there), never an email; it is attached to a user created with it, or, when a signed-in
user asked for it, to their account. Two accounts are never merged because their emails
match.

What is in flight (a flow that was started, a link that was asked for, a sign-in the
page has not collected yet) lives in RAM for a minute or a few: this is one process. A
provider whose client id is not configured does not exist here.
"""
import asyncio
import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import urlencode

import httpx

from backend.config import AppConfig
from backend.features.accounts.store import AccountsStore
from backend.features.memory.memory import one_line
from backend.models import Identity, User
from backend.utils.logging import get_logger

logger = get_logger("accounts.providers")

PROVIDERS = ("google", "facebook")

# How long a started flow may take at the provider, and how long the page has to
# collect what the callback left for it.
_FLOW_TTL = timedelta(minutes=10)
_HANDOVER_TTL = timedelta(minutes=1)

_DISPLAY_NAME_MAX_CHARS = 64

# Why a sign-in did not go through, as the page is told (`#auth_error=<reason>`).
STATE, DENIED, PROVIDER_FAILED = "state", "denied", "provider"
LINKED_ELSEWHERE, ALREADY_LINKED, LINK_EXPIRED = "linked_elsewhere", "already_linked", "link_expired"


class UnknownProvider(Exception):
    """No such provider, or it is not configured."""


class SignInFailed(Exception):
    """The flow did not end in a user. `reason` is what the page is told."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


class LastWayToSignIn(Exception):
    """The identity is the only way the user has to sign in: it cannot be detached."""


@dataclass(frozen=True)
class Provider:
    name: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scope: str


@dataclass(frozen=True)
class Outcome:
    """How a flow ended: `code` is what the page exchanges for a session (a sign-in),
    `linked` the provider that was attached to the account of a signed-in user."""
    code: Optional[str] = None
    linked: Optional[str] = None


@dataclass(frozen=True)
class _Flow:
    provider: str
    verifier: str
    link_user_id: Optional[str]
    started_at: datetime


def configured_providers(config: AppConfig) -> dict[str, Provider]:
    """The providers whose application is configured, by name."""
    found = {}
    for name in PROVIDERS:
        key = f"AUTH_{name.upper()}"
        client_id = getattr(config, f"{key}_CLIENT_ID")
        if client_id:
            found[name] = Provider(
                name=name,
                client_id=client_id,
                client_secret=getattr(config, f"{key}_CLIENT_SECRET"),
                authorize_url=getattr(config, f"{key}_AUTHORIZE_URL"),
                token_url=getattr(config, f"{key}_TOKEN_URL"),
                userinfo_url=getattr(config, f"{key}_USERINFO_URL"),
                scope=getattr(config, f"{key}_SCOPE"),
            )
    return found


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _challenge(verifier: str) -> str:
    """The PKCE challenge of a verifier: its SHA-256, base64url without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class ExternalSignIn:
    def __init__(
        self,
        config: AppConfig,
        store: AccountsStore,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        """`transport` replaces the network in the tests."""
        self.config = config
        self._store = store
        self._transport = transport
        self._clock = clock
        self._flows: dict[str, _Flow] = {}                      # by `state`
        self._tickets: dict[str, tuple[str, datetime]] = {}     # → (user id, asked at)
        self._codes: dict[str, tuple[str, datetime]] = {}       # → (user id, left at)

    def providers(self) -> list[str]:
        """The names of the providers that can be used, in a fixed order."""
        return list(configured_providers(self.config))

    def callback_url(self, provider: str) -> str:
        """Where the provider sends the browser back: fixed by the configuration, never
        taken from a request."""
        return f"{self.config.AUTH_PUBLIC_URL}/auth/{provider}/callback"

    # ── Starting a flow ───────────────────────────────────────────────────

    def link_ticket(self, user_id: str) -> str:
        """A ticket a signed-in user starts a flow with, so that the identity is
        attached to their account: the start of a flow is a navigation of the browser,
        which carries no session token. Valid once, for a minute."""
        self._forget_old()
        ticket = secrets.token_urlsafe(24)
        self._tickets[ticket] = (user_id, self._clock())
        return ticket

    def start(self, provider: str, ticket: Optional[str] = None) -> tuple[str, str]:
        """Where to send the browser, and the `state` to bind to it (a cookie): the
        callback must come back with both. Raises UnknownProvider, or SignInFailed
        when `ticket` is not a valid one."""
        found = self._provider(provider)
        self._forget_old()
        link_user_id = None
        if ticket is not None:
            asked = self._tickets.pop(ticket, None)
            if asked is None:
                raise SignInFailed(LINK_EXPIRED)
            link_user_id = asked[0]
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        self._flows[state] = _Flow(provider, verifier, link_user_id, self._clock())
        query = urlencode({
            "response_type": "code",
            "client_id": found.client_id,
            "redirect_uri": self.callback_url(provider),
            "scope": found.scope,
            "state": state,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
        })
        separator = "&" if "?" in found.authorize_url else "?"
        return f"{found.authorize_url}{separator}{query}", state

    # ── Coming back from the provider ─────────────────────────────────────

    async def finish(
        self,
        provider: str,
        code: Optional[str],
        state: Optional[str],
        bound_state: Optional[str],
        error: Optional[str] = None,
    ) -> Outcome:
        """End the flow the provider sent the browser back from. `bound_state` is the
        state the browser that started the flow was given. Raises UnknownProvider or
        SignInFailed; nothing is created then."""
        found = self._provider(provider)
        flow = self._flows.pop(state or "", None)
        if (
            flow is None
            or flow.provider != provider
            or self._clock() - flow.started_at > _FLOW_TTL
            or not hmac.compare_digest(state or "", bound_state or "")
        ):
            raise SignInFailed(STATE)
        if error or not code:
            raise SignInFailed(DENIED, error or "no code")

        subject, name, email = await self._who(found, code, flow.verifier)
        now = self._clock()
        if flow.link_user_id is not None:
            identity = Identity(provider, subject, flow.link_user_id, email, now)
            owner = await asyncio.to_thread(self._store.add_identity, identity)
            if owner is None:
                raise SignInFailed(ALREADY_LINKED)
            if owner != flow.link_user_id:
                raise SignInFailed(LINKED_ELSEWHERE)
            logger.info({"msg": "Identity linked", "provider": provider, "user_id": owner})
            return Outcome(linked=provider)

        user_id = await asyncio.to_thread(self._store.identity_user, provider, subject)
        if user_id is None:
            shown = (one_line(name or "") or provider.capitalize())[:_DISPLAY_NAME_MAX_CHARS]
            user = User(id=uuid.uuid4().hex, display_name=shown, created_at=now, last_seen_at=now)
            identity = Identity(provider, subject, user.id, email, now)
            # Somebody may have created it meanwhile: the identity has one user.
            user_id = await asyncio.to_thread(self._store.add_identity, identity, user)
            logger.info({"msg": "Account created", "provider": provider, "user_id": user_id})
        handover = secrets.token_urlsafe(24)
        self._codes[handover] = (user_id, self._clock())
        return Outcome(code=handover)

    def exchange(self, code: str) -> Optional[str]:
        """The id of the user a callback left a sign-in for, once and within a minute;
        None for an unknown, used or expired code."""
        left = self._codes.pop(code, None)
        if left is None or self._clock() - left[1] > _HANDOVER_TTL:
            return None
        return left[0]

    async def _who(self, provider: Provider, code: str, verifier: str) -> tuple[str, str, Optional[str]]:
        """Ask the provider who the code stands for: (their id there, their name, their
        email when given). The access token is used once and kept nowhere."""
        timeout = self.config.AUTH_PROVIDER_TIMEOUT_SEC
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as http:
                token = await http.post(provider.token_url, data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.callback_url(provider.name),
                    "client_id": provider.client_id,
                    "client_secret": provider.client_secret,
                    "code_verifier": verifier,
                })
                token.raise_for_status()
                access_token = token.json().get("access_token")
                if not isinstance(access_token, str) or not access_token:
                    raise SignInFailed(PROVIDER_FAILED, "no access token")
                who = await http.get(
                    provider.userinfo_url, headers={"Authorization": f"Bearer {access_token}"}
                )
                who.raise_for_status()
                info = who.json()
        except (httpx.HTTPError, ValueError) as e:
            # The provider's own text may hold the code or the secret: only its kind.
            raise SignInFailed(PROVIDER_FAILED, type(e).__name__) from e
        if not isinstance(info, dict):
            raise SignInFailed(PROVIDER_FAILED, "not an object")
        subject = info.get("sub") or info.get("id")
        if not isinstance(subject, (str, int)) or isinstance(subject, bool) or not str(subject):
            raise SignInFailed(PROVIDER_FAILED, "no subject")
        name, email = info.get("name"), info.get("email")
        return (
            str(subject),
            name if isinstance(name, str) else "",
            one_line(email) if isinstance(email, str) and email.strip() else None,
        )

    # ── The identities of a signed-in user ────────────────────────────────

    async def identities(self, user_id: str) -> list[Identity]:
        return await asyncio.to_thread(self._store.list_identities, user_id)

    async def unlink(self, user_id: str, provider: str) -> bool:
        """Detach the user's identity at the provider; False when they have none.
        Raises LastWayToSignIn when nothing else signs them in."""
        def detach() -> bool:
            linked = self._store.list_identities(user_id)
            if not any(identity.provider == provider for identity in linked):
                return False
            if len(linked) == 1 and not self._store.has_credentials(user_id):
                raise LastWayToSignIn(f"'{provider}' is the only way left to sign in")
            return self._store.delete_identity(user_id, provider)

        return await asyncio.to_thread(detach)

    # ── Internals ─────────────────────────────────────────────────────────

    def _provider(self, name: str) -> Provider:
        found = configured_providers(self.config).get(name)
        if found is None:
            raise UnknownProvider(name)
        return found

    def _forget_old(self) -> None:
        """Drop what was started or left and never collected."""
        now = self._clock()
        self._flows = {
            state: flow for state, flow in self._flows.items()
            if now - flow.started_at <= _FLOW_TTL
        }
        for kept in (self._tickets, self._codes):
            for key in [key for key, (_, at) in kept.items() if now - at > _HANDOVER_TTL]:
                del kept[key]
