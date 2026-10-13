"""Signing in with Google or Facebook, the provider's HTTP replaced by a fake."""
import asyncio
import base64
import hashlib
import threading
from typing import Iterator
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import AppConfig
from backend.main import create_app
from tests.unit.accounts_fakes import create_account
from tests.unit.fakes import build_orchestrator

PAGE = "http://kiosk.test:3000"
API = "http://kiosk.test:8000"

# The secret a page keeps while its sign-in is at the provider, and another page's.
VERIFIER = "the-secret-of-the-page-that-started-the-sign-in"
OTHER_VERIFIER = "the-secret-of-another-page"


def _challenge(verifier: str = VERIFIER) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class FakeProvider:
    """The token and "who am I" endpoints of every provider. `people` maps a code the
    provider gave to the person it stands for; `requests` holds what was asked."""

    def __init__(self) -> None:
        self.people = {
            "code-linh": {"sub": "g-linh", "name": "Khánh Linh", "email": "linh@example.com"},
            "code-an": {"sub": "g-an", "name": "An", "email": "linh@example.com"},
            "code-fb": {"id": 1234567, "name": "Linh FB"},
        }
        self.requests: list[httpx.Request] = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            return httpx.Response(500, text="client_secret=very-secret is wrong")
        if request.method == "POST":
            form = parse_qs(request.content.decode())
            return httpx.Response(200, json={"access_token": "at-" + form["code"][0]})
        code = request.headers["authorization"].removeprefix("Bearer at-")
        return httpx.Response(200, json=self.people[code])


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def client(config: AppConfig, provider: FakeProvider) -> Iterator[TestClient]:
    config.AUTH_GOOGLE_CLIENT_ID, config.AUTH_GOOGLE_CLIENT_SECRET = "g-client", "g-secret"
    config.AUTH_FACEBOOK_CLIENT_ID, config.AUTH_FACEBOOK_CLIENT_SECRET = "f-client", "f-secret"
    config.AUTH_PUBLIC_URL, config.AUTH_PAGE_URL = API, PAGE
    with TestClient(create_app(config, build_orchestrator(config))) as test_client:
        test_client.app.state.sign_in._transport = httpx.MockTransport(provider)
        yield test_client


def _start(client, name: str = "google", ticket: str | None = None) -> dict:
    """Start a flow as a browser does (a link with its ticket, a sign-in with the
    challenge of the page); the query the provider was sent."""
    params = {"ticket": ticket} if ticket else {"challenge": _challenge()}
    response = client.get(f"/auth/{name}/start", params=params, follow_redirects=False)
    assert response.status_code == 302, response.text
    return {key: value[0] for key, value in parse_qs(urlsplit(response.headers["location"]).query).items()}


def _fragment(response) -> str:
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(f"{PAGE}/#")
    return location.split("#", 1)[1]


def _callback(client, code: str, state: str, name: str = "google") -> str:
    return _fragment(client.get(
        f"/auth/{name}/callback", params={"code": code, "state": state}, follow_redirects=False
    ))


def _sign_in(client, code: str = "code-linh", name: str = "google") -> dict:
    """The whole flow of a guest: the session the page ends up with."""
    sent = _start(client, name)
    fragment = _callback(client, code, sent["state"], name)
    assert fragment.startswith("auth_code=")
    response = _exchange(client, fragment.removeprefix("auth_code="))
    assert response.status_code == 200, response.text
    return response.json()


def _exchange(client, code: str, verifier: str | None = VERIFIER):
    body = {"code": code} if verifier is None else {"code": code, "verifier": verifier}
    return client.post("/auth/exchange", json=body)


def _bearer(session: dict) -> dict:
    return {"Authorization": f"Bearer {session['token']}"}


# ── Starting ──────────────────────────────────────────────────────────────

def test_the_configured_providers_are_offered(client, config):
    assert client.get("/auth/providers").json()["providers"] == ["google", "facebook"]

    config.AUTH_FACEBOOK_CLIENT_ID = ""

    assert client.get("/auth/providers").json()["providers"] == ["google"]
    params = {"challenge": _challenge()}
    assert client.get("/auth/facebook/start", params=params, follow_redirects=False).status_code == 404
    assert client.get("/auth/github/start", params=params, follow_redirects=False).status_code == 404


@pytest.mark.parametrize("params", [None, {"challenge": ""}, {"challenge": "too-short"},
                                    {"challenge": VERIFIER + "="}, {"challenge": "é" * 43}])
def test_a_sign_in_started_without_a_challenge_is_refused(client, params):
    response = client.get("/auth/google/start", params=params, follow_redirects=False)

    assert response.status_code == 400
    assert "set-cookie" not in response.headers and client.app.state.sign_in._flows == {}


def test_starting_sends_the_browser_to_the_provider_with_a_state_and_a_challenge(client, config):
    response = client.get(
        "/auth/google/start", params={"challenge": _challenge()}, follow_redirects=False
    )
    location = urlsplit(response.headers["location"])
    sent = {key: value[0] for key, value in parse_qs(location.query).items()}

    assert f"{location.scheme}://{location.netloc}{location.path}" == config.AUTH_GOOGLE_AUTHORIZE_URL
    assert sent["client_id"] == "g-client" and "client_secret" not in sent
    assert sent["redirect_uri"] == f"{API}/auth/google/callback"
    assert sent["response_type"] == "code" and sent["scope"] == "openid email profile"
    assert sent["code_challenge_method"] == "S256" and len(sent["code_challenge"]) == 43
    assert len(sent["state"]) >= 32
    cookie = response.headers["set-cookie"]
    assert f"auth_state={sent['state']}" in cookie
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/auth" in cookie
    assert "secure" not in cookie.lower()  # served over http here
    # The page's challenge stays here: the provider is given this backend's own.
    assert sent["code_challenge"] != _challenge()


def test_the_state_cookie_is_secure_when_the_backend_is_served_over_https(config, provider):
    config.AUTH_GOOGLE_CLIENT_ID, config.AUTH_GOOGLE_CLIENT_SECRET = "g-client", "g-secret"
    config.AUTH_PUBLIC_URL, config.AUTH_PAGE_URL = "https://kiosk.test", PAGE
    with TestClient(
        create_app(config, build_orchestrator(config)), base_url="https://kiosk.test"
    ) as https:
        https.app.state.sign_in._transport = httpx.MockTransport(provider)
        started = https.get(
            "/auth/google/start", params={"challenge": _challenge()}, follow_redirects=False
        )
        state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
        back = https.get(
            "/auth/google/callback", params={"code": "code-linh", "state": state},
            follow_redirects=False,
        )

    assert "; Secure" in started.headers["set-cookie"]
    assert back.headers["location"].startswith(f"{PAGE}/#auth_code=")
    assert "; Secure" in back.headers["set-cookie"]  # forgotten the way it was set


# ── Signing in ────────────────────────────────────────────────────────────

def test_a_new_identity_creates_a_user_and_signs_them_in(client, provider):
    session = _sign_in(client)

    assert session["user"]["display_name"] == "Khánh Linh"
    assert client.get("/auth/session", headers=_bearer(session)).json()["user"] == session["user"]
    assert client.get("/auth/identities", headers=_bearer(session)).json()["identities"] == [
        {"provider": "google", "email": "linh@example.com",
         "created_at": client.get("/auth/identities", headers=_bearer(session)).json()["identities"][0]["created_at"]}
    ]
    assert client.app.state.users.count() == 1


def test_the_code_is_exchanged_with_the_secret_and_the_verifier_of_its_flow(client, provider):
    sent = _start(client)
    _callback(client, "code-linh", sent["state"])

    exchange, who = provider.requests
    form = {key: value[0] for key, value in parse_qs(exchange.content.decode()).items()}
    assert str(exchange.url) == "https://oauth2.googleapis.com/token"
    assert form["client_id"] == "g-client" and form["client_secret"] == "g-secret"
    assert form["code"] == "code-linh" and form["grant_type"] == "authorization_code"
    assert form["redirect_uri"] == f"{API}/auth/google/callback"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest())
    assert challenge.rstrip(b"=").decode() == sent["code_challenge"]
    assert who.headers["authorization"] == "Bearer at-code-linh"


def test_the_same_identity_signs_the_same_user_in_again(client):
    first = _sign_in(client)
    second = _sign_in(client)

    assert second["user"] == first["user"] and second["token"] != first["token"]
    assert client.app.state.users.count() == 1


def test_facebook_names_the_person_by_its_own_id(client, provider):
    session = _sign_in(client, "code-fb", "facebook")

    assert session["user"]["display_name"] == "Linh FB"
    assert client.app.state.sign_in._store.identity_user("facebook", "1234567") == session["user"]["id"]
    assert str(provider.requests[0].url) == "https://graph.facebook.com/v19.0/oauth/access_token"


def test_two_people_with_the_same_email_are_two_users(client):
    linh, an = _sign_in(client, "code-linh"), _sign_in(client, "code-an")

    assert linh["user"]["id"] != an["user"]["id"]
    assert client.app.state.users.count() == 2


def test_nothing_of_the_provider_is_kept(client, config):
    from sqlalchemy import create_engine, text

    session = _sign_in(client)

    engine = create_engine(config.DATABASE_URL)
    with engine.connect() as connection:
        stored = [
            str(row)
            for table in ("users", "identities", "sessions")
            for row in connection.execute(text(f"SELECT * FROM {table}"))
        ]
    engine.dispose()
    assert all("at-code-linh" not in row and session["token"] not in row for row in stored)


def test_the_code_left_for_the_page_works_once_and_is_not_the_token(client):
    sent = _start(client)
    code = _callback(client, "code-linh", sent["state"]).removeprefix("auth_code=")

    first = _exchange(client, code)
    again = _exchange(client, code)

    assert first.status_code == 200 and first.json()["token"] != code
    assert again.status_code == 401
    assert _exchange(client, "made-up").status_code == 401
    assert client.get("/auth/session", headers={"Authorization": f"Bearer {code}"}).json()["user"] is None


def test_the_code_left_for_the_page_expires_after_a_minute(client):
    from datetime import datetime, timedelta, timezone

    sign_in = client.app.state.sign_in
    sent = _start(client)
    code = _callback(client, "code-linh", sent["state"]).removeprefix("auth_code=")

    sign_in._clock = lambda: datetime.now(timezone.utc) + timedelta(seconds=61)

    assert _exchange(client, code).status_code == 401


@pytest.mark.parametrize("verifier", [OTHER_VERIFIER, "", None, _challenge(), "bí mật"])
def test_the_code_signs_in_only_the_page_that_started_and_is_spent_by_a_wrong_secret(
    client, verifier
):
    sent = _start(client)
    code = _callback(client, "code-linh", sent["state"]).removeprefix("auth_code=")

    refused = _exchange(client, code, verifier)

    assert refused.status_code == 401 and "token" not in refused.text
    # Spent: the page that started cannot be raced with guesses.
    assert _exchange(client, code).status_code == 401
    assert client.app.state.sign_in._codes == {}


def test_the_secret_of_the_page_is_not_kept_and_the_exchange_takes_nothing_else(client):
    sent = _start(client)
    code = _callback(client, "code-linh", sent["state"]).removeprefix("auth_code=")

    assert VERIFIER not in repr(vars(client.app.state.sign_in))
    extra = client.post("/auth/exchange", json={"code": code, "verifier": VERIFIER, "user": "x"})
    assert extra.status_code == 422


# ── Refusals ──────────────────────────────────────────────────────────────

def test_a_callback_with_another_state_is_refused(client, provider):
    _start(client)

    assert _callback(client, "code-linh", "another-state") == "auth_error=state"
    assert provider.requests == [] and client.app.state.users.count() == 0


def test_a_callback_from_a_browser_that_did_not_start_the_flow_is_refused(client, provider):
    sent = _start(client)
    client.cookies.clear()  # the state was bound to the browser that started

    assert _callback(client, "code-linh", sent["state"]) == "auth_error=state"
    assert provider.requests == []


def test_a_state_is_used_once_and_only_for_its_provider(client):
    sent = _start(client)
    assert _callback(client, "code-fb", sent["state"], "facebook") == "auth_error=state"

    sent = _start(client)
    assert _callback(client, "code-linh", sent["state"]).startswith("auth_code=")
    assert _callback(client, "code-linh", sent["state"]) == "auth_error=state"


def test_a_person_who_refused_at_the_provider_is_sent_back_as_a_guest(client, provider):
    sent = _start(client)

    response = client.get(
        "/auth/google/callback",
        params={"error": "access_denied", "state": sent["state"]},
        follow_redirects=False,
    )

    assert _fragment(response) == "auth_error=denied"
    assert provider.requests == [] and client.app.state.users.count() == 0
    assert "auth_state=" in response.headers["set-cookie"]  # the state is forgotten


def test_a_failing_provider_creates_nothing_and_its_text_goes_nowhere(client, provider, caplog):
    provider.down = True
    sent = _start(client)

    fragment = _callback(client, "code-linh", sent["state"])

    assert fragment == "auth_error=provider"
    assert client.app.state.users.count() == 0
    assert "very-secret" not in "\n".join(str(record.msg) for record in caplog.records)


def test_an_answer_without_an_identity_is_refused(client, provider):
    provider.people["code-linh"] = {"name": "No id"}
    sent = _start(client)

    assert _callback(client, "code-linh", sent["state"]) == "auth_error=provider"
    assert client.app.state.users.count() == 0


# ── Linking ───────────────────────────────────────────────────────────────

def _local_account(client, email: str = "local@example.com") -> dict:
    return create_account(client, email)


def _prove(client, session: dict, code: str, name: str = "google") -> str:
    """The flow of a link up to the callback: the code it leaves for the page."""
    asked = client.post(f"/auth/{name}/link", headers=_bearer(session))
    assert asked.status_code == 200, asked.text
    url = urlsplit(asked.json()["url"])
    assert asked.json()["url"].startswith(f"{API}/auth/{name}/start?ticket=")
    sent = _start(client, name, parse_qs(url.query)["ticket"][0])
    fragment = _callback(client, code, sent["state"], name)
    assert fragment.startswith("auth_link="), fragment
    return fragment.removeprefix("auth_link=")


def _confirm(client, session: dict | None, code: str):
    return client.post(
        "/auth/identities", json={"code": code}, headers=_bearer(session) if session else {}
    )


def _link(client, session: dict, code: str, name: str = "google"):
    """The whole link: the flow, then the page confirming it with its session."""
    return _confirm(client, session, _prove(client, session, code, name))


def _identities(client, session: dict) -> list[tuple]:
    linked = client.get("/auth/identities", headers=_bearer(session)).json()["identities"]
    return [(found["provider"], found["email"]) for found in linked]


def test_a_signed_in_user_links_an_identity_and_signs_in_either_way(client):
    local = _local_account(client)

    linked = _link(client, local, "code-linh")

    assert (linked.status_code, linked.json()) == (200, {"provider": "google"})
    by_google = _sign_in(client)
    assert by_google["user"] == local["user"]
    assert client.app.state.users.count() == 1
    assert _identities(client, local) == [("google", "linh@example.com")]


def test_the_callback_of_a_link_attaches_nothing(client):
    local = _local_account(client)

    code = _prove(client, local, "code-linh")

    assert _identities(client, local) == []
    assert client.app.state.sign_in._store.identity_user("google", "g-linh") is None
    # Not a sign-in either: the code opens no session, with or without a secret.
    assert _exchange(client, code).status_code == 401


def test_a_link_confirmed_by_another_user_is_refused_and_attaches_nothing(client):
    """A link made by somebody else, opened by a signed-in reader: the reader's
    identity at the provider must not end on the account of who made the link."""
    attacker, reader = _local_account(client), _local_account(client, "reader@example.com")
    code = _prove(client, attacker, "code-linh")  # the reader went through the provider

    refused = _confirm(client, reader, code)

    assert refused.status_code == 403
    assert _identities(client, attacker) == [] and _identities(client, reader) == []
    # Spent: the one who made the link cannot confirm it afterwards.
    assert _confirm(client, attacker, code).status_code == 410
    assert client.app.state.sign_in._store.identity_user("google", "g-linh") is None


def test_a_link_is_confirmed_once_within_a_minute_and_only_with_a_session(client):
    from datetime import datetime, timedelta, timezone

    local = _local_account(client)
    sign_in = client.app.state.sign_in

    code = _prove(client, local, "code-linh")
    assert _confirm(client, None, code).status_code == 401
    assert _confirm(client, local, code).status_code == 200  # a guest spent nothing
    assert _confirm(client, local, code).status_code == 410
    assert _confirm(client, local, "made-up").status_code == 410

    late = _prove(client, local, "code-fb", "facebook")
    sign_in._clock = lambda: datetime.now(timezone.utc) + timedelta(seconds=61)
    assert _confirm(client, local, late).status_code == 410
    assert _identities(client, local) == [("google", "linh@example.com")]
    assert client.post(
        "/auth/identities", json={"code": code, "user_id": "x"}, headers=_bearer(local)
    ).status_code == 422


def test_an_identity_of_another_account_is_not_linked(client):
    google_user = _sign_in(client)
    local = _local_account(client)

    assert _link(client, local, "code-linh").status_code == 409

    assert _identities(client, local) == []
    assert _sign_in(client)["user"] == google_user["user"]


def test_a_second_identity_of_the_same_provider_is_not_linked(client):
    local = _local_account(client)
    _link(client, local, "code-linh")

    assert _link(client, local, "code-an").status_code == 409
    assert _identities(client, local) == [("google", "linh@example.com")]


def test_a_ticket_links_once_and_a_made_up_one_links_nothing(client):
    local = _local_account(client)
    asked = client.post("/auth/google/link", headers=_bearer(local)).json()["url"]
    ticket = parse_qs(urlsplit(asked).query)["ticket"][0]
    _start(client, "google", ticket)

    for used in (ticket, "made-up"):
        response = client.get("/auth/google/start", params={"ticket": used}, follow_redirects=False)
        assert _fragment(response) == "auth_error=link_expired"


def test_a_guest_cannot_ask_to_link_or_confirm_or_list_or_unlink(client):
    responses = [
        client.post("/auth/google/link"),
        client.post("/auth/identities", json={"code": "any"}),
        client.get("/auth/identities"),
        client.delete("/auth/identities/google"),
    ]

    assert [response.status_code for response in responses] == [401] * 4


def test_linking_an_unknown_provider_answers_404(client):
    local = _local_account(client)

    assert client.post("/auth/github/link", headers=_bearer(local)).status_code == 404


def test_an_identity_is_unlinked_while_another_way_to_sign_in_remains(client):
    local = _local_account(client)
    _link(client, local, "code-linh")

    assert client.delete("/auth/identities/google", headers=_bearer(local)).status_code == 204

    assert client.get("/auth/identities", headers=_bearer(local)).json()["identities"] == []
    assert client.delete("/auth/identities/google", headers=_bearer(local)).status_code == 404
    # The identity is free again: it now signs in a user of its own.
    assert _sign_in(client)["user"]["id"] != local["user"]["id"]


def test_the_only_way_to_sign_in_cannot_be_unlinked(client):
    session = _sign_in(client)

    assert client.delete("/auth/identities/google", headers=_bearer(session)).status_code == 409

    _link(client, session, "code-fb", "facebook")
    assert client.delete("/auth/identities/google", headers=_bearer(session)).status_code == 204
    assert client.delete("/auth/identities/facebook", headers=_bearer(session)).status_code == 409


def test_two_unlinks_at_once_leave_one_way_to_sign_in(client):
    session = _sign_in(client)  # no password: its identities are all it signs in with
    _link(client, session, "code-fb", "facebook")
    sign_in, user_id = client.app.state.sign_in, session["user"]["id"]
    store = sign_in._store
    # Both look before either deletes, wherever the look and the deletion are apart.
    both_looked = threading.Barrier(2, timeout=1)
    list_identities = store.list_identities

    def listing(*args):
        linked = list_identities(*args)
        try:
            both_looked.wait()
        except threading.BrokenBarrierError:
            pass
        return linked

    store.list_identities = listing

    async def unlink_both() -> list:
        return await asyncio.gather(
            sign_in.unlink(user_id, "google"),
            sign_in.unlink(user_id, "facebook"),
            return_exceptions=True,
        )

    results = asyncio.run(unlink_both())
    store.list_identities = list_identities

    assert sum(result is True for result in results) == 1
    assert sum(type(result).__name__ == "LastWayToSignIn" for result in results) == 1
    assert len(store.list_identities(user_id)) == 1


# ── One email, one account ────────────────────────────────────────────────

def test_a_verified_email_of_a_local_account_signs_that_account_in(client, provider):
    provider.people["code-linh"]["email_verified"] = True
    local = _local_account(client, "Linh@Example.com")

    by_google = _sign_in(client)

    assert by_google["user"] == local["user"]
    assert client.app.state.users.count() == 1
    linked = client.get("/auth/identities", headers=_bearer(local)).json()["identities"]
    assert [found["provider"] for found in linked] == ["google"]
    assert _sign_in(client)["user"] == local["user"]  # by its identity from now on


@pytest.mark.parametrize("verified", [False, None, "true", 1])
def test_an_email_the_provider_did_not_verify_joins_no_account(client, provider, verified):
    if verified is not None:
        provider.people["code-linh"]["email_verified"] = verified
    local = _local_account(client, "linh@example.com")

    by_google = _sign_in(client)

    assert by_google["user"]["id"] != local["user"]["id"]
    assert client.get("/auth/identities", headers=_bearer(local)).json()["identities"] == []


def test_a_verified_email_of_an_account_with_that_provider_already_creates_a_user(
    client, provider, caplog
):
    for code in ("code-linh", "code-an"):
        provider.people[code]["email_verified"] = True
    local = _local_account(client, "linh@example.com")
    assert _sign_in(client)["user"] == local["user"]  # g-linh joins the local account
    before = _identities(client, local)

    caplog.clear()
    with caplog.at_level("INFO"):
        other = _sign_in(client, "code-an")  # g-an: the same verified email

    assert other["user"]["id"] != local["user"]["id"]
    assert other["user"]["display_name"] == "An"
    assert client.app.state.users.count() == 2
    assert _identities(client, local) == before == [("google", "linh@example.com")]
    who = client.get("/auth/identities", headers=_bearer(other)).json()["identities"]
    assert [found["provider"] for found in who] == ["google"]
    assert "already has an identity of this provider" in caplog.text
    assert "Identity joined the account of its email" not in caplog.text
    assert "linh@example.com" not in caplog.text


def test_a_verified_email_without_a_local_account_creates_a_user(client, provider):
    provider.people["code-linh"]["email_verified"] = True
    _local_account(client, "someone-else@example.com")

    by_google = _sign_in(client)

    assert by_google["user"]["display_name"] == "Khánh Linh"
    assert client.app.state.users.count() == 2


def test_a_facebook_email_joins_no_account(client, provider):
    provider.people["code-fb"]["email"] = "linh@example.com"
    local = _local_account(client, "linh@example.com")

    by_facebook = _sign_in(client, "code-fb", "facebook")

    assert by_facebook["user"]["id"] != local["user"]["id"]
