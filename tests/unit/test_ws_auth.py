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


def _auth(ws, token, frame_id: str = "frame-1") -> dict:
    ws.send_text(json.dumps({"type": "auth", "token": token, "id": frame_id}))
    return _next(ws, "session")


def _told(user, reply_to, status: str) -> dict:
    return {"type": "session", "user": user, "reply_to": reply_to, "status": status}


def _record_sessions(client) -> list[dict]:
    """Every `session` message the page is sent from now on, in order."""
    manager = client.app.state.orchestrator.ws_manager
    told: list[dict] = []
    send = manager.send

    async def recording(message: dict) -> None:
        if message.get("type") == "session":
            told.append(message)
        await send(message)

    manager.send = recording
    return told


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

    assert told == _told(session["user"], "frame-1", "signed_in")
    assert orch._engine.user_ids == [session["user"]["id"]]


@pytest.mark.parametrize(
    "token, status",
    [(None, "guest"), ("", "guest"), (7, "guest"), ("not-a-token", "rejected")],
)
def test_no_token_or_a_bad_one_is_a_guest_and_the_page_is_told_which(client, token, status):
    _register(client)
    orch = client.app.state.orchestrator

    with client.websocket_connect("/ws") as ws:
        told = _auth(ws, token)
        _turn(ws)

    assert told == _told(None, "frame-1", status)
    assert orch._engine.user_ids == [None]
    assert client.app.state.ws_token is None


def test_every_auth_is_answered_with_its_own_id(client):
    session = _register(client)

    with client.websocket_connect("/ws") as ws:
        answers = [
            _auth(ws, None, "a"),
            _auth(ws, session["token"], "b"),
            _auth(ws, "not-a-token", "c"),
            _auth(ws, None, "d"),
        ]

    assert [(told["reply_to"], told["status"]) for told in answers] == [
        ("a", "guest"), ("b", "signed_in"), ("c", "rejected"), ("d", "guest"),
    ]
    assert [told["user"] for told in answers] == [None, session["user"], None, None]


@pytest.mark.parametrize("frame_id", [None, 7, ["x"]])
def test_an_auth_without_an_id_is_answered_with_no_reply_to(client, frame_id):
    session = _register(client)
    frame = {"type": "auth", "token": session["token"]}
    if frame_id is not None:
        frame["id"] = frame_id

    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps(frame))

        assert _next(ws, "session") == _told(session["user"], None, "signed_in")


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

    assert told == _told(None, "frame-1", "ended")
    assert orch._engine.user_ids == [session["user"]["id"], None]
    assert client.app.state.ws_token is None


def test_a_session_signed_out_elsewhere_makes_the_connection_a_guest(client):
    session = _register(client)

    with client.websocket_connect("/ws") as ws:
        _auth(ws, session["token"])
        client.post("/auth/logout", headers={"Authorization": f"Bearer {session['token']}"})

        assert _next(ws, "session") == _told(None, "frame-1", "ended")


def test_a_session_that_ends_is_told_with_the_frame_that_opened_it(client):
    linh, an = _register(client), _register(client, "an@example.com")

    with client.websocket_connect("/ws") as ws:
        _auth(ws, linh["token"], "first")
        _auth(ws, an["token"], "second")
        client.post("/auth/logout", headers={"Authorization": f"Bearer {linh['token']}"})
        time.sleep(0.2)  # several looks of the watch: the session of Linh is not the connection's
        client.post("/auth/logout", headers={"Authorization": f"Bearer {an['token']}"})

        assert _next(ws, "session") == _told(None, "second", "ended")


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


def test_a_failing_database_while_resolving_is_unresolved_and_the_connection_stays(client):
    session = _register(client)
    orch = client.app.state.orchestrator

    def down(*args):
        raise DatabaseError("database is gone")

    client.app.state.sessions._store.get_session = down

    with client.websocket_connect("/ws") as ws:
        told = _auth(ws, session["token"])
        _turn(ws)

    # Not `rejected`: the page keeps a token that may be good and presents it again.
    assert told == _told(None, "frame-1", "unresolved")
    assert orch._engine.user_ids == [None]


def test_the_configured_user_is_the_user_whatever_the_page_says(client, config):
    config.AUTH_DEV_USER_ID = "dev"
    orch = client.app.state.orchestrator

    with client.websocket_connect("/ws") as ws:
        told = _auth(ws, None)
        _turn(ws)

    assert told == _told({"id": "dev", "display_name": "dev"}, "frame-1", "signed_in")
    assert orch._engine.user_ids == ["dev"]
    assert client.app.state.ws_token is None  # no session to watch or to end


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

    assert told == _told(None, "frame-1", "ended")
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


# ── Two changes close together ────────────────────────────────────────────

def _agree(client, told: list[dict], session: dict) -> None:
    """The page, the orchestrator and the kept token name the same signed-in user."""
    assert told[-1]["user"] == session["user"] and told[-1]["status"] == "signed_in"
    assert client.app.state.orchestrator._user_id == session["user"]["id"]
    assert client.app.state.ws_token == session["token"]
    assert client.app.state.ws_user_id == session["user"]["id"]


def test_a_sign_in_and_the_end_of_a_session_close_together_leave_one_answer(client):
    linh, an = _register(client), _register(client, "an@example.com")
    sessions = client.app.state.sessions
    resolve = sessions.resolve

    def slow(token):
        # A slow lookup: the end of Linh's session falls in the middle of An's sign-in.
        time.sleep(0.15 if token else 0.3)
        return resolve(token)

    with client.websocket_connect("/ws") as ws:
        _auth(ws, linh["token"], "linh")
        told = _record_sessions(client)
        sessions.resolve = slow
        client.post("/auth/logout", headers={"Authorization": f"Bearer {linh['token']}"})
        ws.send_text(json.dumps({"type": "auth", "token": an["token"], "id": "an"}))
        _wait(lambda: any(message["reply_to"] == "an" for message in told))
        time.sleep(0.6)  # whatever was still running has ended

        _agree(client, told, an)
        assert told[-1]["reply_to"] == "an"


def test_the_end_of_a_session_the_connection_no_longer_holds_changes_nothing(client, config):
    config.AUTH_LOGOUT_ON_LEAVE = 1
    linh, an = _register(client), _register(client, "an@example.com")
    sessions = client.app.state.sessions
    orch = client.app.state.orchestrator
    revoke = sessions.revoke

    def slow(token):
        revoke(token)
        time.sleep(0.3)  # the page signs in as An meanwhile

    with client.websocket_connect("/ws") as ws:
        _auth(ws, linh["token"], "linh")
        # Only the closed conversation ends the session here, not the watch.
        config.AUTH_SESSION_CHECK_SEC = 60
        time.sleep(0.15)
        sessions.revoke = slow
        leaving = client.portal.start_task_soon(
            orch.on_conversation_closed, linh["user"]["id"], "idle"
        )
        time.sleep(0.1)
        assert _auth(ws, an["token"], "an")["status"] == "signed_in"
        told = _record_sessions(client)
        leaving.result(timeout=2)
        time.sleep(0.2)

        assert told == []
        assert orch._user_id == an["user"]["id"]
        assert client.app.state.ws_token == an["token"]


def test_the_closed_conversation_of_somebody_else_signs_the_connection_s_user_out_of_nothing(
    client, config
):
    config.AUTH_LOGOUT_ON_LEAVE = 1
    linh, an = _register(client), _register(client, "an@example.com")
    orch = client.app.state.orchestrator

    with client.websocket_connect("/ws") as ws:
        _auth(ws, an["token"], "an")
        told = _record_sessions(client)
        # Reported late: the conversation Linh left, once the connection is An's.
        client.portal.call(orch.on_conversation_closed, linh["user"]["id"], "idle")

        assert told == []
        assert client.app.state.ws_token == an["token"]
    assert client.get(
        "/auth/session", headers={"Authorization": f"Bearer {an['token']}"}
    ).json()["user"] == an["user"]


# ── Housekeeping ──────────────────────────────────────────────────────────

def test_ended_sessions_are_deleted_while_the_backend_runs_whatever_the_database_does(config):
    from backend.features.accounts.sessions import session_id

    config.AUTH_SESSION_CLEANUP_MINUTES = 0.05 / 60
    with TestClient(create_app(config, build_orchestrator(config))) as client:
        store = client.app.state.sessions._store
        delete, rounds = store.delete_ended_sessions, []

        def failing_first(*args):
            rounds.append(args)
            if len(rounds) <= 2:
                raise DatabaseError("database is gone")
            return delete(*args)

        store.delete_ended_sessions = failing_first
        ended, kept = _register(client), _register(client, "an@example.com")
        client.post("/auth/logout", headers={"Authorization": f"Bearer {ended['token']}"})
        assert store.get_session(session_id(ended["token"])) is not None

        _wait(lambda: store.get_session(session_id(ended["token"])) is None)

        assert len(rounds) >= 3  # the rounds that failed did not stop the next ones
        assert store.get_session(session_id(kept["token"])) is not None
