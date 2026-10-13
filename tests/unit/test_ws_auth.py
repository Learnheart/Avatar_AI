"""Signing in and out on the WebSocket: the `auth` and `session` messages, and how the
session of the connection ends."""
import json
import time
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.config import AppConfig
from backend.main import create_app
from backend.utils.database import DatabaseError
from tests.unit.accounts_fakes import create_account
from tests.unit.fakes import END, START, audio_message, build_orchestrator, chunk


@pytest.fixture
def client(config: AppConfig) -> Iterator[TestClient]:
    config.AUTH_SESSION_CHECK_SEC = 0.05
    with TestClient(create_app(config, build_orchestrator(config))) as test_client:
        yield test_client


def _register(client, email: str = "linh@example.com") -> dict:
    return create_account(client, email)


def _next(ws, *types: str) -> dict:
    """The next message of one of these types."""
    while True:
        message = ws.receive_json()
        if message["type"] in types:
            return message


def _auth(ws, token) -> dict:
    ws.send_text(json.dumps({"type": "auth", "token": token}))
    return _next(ws, "session")


def _turn(ws) -> None:
    """Say one utterance and let the avatar answer it to the end."""
    ws.send_text(audio_message(chunk(START)))
    ws.send_text(audio_message(chunk(END)))
    while True:
        message = _next(ws, "speech_output")
        if message["is_final"]:
            break
    ws.send_text(json.dumps({"type": "playback_ended"}))
    while _next(ws, "state")["state"] != "idle":
        pass


def _wait(condition, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "the condition never held"
        time.sleep(0.01)


def test_a_valid_token_makes_the_connection_its_user_s(client):
    session = _register(client)
    orch = client.app.state.orchestrator

    with client.websocket_connect("/ws") as ws:
        told = _auth(ws, session["token"])
        _turn(ws)

    assert told == {"type": "session", "user": session["user"]}
    assert orch._engine.user_ids == [session["user"]["id"]]


@pytest.mark.parametrize("token", [None, "not-a-token", "", 7])
def test_no_token_or_a_bad_one_is_a_guest_and_the_page_is_told(client, token):
    _register(client)
    orch = client.app.state.orchestrator

    with client.websocket_connect("/ws") as ws:
        told = _auth(ws, token)
        _turn(ws)

    assert told == {"type": "session", "user": None}
    assert orch._engine.user_ids == [None]


def test_a_connection_that_never_says_who_it_is_is_a_guest(client):
    orch = client.app.state.orchestrator

    with client.websocket_connect("/ws") as ws:
        _turn(ws)

    assert orch._engine.user_ids == [None]


def test_the_orchestrator_is_never_given_the_token(client, monkeypatch):
    session = _register(client)
    orch = client.app.state.orchestrator
    seen: list[str] = []
    handle = orch.handle_client_message

    async def recording(raw: str) -> None:
        seen.append(raw)
        await handle(raw)

    monkeypatch.setattr(orch, "handle_client_message", recording)

    with client.websocket_connect("/ws") as ws:
        _auth(ws, session["token"])
        ws.send_text(json.dumps({"type": "playback_ended"}))
        ws.send_text(json.dumps({"type": "teleport"}))
        _next(ws, "error")

    assert len(seen) == 2
    assert all(session["token"] not in raw for raw in seen)


def test_signing_in_and_out_on_an_open_connection_starts_a_new_conversation(client):
    session = _register(client)
    orch = client.app.state.orchestrator

    with client.websocket_connect("/ws") as ws:
        _auth(ws, None)
        _turn(ws)
        assert _auth(ws, session["token"])["user"] == session["user"]
        _turn(ws)
        client.post("/auth/logout", headers={"Authorization": f"Bearer {session['token']}"})
        assert _auth(ws, None)["user"] is None
        _turn(ws)

    assert orch._engine.user_ids == [None, session["user"]["id"], None]
    assert orch._engine.histories == [(), (), ()]


def test_a_reloaded_page_keeps_the_conversation_of_its_user(client):
    session = _register(client)
    orch = client.app.state.orchestrator

    with client.websocket_connect("/ws") as ws:
        _auth(ws, session["token"])
        _turn(ws)
    with client.websocket_connect("/ws") as ws:
        _auth(ws, session["token"])
        _turn(ws)

    assert orch._engine.user_ids == [session["user"]["id"]] * 2
    assert len(orch._engine.histories[1]) == 1  # the turn before the reload


def test_a_page_that_reconnects_without_its_token_is_a_guest_with_a_new_conversation(client):
    session = _register(client)
    orch = client.app.state.orchestrator

    with client.websocket_connect("/ws") as ws:
        _auth(ws, session["token"])
        _turn(ws)
    with client.websocket_connect("/ws") as ws:
        _turn(ws)  # another page: its first frame is not an `auth`

    assert orch._engine.user_ids == [session["user"]["id"], None]
    assert orch._engine.histories[1] == ()


def test_a_session_that_expires_while_connected_makes_the_connection_a_guest(client, config):
    session = _register(client)
    orch = client.app.state.orchestrator

    with client.websocket_connect("/ws") as ws:
        _auth(ws, session["token"])
        _turn(ws)
        config.AUTH_SESSION_MAX_HOURS = 1e-9
        told = _next(ws, "session")
        _turn(ws)

    assert told == {"type": "session", "user": None}
    assert orch._engine.user_ids == [session["user"]["id"], None]
    assert client.app.state.ws_token is None


def test_a_session_signed_out_elsewhere_makes_the_connection_a_guest(client):
    session = _register(client)

    with client.websocket_connect("/ws") as ws:
        _auth(ws, session["token"])
        client.post("/auth/logout", headers={"Authorization": f"Bearer {session['token']}"})

        assert _next(ws, "session") == {"type": "session", "user": None}


def test_a_turn_keeps_the_session_alive_and_looking_at_it_does_not(client):
    session = _register(client)
    store = client.app.state.sessions._store
    from backend.features.accounts.sessions import session_id

    with client.websocket_connect("/ws") as ws:
        _auth(ws, session["token"])
        seen = store.get_session(session_id(session["token"])).last_seen_at
        time.sleep(0.2)  # several looks of the watch
        assert store.get_session(session_id(session["token"])).last_seen_at == seen

        _turn(ws)
        _wait(lambda: store.get_session(session_id(session["token"])).last_seen_at > seen)


def test_a_failing_database_while_resolving_is_a_guest_and_the_connection_stays(client):
    session = _register(client)
    orch = client.app.state.orchestrator

    def down(*args):
        raise DatabaseError("database is gone")

    client.app.state.sessions._store.get_session = down

    with client.websocket_connect("/ws") as ws:
        told = _auth(ws, session["token"])
        _turn(ws)

    assert told == {"type": "session", "user": None}
    assert orch._engine.user_ids == [None]


def test_the_configured_user_is_the_user_whatever_the_page_says(client, config):
    config.AUTH_DEV_USER_ID = "dev"
    orch = client.app.state.orchestrator

    with client.websocket_connect("/ws") as ws:
        told = _auth(ws, None)
        _turn(ws)

    assert told == {"type": "session", "user": {"id": "dev", "display_name": "dev"}}
    assert orch._engine.user_ids == ["dev"]


# ── Leaving the kiosk ─────────────────────────────────────────────────────

def _leave(orch) -> None:
    """Let the conversation be closed for silence right away."""
    orch.config.CONVERSATION_IDLE_SEC = 0.01


def test_by_default_a_person_who_left_stays_signed_in(client):
    session = _register(client)
    orch = client.app.state.orchestrator

    with client.websocket_connect("/ws") as ws:
        _auth(ws, session["token"])
        _turn(ws)
        _leave(orch)
        assert _next(ws, "conversation") == {"type": "conversation", "conversation_id": None}
        orch.config.CONVERSATION_IDLE_SEC = 120
        _turn(ws)

    assert orch._engine.user_ids == [session["user"]["id"]] * 2


def test_with_logout_on_leave_a_person_who_left_is_signed_out(client, config):
    config.AUTH_LOGOUT_ON_LEAVE = 1
    session = _register(client)
    orch = client.app.state.orchestrator

    with client.websocket_connect("/ws") as ws:
        _auth(ws, session["token"])
        _turn(ws)
        _leave(orch)
        told = _next(ws, "session")
        orch.config.CONVERSATION_IDLE_SEC = 120
        _turn(ws)

    assert told == {"type": "session", "user": None}
    assert orch._engine.user_ids == [session["user"]["id"], None]
    assert client.get(
        "/auth/session", headers={"Authorization": f"Bearer {session['token']}"}
    ).json()["user"] is None


def test_with_logout_on_leave_a_new_conversation_asked_for_signs_nobody_out(client, config):
    config.AUTH_LOGOUT_ON_LEAVE = 1
    session = _register(client)
    orch = client.app.state.orchestrator

    with client.websocket_connect("/ws") as ws:
        _auth(ws, session["token"])
        _turn(ws)
        ws.send_text(json.dumps({"type": "conversation_reset"}))
        assert _next(ws, "conversation")["conversation_id"] is None
        _turn(ws)

    assert orch._engine.user_ids == [session["user"]["id"]] * 2
