"""
HTTP routes of the sign-in, used by `frontend/src/auth/`.

  POST   /auth/register   {"email", "password", "display_name"?} → 202
                          {"email", "expires_in_sec", "resend_in_sec"}: a code was
                          sent to the email, no account exists yet
  POST   /auth/register/verify   {"email", "code"} → 201 + a session: the account is
                          created; 401 a wrong code, 410 no code waits (ask again)
  POST   /auth/login      {"email", "password"} → a session
  POST   /auth/logout     → 204, with or without a valid token
  GET    /auth/session    → {"user": {"id", "display_name"} | null, "expires_at"}
  GET    /auth/providers  → {"providers": [...], "registration": bool}

  POST   /auth/{provider}/link      → {"url"}: where a signed-in user starts a flow that
                                      attaches the provider to their account
  GET    /auth/{provider}/start     → 302 to the provider (`?ticket=` from the link)
  GET    /auth/{provider}/callback  → 302 to the page, with `#auth_code=`,
                                      `#auth_linked=` or `#auth_error=`
  POST   /auth/exchange   {"code"} → a session, for the code the callback left
  GET    /auth/identities → {"identities": [{"provider", "email", "created_at"}]}
  DELETE /auth/identities/{provider} → 204

A session is {"token", "user": {"id", "display_name"}, "expires_at"}. The token is read
from `Authorization: Bearer <token>`. The services are read from `app.state`, set at
startup by `backend/main.py`.
"""
import asyncio
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict

from backend.config import AppConfig
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
from backend.features.accounts.identity import bearer_token, current_user_id
from backend.features.accounts.providers import (
    ExternalSignIn,
    LastWayToSignIn,
    SignInFailed,
    UnknownProvider,
)
from backend.features.accounts.sessions import SessionService
from backend.utils.database import DatabaseError
from backend.utils.logging import get_logger

logger = get_logger("accounts.api")

# The state of a flow, bound to the browser that started it. Sent back by the browser
# on the provider's redirect to the callback (a top-level navigation).
_STATE_COOKIE = "auth_state"
_STATE_COOKIE_MAX_AGE_SEC = 600


class NewAccount(BaseModel):
    """Body of register. Any other field is refused."""
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str
    display_name: Optional[str] = None


class EmailCode(BaseModel):
    """Body of register/verify: the code that was sent to the email."""
    model_config = ConfigDict(extra="forbid")

    email: str
    code: str


class SignIn(BaseModel):
    """Body of login. Any other field is refused."""
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str


class Handover(BaseModel):
    """Body of exchange: the code the callback left for the page."""
    model_config = ConfigDict(extra="forbid")

    code: str


@contextmanager
def _answering() -> Iterator[None]:
    """Turn the failures of the accounts into their HTTP answers."""
    try:
        yield
    except RegistrationClosed as e:
        raise HTTPException(403, str(e)) from e
    except EmailTaken as e:
        raise HTTPException(409, str(e)) from e
    except CodeJustSent as e:
        raise HTTPException(429, str(e), headers={"Retry-After": str(e.seconds)}) from e
    except WrongCode as e:
        raise HTTPException(401, str(e)) from e
    except CodeExpired as e:
        raise HTTPException(410, str(e)) from e
    except CodeNotSent as e:
        raise HTTPException(502, "The code could not be sent") from e
    except LastWayToSignIn as e:
        raise HTTPException(409, str(e)) from e
    except InvalidAccount as e:
        raise HTTPException(422, str(e)) from e
    except WrongCredentials as e:
        raise HTTPException(401, str(e)) from e
    except TooManyAttempts as e:
        raise HTTPException(429, str(e)) from e
    except UnknownProvider as e:
        raise HTTPException(404, "No such sign-in provider") from e
    except DatabaseError as e:
        logger.error({"msg": "Sign-in request failed", "error": str(e)})
        raise HTTPException(502, "Sign-in is not available") from e


def create_auth_router(config: AppConfig) -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])

    def accounts(request: Request) -> AccountService:
        return request.app.state.accounts

    def sessions(request: Request) -> SessionService:
        return request.app.state.sessions

    def sign_in(request: Request) -> ExternalSignIn:
        return request.app.state.sign_in

    def token_of(request: Request) -> Optional[str]:
        return bearer_token(request.headers.get("authorization"))

    def shown(request: Request, user_id: str) -> dict:
        """The user as the page shows them. Blocks."""
        user = request.app.state.users.get(user_id)
        return {"id": user_id, "display_name": user.display_name if user else user_id}

    def open_session(request: Request, user_id: str) -> dict:
        """Sign the user in. Blocks."""
        token, expires_at = sessions(request).open(user_id)
        return {
            "token": token,
            "user": shown(request, user_id),
            "expires_at": expires_at.isoformat(),
        }

    async def user(request: Request) -> str:
        """The id of the signed-in user; a guest has no account to manage."""
        with _answering():
            user_id = await asyncio.to_thread(
                current_user_id, config, request.app.state.users,
                sessions=sessions(request), token=token_of(request),
            )
        if user_id is None:
            raise HTTPException(401, "Login required")
        return user_id

    # ── Local accounts and the session ────────────────────────────────────

    @router.post("/register", status_code=202)
    async def register(request: Request, new: NewAccount) -> dict:
        with _answering():
            sent = await asyncio.to_thread(
                accounts(request).start_registration, new.email, new.password, new.display_name
            )
        return sent.to_dict()

    @router.post("/register/verify", status_code=201)
    async def verify(request: Request, given: EmailCode) -> dict:
        def create() -> dict:
            created = accounts(request).verify_registration(given.email, given.code)
            return open_session(request, created.id)

        with _answering():
            return await asyncio.to_thread(create)

    @router.post("/login")
    async def login(request: Request, credentials: SignIn) -> dict:
        def check() -> dict:
            user_id = accounts(request).login(credentials.email, credentials.password)
            return open_session(request, user_id)

        with _answering():
            return await asyncio.to_thread(check)

    @router.post("/logout", status_code=204)
    async def logout(request: Request) -> Response:
        with _answering():
            await asyncio.to_thread(sessions(request).revoke, token_of(request))
        return Response(status_code=204)

    @router.get("/session")
    async def session(request: Request) -> dict:
        """Who the token signs in, for a page that kept one: never a 401."""
        def read() -> dict:
            expires_at: Optional[datetime] = None
            # AUTH_DEV_USER_ID first: that user has no session to expire.
            user_id = current_user_id(config, request.app.state.users)
            if user_id is None:
                resolved = sessions(request).resolve(token_of(request))
                if resolved is not None:
                    user_id, expires_at = resolved
            return {
                "user": shown(request, user_id) if user_id else None,
                "expires_at": expires_at.isoformat() if expires_at else None,
            }

        with _answering():
            return await asyncio.to_thread(read)

    @router.get("/providers")
    async def providers(request: Request) -> dict:
        return {
            "providers": sign_in(request).providers(),
            "registration": bool(config.AUTH_ALLOW_REGISTRATION),
        }

    # ── External providers ────────────────────────────────────────────────

    def back_to_page(fragment: str) -> RedirectResponse:
        """Send the browser back to the page with what happened in the fragment (never
        sent to a server), and forget the state of the flow."""
        response = RedirectResponse(f"{config.AUTH_PAGE_URL}/#{fragment}", status_code=302)
        response.delete_cookie(_STATE_COOKIE, path="/auth")
        return response

    @router.post("/{provider}/link")
    async def link(request: Request, provider: str, user_id: str = Depends(user)) -> dict:
        if provider not in sign_in(request).providers():
            raise HTTPException(404, "No such sign-in provider")
        ticket = sign_in(request).link_ticket(user_id)
        return {"url": f"{config.AUTH_PUBLIC_URL}/auth/{provider}/start?ticket={quote(ticket)}"}

    @router.get("/{provider}/start")
    async def start(request: Request, provider: str, ticket: Optional[str] = None) -> Response:
        try:
            with _answering():
                url, state = sign_in(request).start(provider, ticket)
        except SignInFailed as e:
            return back_to_page(f"auth_error={e.reason}")
        response = RedirectResponse(url, status_code=302)
        response.set_cookie(
            _STATE_COOKIE, state, max_age=_STATE_COOKIE_MAX_AGE_SEC, path="/auth",
            httponly=True, samesite="lax",
        )
        return response

    @router.get("/{provider}/callback")
    async def callback(
        request: Request,
        provider: str,
        code: Optional[str] = None,
        state: Optional[str] = None,
        error: Optional[str] = None,
        auth_state: Optional[str] = Cookie(None),
    ) -> Response:
        try:
            with _answering():
                outcome = await sign_in(request).finish(provider, code, state, auth_state, error)
        except SignInFailed as e:
            logger.warning({"msg": "External sign-in refused", "provider": provider, "reason": e.reason, "detail": str(e)})
            return back_to_page(f"auth_error={e.reason}")
        if outcome.linked:
            return back_to_page(f"auth_linked={outcome.linked}")
        return back_to_page(f"auth_code={quote(outcome.code or '')}")

    @router.post("/exchange")
    async def exchange(request: Request, handover: Handover) -> dict:
        user_id = sign_in(request).exchange(handover.code)
        if user_id is None:
            raise HTTPException(401, "Unknown or expired code")
        with _answering():
            return await asyncio.to_thread(open_session, request, user_id)

    @router.get("/identities")
    async def identities(request: Request, user_id: str = Depends(user)) -> dict:
        with _answering():
            linked = await sign_in(request).identities(user_id)
        return {"identities": [identity.to_dict() for identity in linked]}

    @router.delete("/identities/{provider}", status_code=204)
    async def unlink(request: Request, provider: str, user_id: str = Depends(user)) -> Response:
        with _answering():
            detached = await sign_in(request).unlink(user_id, provider)
        if not detached:
            raise HTTPException(404, "No such linked account")
        return Response(status_code=204)

    return router
