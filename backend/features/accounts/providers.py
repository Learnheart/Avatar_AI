"""
ExternalSignIn — signing in with an account kept elsewhere (Google, Facebook).

The OAuth 2 authorization-code flow with PKCE: the browser is sent to the provider, the
provider sends it back with a code, the code is exchanged for an access token, and the
provider is asked once who the person is. Nothing of the provider is kept after that: no
access token, no refresh token. An identity is the pair (provider, the person's id
there), never an email; it is attached to a user created with it, or, when a signed-in
user asked for it, to their account. One email has one account: an identity whose email
the provider says it has verified, and which is the email of a local account (proved
there by a code), signs that account in and is attached to it. Nothing else is merged
by email.

A flow ends in the page that started it, and in no other. A sign-in is started with a
challenge, the SHA-256 of a secret the page keeps: the code the callback leaves is
exchanged for a session only with that secret, so a link made by somebody else signs
nobody in. A link attaches nothing at the callback: the proved identity waits until the
page confirms it with the session of the user who asked for it.

What is in flight (a flow that was started, a link that was asked for, a sign-in or a
proved identity the page has not collected yet) lives in RAM for a minute or a few:
this is one process. A provider whose client id is not configured does not exist here.
"""
import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import urlencode

import httpx

from backend.config import AppConfig
from backend.features.accounts.accounts import normal_email
from backend.features.accounts.store import LAST_WAY, NOT_LINKED, AccountsStore
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

# What the page gives to start a sign-in: the SHA-256 of its secret, base64url without
# padding.
_PAGE_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")

# Why a flow did not go through, as the page is told (`#auth_error=<reason>`).
STATE, DENIED, PROVIDER_FAILED, LINK_EXPIRED = "state", "denied", "provider", "link_expired"


class UnknownProvider(Exception):
    """No such provider, or it is not configured."""


class SignInFailed(Exception):
    """The flow did not end in a user. `reason` is what the page is told."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


class LastWayToSignIn(Exception):
    """The identity is the only way the user has to sign in: it cannot be detached."""


class ChallengeRequired(Exception):
    """A sign-in was started without the challenge that binds it to its page."""


class LinkExpired(Exception):
    """No proved identity waits under that code: unknown, used or too old."""


class LinkNotYours(Exception):
    """The proved identity waits for another user than the one confirming it."""


class IdentityTaken(Exception):
    """The identity belongs to another user, or the user has another identity of that
    provider already: nothing was attached."""


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
    """How a flow ended, as a code left for the page: `code` is what it exchanges for a
    session (a sign-in), `link` what a signed-in user confirms to attach the identity
    that was proved to their account."""
    code: Optional[str] = None
    link: Optional[str] = None


@dataclass(frozen=True)
class _Flow:
    provider: str
    verifier: str                 # PKCE, between this backend and the provider
    link_user_id: Optional[str]
    challenge: Optional[str]      # of the page that started a sign-in
    at: datetime


@dataclass(frozen=True)
class _SignIn:
    """A sign-in that waits for its page."""
    user_id: str
    challenge: str
    at: datetime


@dataclass(frozen=True)
class _ProvedIdentity:
    """An identity the provider vouched for, that waits to be confirmed by the user
    who asked to link it."""
    provider: str
    subject: str
    email: Optional[str]
    user_id: str
    at: datetime


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
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
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
        self._codes: dict[str, _SignIn] = {}                    # by hand-off code
        self._links: dict[str, _ProvedIdentity] = {}            # by link code

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

    def start(
        self, provider: str, ticket: Optional[str] = None, challenge: Optional[str] = None
    ) -> tuple[str, str]:
        """Where to send the browser, and the `state` to bind to it (a cookie): the
        callback must come back with both. A sign-in needs `challenge`, the SHA-256
        (base64url) of the secret its page will exchange the result with; a link needs
        `ticket` and no challenge, since its result is confirmed with the session.
        Raises UnknownProvider, ChallengeRequired, or SignInFailed when `ticket` is not
        a valid one."""
        found = self._provider(provider)
        self._forget_old()
        link_user_id = None
        if ticket is not None:
            asked = self._tickets.pop(ticket, None)
            if asked is None:
                raise SignInFailed(LINK_EXPIRED)
            link_user_id, challenge = asked[0], None
        elif not challenge or not _PAGE_CHALLENGE.fullmatch(challenge):
            raise ChallengeRequired(provider)
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        self._flows[state] = _Flow(provider, verifier, link_user_id, challenge, self._clock())
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
        state the browser that started the flow was given. A link attaches nothing
        here: the identity is kept until the user confirms it (`confirm_link`). Raises
        UnknownProvider or SignInFailed; nothing is created then."""
        found = self._provider(provider)
        flow = self._flows.pop(state or "", None)
        if (
            flow is None
            or flow.provider != provider
            or self._clock() - flow.at > _FLOW_TTL
            or not hmac.compare_digest(state or "", bound_state or "")
        ):
            raise SignInFailed(STATE)
        if error or not code:
            raise SignInFailed(DENIED, error or "no code")

        subject, name, email, verified = await self._who(found, code, flow.verifier)
        now = self._clock()
        if flow.link_user_id is not None:
            link = secrets.token_urlsafe(24)
            self._links[link] = _ProvedIdentity(provider, subject, email, flow.link_user_id, now)
            return Outcome(link=link)

        user_id = await asyncio.to_thread(self._store.identity_user, provider, subject)
        if user_id is None and email and verified:
            # The same address, proved on both sides: the same person.
            local = await asyncio.to_thread(self._store.get_credentials, normal_email(email))
            if local is not None:
                identity = Identity(provider, subject, local.user_id, email, now)
                user_id = await asyncio.to_thread(self._store.add_identity, identity)
                if user_id is not None:
                    logger.info(
                        {"msg": "Identity joined the account of its email", "provider": provider}
                    )
                else:
                    # That account signs in with another identity of this provider.
                    logger.info({
                        "msg": "The account of this email already has an identity of this "
                               "provider: a separate account is created",
                        "provider": provider,
                    })
        if user_id is None:
            shown = (one_line(name or "") or provider.capitalize())[:_DISPLAY_NAME_MAX_CHARS]
            user = User(id=uuid.uuid4().hex, display_name=shown, created_at=now, last_seen_at=now)
            identity = Identity(provider, subject, user.id, email, now)
            # Somebody may have created it meanwhile: the identity has one user.
            user_id = await asyncio.to_thread(self._store.add_identity, identity, user)
            logger.info({"msg": "Account created", "provider": provider, "user_id": user_id})
        handover = secrets.token_urlsafe(24)
        # A sign-in flow was started with a challenge (`start`).
        self._codes[handover] = _SignIn(user_id, flow.challenge or "", self._clock())
        return Outcome(code=handover)

    def exchange(self, code: str, verifier: Optional[str]) -> Optional[str]:
        """The id of the user a callback left a sign-in for, once and within a minute,
        for the page that started the flow: `verifier` is the secret whose challenge
        the flow was started with. None for an unknown, used or expired code and for
        another verifier; the code is spent whatever the answer."""
        left = self._codes.pop(code, None)
        if left is None or self._clock() - left.at > _HANDOVER_TTL:
            return None
        if not left.challenge or not hmac.compare_digest(
            _challenge(verifier or "").encode("ascii"), left.challenge.encode("ascii")
        ):
            logger.warning({"msg": "Sign-in collected with another verifier, refused"})
            return None
        return left.user_id

    async def confirm_link(self, code: str, user_id: str) -> str:
        """Attach the identity a link flow proved to the account of `user_id`, who
        presents the code the callback left; the provider that was attached. Once and
        within a minute. Raises LinkExpired, LinkNotYours when the flow was started by
        another user, IdentityTaken; nothing is attached then."""
        proved = self._links.pop(code, None)
        if proved is None or self._clock() - proved.at > _HANDOVER_TTL:
            raise LinkExpired("no identity waits under that code")
        if proved.user_id != user_id:
            logger.warning({"msg": "Link confirmed by another user, refused", "provider": proved.provider})
            raise LinkNotYours("that link was asked for by another user")
        identity = Identity(proved.provider, proved.subject, user_id, proved.email, self._clock())
        owner = await asyncio.to_thread(self._store.add_identity, identity)
        if owner is None:
            raise IdentityTaken(f"another '{proved.provider}' account is linked already")
        if owner != user_id:
            raise IdentityTaken(f"that '{proved.provider}' account belongs to another user")
        logger.info({"msg": "Identity linked", "provider": proved.provider, "user_id": user_id})
        return proved.provider

    async def _who(
        self, provider: Provider, code: str, verifier: str
    ) -> tuple[str, str, Optional[str], bool]:
        """Ask the provider who the code stands for: (their id there, their name, their
        email when given, whether the provider says it verified that email). The access
        token is used once and kept nowhere."""
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
            info.get("email_verified") is True,
        )

    # ── The identities of a signed-in user ────────────────────────────────

    async def identities(self, user_id: str) -> list[Identity]:
        return await asyncio.to_thread(self._store.list_identities, user_id)

    async def unlink(self, user_id: str, provider: str) -> bool:
        """Detach the user's identity at the provider; False when they have none.
        Raises LastWayToSignIn when nothing else signs them in."""
        detached = await asyncio.to_thread(self._store.detach_identity, user_id, provider)
        if detached == LAST_WAY:
            raise LastWayToSignIn(f"'{provider}' is the only way left to sign in")
        return detached != NOT_LINKED

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
            if now - flow.at <= _FLOW_TTL
        }
        self._tickets = {
            ticket: asked for ticket, asked in self._tickets.items()
            if now - asked[1] <= _HANDOVER_TTL
        }
        self._codes = {
            code: left for code, left in self._codes.items() if now - left.at <= _HANDOVER_TTL
        }
        self._links = {
            code: proved for code, proved in self._links.items()
            if now - proved.at <= _HANDOVER_TTL
        }
