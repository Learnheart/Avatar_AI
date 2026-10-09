import json
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.config import AppConfig
from backend.main import create_app
from tests.unit.fakes import build_orchestrator


@pytest.fixture
def client(config: AppConfig) -> Iterator[TestClient]:
    app = create_app(config, build_orchestrator(config))
    with TestClient(app) as test_client:
        yield test_client


def _receive_turn_message(ws) -> dict:
    """Next message that is not part of the tracking stream."""
    while True:
        message = ws.receive_json()
        if message["type"] != "tracking_update":
            return message


def _receive_handshake(ws) -> None:
    assert ws.receive_json()["type"] == "connected"
    assert _receive_turn_message(ws) == {"type": "state", "state": "idle"}


def test_connected_then_the_current_state_are_sent_on_open(client, config):
    with client.websocket_connect("/ws") as ws:
        message = ws.receive_json()
        state = _receive_turn_message(ws)

    assert state == {"type": "state", "state": "idle"}
    assert message["type"] == "connected"
    assert message["session_id"]
    assert message["config"] == {
        "tracking_fps": config.TRACKING_FPS,
        "audio_format": "pcm_16khz_mono",
    }


def test_unknown_type_gets_error_and_the_socket_stays_open(client):
    with client.websocket_connect("/ws") as ws:
        _receive_handshake(ws)
        ws.send_text(json.dumps({"type": "teleport"}))
        error = _receive_turn_message(ws)

        assert (error["type"], error["code"]) == ("error", "unknown_message")
        assert error["message"]

        ws.send_text(json.dumps({"type": "playback_ended"}))  # known type: no reply
        ws.send_text(json.dumps({"type": "still_unknown"}))
        assert _receive_turn_message(ws)["code"] == "unknown_message"


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        "[1, 2, 3]",
        json.dumps({"type": "audio_chunk", "audio_data": "%%% not base64 %%%"}),
        json.dumps({"type": "audio_chunk", "audio_data": 42}),
    ],
)
def test_malformed_message_gets_error_and_the_socket_stays_open(client, raw):
    with client.websocket_connect("/ws") as ws:
        _receive_handshake(ws)
        ws.send_text(raw)
        error = _receive_turn_message(ws)

        assert (error["type"], error["code"]) == ("error", "bad_message")

        ws.send_text(json.dumps({"type": "nope"}))
        assert _receive_turn_message(ws)["code"] == "unknown_message"


def test_health_responds(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["avatar_state"] == "idle"
    assert body["camera_connected"] is False
    assert body["ws_connected"] is False
    assert body["uptime_seconds"] >= 0


def test_health_reports_an_open_socket(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()

        assert client.get("/health").json()["ws_connected"] is True


def test_shutdown_stops_the_orchestrator(config):
    orchestrator = build_orchestrator(config)

    with TestClient(create_app(config, orchestrator)):
        assert orchestrator._engine.loaded

    assert not orchestrator._engine.loaded


def test_startup_fails_without_groq_api_key():
    app = create_app(AppConfig(), build_orchestrator(AppConfig()))

    with pytest.raises(ValueError, match="groq_api_key"):
        with TestClient(app):
            pass
