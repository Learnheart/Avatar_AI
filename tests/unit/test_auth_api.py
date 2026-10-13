"""The /auth routes, and a session token on the routes of the memory and the persona."""
import json
import logging
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.config import AppConfig
from backend.main import create_app
from backend.utils.database import DatabaseError
from tests.unit.accounts_fakes import PASSWORD, capture_mail, create_account
from tests.unit.fakes import build_orchestrator

EMAIL = "linh@example.com"
LINH = {"email": EMAIL, "password": PASSWORD, "display_name": "Khánh Linh"}


@pytest.fixture
def client(config: AppConfig) -> Iterator[TestClient]:
    # AUTH_DEV_USER_ID is empty: the session token decides who the user is.
    with TestClient(create_app(config, build_orchestrator(config))) as test_client:
        yield test_client


def _bearer(session: dict) -> dict:
    return {"Authorization": f"Bearer {session['token']}"}


def _register(client, **fields) -> dict:
    account = {**LINH, **fields}
    return create_account(client, account["email"], display_name=account["display_name"])


# ── Creating an account, signing in and out ───────────────────────────────

def test_registering_signs_the_person_in(client):
    session = _register(client)

    assert set(session) == {"token", "user", "expires_at"}
    assert session["user"]["display_name"] == "Khánh Linh"
    assert session["user"]["id"] != EMAIL
    assert session["expires_at"].endswith("+00:00")
    assert client.get("/auth/session", headers=_bearer(session)).json()["user"] == session["user"]


def test_signing_in_opens_another_session_of_the_same_user(client):
    first = _register(client)

    response = client.post("/auth/login", json={"email": "LINH@Example.com", "password": PASSWORD})

    second = response.json()
    assert response.status_code == 200
    assert second["user"] == first["user"]
    assert second["token"] != first["token"]


def test_a_wrong_password_and_an_unknown_email_answer_the_same_401(client):
    _register(client)

    wrong = client.post("/auth/login", json={"email": EMAIL, "password": "not it at all"})
    unknown = client.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_too_many_failed_sign_ins_answer_429(client, config):
    config.AUTH_LOGIN_MAX_ATTEMPTS = 2
    _register(client)
    for _ in range(2):
        client.post("/auth/login", json={"email": EMAIL, "password": "a wrong guess"})

    held = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})

    assert held.status_code == 429


def test_signing_out_ends_that_session_only(client):
    first = _register(client)
    second = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).json()

    assert client.post("/auth/logout", headers=_bearer(first)).status_code == 204

    assert client.get("/auth/session", headers=_bearer(first)).json()["user"] is None
    assert client.get("/auth/session", headers=_bearer(second)).json()["user"] == second["user"]


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic x"}])
def test_signing_out_without_a_session_is_not_an_error(client, headers):
    assert client.post("/auth/logout", headers=headers).status_code == 204


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "nope"}])
def test_a_guest_asking_who_they_are_is_told_nobody(client, headers):
    response = client.get("/auth/session", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"user": None, "expires_at": None}


def test_an_expired_session_is_a_guest(client, config):
    session = _register(client)

    config.AUTH_SESSION_MAX_HOURS = 1e-9

    assert client.get("/auth/session", headers=_bearer(session)).json()["user"] is None
    assert client.get("/memory/items", headers=_bearer(session)).status_code == 401


def test_the_end_of_the_session_given_to_the_page_follows_the_config(client, config):
    config.AUTH_SESSION_IDLE_MINUTES, config.AUTH_SESSION_MAX_HOURS = 600, 1
    session = _register(client)

    told = client.get("/auth/session", headers=_bearer(session)).json()

    # The age (1 h) ends it before the idle time (10 h) does.
    assert told["expires_at"] == session["expires_at"]


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"email": "not an email", "password": PASSWORD}, 422),
        ({"email": EMAIL, "password": "short"}, 422),
        ({"email": EMAIL}, 422),
        ({"username": "linh", "password": PASSWORD}, 422),
        ({"email": EMAIL, "password": PASSWORD, "admin": True}, 422),
    ],
)
def test_an_account_that_cannot_be_created_answers_422(client, body, status):
    mailer = capture_mail(client)

    assert client.post("/auth/register", json=body).status_code == status
    assert client.app.state.users.count() == 0 and mailer.sent == []


def test_an_email_that_has_an_account_answers_409_and_gets_no_code(client):
    _register(client)
    mailer = capture_mail(client)

    assert client.post("/auth/register", json=LINH).status_code == 409
    assert len(mailer.sent) == 1


def test_registration_switched_off_answers_403_and_is_not_offered(client, config):
    config.AUTH_ALLOW_REGISTRATION = 0

    assert client.post("/auth/register", json=LINH).status_code == 403
    assert client.get("/auth/providers").json() == {"providers": [], "registration": False}


def test_the_form_is_told_what_it_may_offer(client):
    assert client.get("/auth/providers").json() == {"providers": [], "registration": True}


def test_the_configured_user_needs_no_token(client, config):
    config.AUTH_DEV_USER_ID = "dev"

    told = client.get("/auth/session").json()

    assert told == {"user": {"id": "dev", "display_name": "dev"}, "expires_at": None}
    assert client.get("/memory/items").status_code == 200


# ── The token on the other routes ─────────────────────────────────────────

def test_a_session_reaches_the_memory_and_the_persona_of_its_user(client):
    session = _register(client)
    headers = _bearer(session)

    added = client.post("/memory/items", headers=headers,
                        json={"category": "identity", "text": "Tôi tên là Linh."})
    saved = client.put("/persona", headers=headers, json={"fields": {"name": "Mai"}})

    assert (added.status_code, saved.status_code) == (201, 200)
    assert client.get("/memory/items", headers=headers).json() == {"items": [added.json()]}
    assert client.get("/persona", headers=headers).json() == {"fields": {"name": "Mai"}}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}])
def test_without_a_valid_session_the_per_user_routes_answer_401(client, headers):
    _register(client)

    responses = [
        client.get("/memory/items", headers=headers),
        client.get("/memory/imports", headers=headers),
        client.get("/persona", headers=headers),
        client.get("/auth/identities", headers=headers),
    ]

    assert [response.status_code for response in responses] == [401] * 4


def test_one_user_s_session_never_reaches_another_s_data(client):
    linh = _bearer(_register(client))
    an = _bearer(_register(client, email="an@example.com", display_name="An"))
    client.post("/memory/items", headers=linh, json={"category": "identity", "text": "Tôi là Linh."})
    client.put("/persona", headers=linh, json={"fields": {"name": "Mai"}})

    assert client.get("/memory/items", headers=an).json() == {"items": []}
    assert client.get("/persona", headers=an).json() == {"fields": {}}


def test_a_signed_out_session_no_longer_reaches_the_data(client):
    headers = _bearer(_register(client))

    client.post("/auth/logout", headers=headers)

    assert client.get("/memory/items", headers=headers).status_code == 401


# ── Secrets and failures ──────────────────────────────────────────────────

def test_no_log_line_holds_a_password_or_a_token(client, caplog):
    with caplog.at_level(logging.DEBUG):
        session = _register(client)
        client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        client.post("/auth/login", json={"email": EMAIL, "password": "a wrong guess"})
        client.get("/memory/items", headers=_bearer(session))
        client.post("/auth/logout", headers=_bearer(session))

    logged = "\n".join(json.dumps(record.msg, ensure_ascii=False, default=str) for record in caplog.records)
    assert logged
    assert PASSWORD not in logged and "a wrong guess" not in logged
    assert session["token"] not in logged
    assert capture_mail(client).code() not in logged


def test_neither_the_password_nor_the_token_is_in_the_database(client, config):
    from sqlalchemy import create_engine, text

    session = _register(client)

    engine = create_engine(config.DATABASE_URL)
    with engine.connect() as connection:
        stored = [
            str(row)
            for table in ("users", "credentials", "sessions")
            for row in connection.execute(text(f"SELECT * FROM {table}"))
        ]
    engine.dispose()
    assert len(stored) == 3
    assert all(PASSWORD not in row and session["token"] not in row for row in stored)


def test_a_failing_database_answers_502_without_its_text(client):
    session = _register(client)

    def down(*args, **kwargs):
        raise DatabaseError("secret database detail")

    store = client.app.state.sessions._store
    store.get_session = store.add_session = store.revoke_session = down
    client.app.state.accounts._store.get_credentials = down

    responses = [
        client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}),
        client.post("/auth/logout", headers=_bearer(session)),
        client.get("/auth/session", headers=_bearer(session)),
        client.get("/auth/identities", headers=_bearer(session)),
        client.get("/memory/items", headers=_bearer(session)),
    ]

    assert [response.status_code for response in responses] == [502] * 5
    assert all("secret" not in response.text for response in responses)


# ── The code sent to the email ────────────────────────────────────────────

def test_asking_for_an_account_sends_a_code_and_signs_nobody_in(client):
    mailer = capture_mail(client)

    response = client.post("/auth/register", json=LINH)

    assert response.status_code == 202
    assert response.json() == {"email": EMAIL, "expires_in_sec": 600, "resend_in_sec": 60}
    assert [mail[0] for mail in mailer.sent] == [EMAIL]
    assert mailer.code() not in response.text
    assert client.app.state.users.count() == 0
    assert client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 401


def test_the_code_creates_the_account_and_signs_the_person_in(client):
    mailer = capture_mail(client)
    client.post("/auth/register", json=LINH)

    response = client.post("/auth/register/verify", json={"email": EMAIL, "code": mailer.code()})

    assert response.status_code == 201
    assert set(response.json()) == {"token", "user", "expires_at"}
    assert client.get("/memory/items", headers=_bearer(response.json())).status_code == 200


def test_a_wrong_code_answers_401_then_410_once_it_was_tried_too_often(client, config):
    config.AUTH_OTP_MAX_ATTEMPTS = 2
    mailer = capture_mail(client)
    client.post("/auth/register", json=LINH)
    code = mailer.code()
    wrong = "000000" if code != "000000" else "111111"

    tries = [
        client.post("/auth/register/verify", json={"email": EMAIL, "code": wrong}).status_code
        for _ in range(3)
    ]
    late = client.post("/auth/register/verify", json={"email": EMAIL, "code": code})

    assert tries == [401, 401, 410]
    assert late.status_code == 410 and client.app.state.users.count() == 0


def test_a_code_for_an_email_that_asked_for_none_answers_410(client):
    response = client.post("/auth/register/verify", json={"email": EMAIL, "code": "123456"})

    assert response.status_code == 410


def test_asking_again_too_soon_answers_429_with_the_wait(client):
    mailer = capture_mail(client)
    client.post("/auth/register", json=LINH)

    again = client.post("/auth/register", json=LINH)

    assert again.status_code == 429
    assert 1 <= int(again.headers["retry-after"]) <= 60
    assert len(mailer.sent) == 1


def test_a_code_that_cannot_be_sent_answers_502_and_can_be_asked_again(client):
    mailer = capture_mail(client)
    mailer.down = True

    failed = client.post("/auth/register", json=LINH)
    mailer.down = False

    assert failed.status_code == 502 and "SMTP" not in failed.text
    assert client.post("/auth/register", json=LINH).status_code == 202


def test_by_default_the_code_is_written_to_the_log_and_the_start_says_so(config, caplog):
    with caplog.at_level(logging.WARNING):
        with TestClient(create_app(config, build_orchestrator(config))) as client:
            client.post("/auth/register", json=LINH)

    written = [record.msg for record in caplog.records if record.name.endswith("mailer")]
    assert [mail["to"] for mail in written] == [EMAIL]
    assert any("MAIL_BACKEND=log" in str(record.msg) for record in caplog.records if record.name.endswith("main"))
