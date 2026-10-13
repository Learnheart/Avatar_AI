from typing import Iterator

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.config import AppConfig
from backend.main import create_app
from backend.utils.database import DatabaseError
from tests.unit.fakes import build_orchestrator
from tests.unit.memory_fakes import CompletingLLM

URL = "/persona"
MAI = {"name": "Mai", "addressing": "xưng em, gọi anh"}


@pytest.fixture
def llm() -> CompletingLLM:
    """The LLM that reads the descriptions, scripted."""
    return CompletingLLM(MAI)


@pytest.fixture
def client(config: AppConfig, llm: CompletingLLM, monkeypatch) -> Iterator[TestClient]:
    # The app builds the real persona on the test's database (tmp directory).
    monkeypatch.setattr(main, "create_llm", lambda config: llm)
    config.AUTH_DEV_USER_ID = "linh"
    with TestClient(create_app(config, build_orchestrator(config))) as test_client:
        yield test_client


def test_a_user_starts_without_persona(client):
    response = client.get(URL)

    assert response.status_code == 200
    assert response.json() == {"fields": {}}


def test_a_saved_persona_is_returned_and_read_back(client):
    response = client.put(URL, json={"fields": MAI})

    assert response.status_code == 200
    assert response.json() == {"fields": MAI}
    assert client.get(URL).json() == {"fields": MAI}


def test_saving_again_replaces_the_whole_persona(client):
    client.put(URL, json={"fields": MAI})

    response = client.put(URL, json={"fields": {"name": " Lan ", "role": ""}})

    assert response.json() == {"fields": {"name": "Lan"}}
    assert client.get(URL).json() == {"fields": {"name": "Lan"}}


def test_a_deleted_persona_is_gone(client):
    client.put(URL, json={"fields": MAI})

    assert client.delete(URL).status_code == 204
    assert client.get(URL).json() == {"fields": {}}
    assert client.delete(URL).status_code == 204


@pytest.mark.parametrize(
    "body",
    [
        {"fields": {"voice": "trầm"}},
        {"fields": {"name": "x" * 301}},
        {"fields": {"name": 7}},
        {"fields": ["name"]},
        {"fields": MAI, "user_id": "an"},
        {},
    ],
)
def test_a_persona_that_cannot_be_stored_answers_422(client, body):
    client.put(URL, json={"fields": MAI})

    response = client.put(URL, json=body)

    assert response.status_code == 422
    assert client.get(URL).json() == {"fields": MAI}


def test_a_saved_persona_is_the_prompt_of_the_next_turn(client):
    client.put(URL, json={"fields": MAI})

    prompt = client.portal.call(client.app.state.persona.prompt, "linh")

    assert "- Tên của trợ lý: Mai" in prompt and "- Cách xưng hô: xưng em, gọi anh" in prompt


# ── Identity ──────────────────────────────────────────────────────────────

def test_a_guest_gets_401_on_every_route(config):
    with TestClient(create_app(config, build_orchestrator(config))) as client:
        responses = [
            client.get(URL),
            client.put(URL, json={"fields": MAI}),
            client.put(URL, json={}),
            client.delete(URL),
        ]
        users = client.app.state.users.count()

    assert [response.status_code for response in responses] == [401] * 4
    assert users == 0


def test_one_user_cannot_see_or_change_another_s_persona(client, config):
    client.put(URL, json={"fields": MAI})

    config.AUTH_DEV_USER_ID = "an"  # another person at the same kiosk

    assert client.get(URL).json() == {"fields": {}}
    assert client.delete(URL).status_code == 204

    config.AUTH_DEV_USER_ID = "linh"
    assert client.get(URL).json() == {"fields": MAI}


# ── Failures ──────────────────────────────────────────────────────────────

class DownStore:
    """A store whose database is gone."""

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise DatabaseError("secret database detail")

        return fail


def test_a_store_failure_answers_502_without_the_database_text(client):
    client.app.state.persona._store = DownStore()

    responses = [client.get(URL), client.put(URL, json={"fields": MAI}), client.delete(URL)]

    assert [response.status_code for response in responses] == [502] * 3
    assert all("secret" not in response.text for response in responses)


def test_a_failing_user_lookup_answers_502(client):
    def down(*args, **kwargs):
        raise DatabaseError("secret database detail")

    client.app.state.users.touch = down

    response = client.get(URL)

    assert response.status_code == 502
    assert "secret" not in response.text
