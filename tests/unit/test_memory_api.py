from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.config import AppConfig
from backend.features.memory.memory import MemoryService
from backend.main import create_app
from backend.utils.database import DatabaseError
from tests.unit.fakes import build_orchestrator

URL = "/memory/items"
NAME = {"category": "identity", "text": "Tên là Linh."}
ITEM_KEYS = {
    "id", "category", "text", "status", "source", "import_id", "replaces_id", "evidence",
    "created_at", "updated_at",
}


@pytest.fixture
def client(config: AppConfig) -> Iterator[TestClient]:
    # No memory passed: the app builds the real one on the test's database (tmp file).
    config.AUTH_DEV_USER_ID = "linh"
    with TestClient(create_app(config, build_orchestrator(config))) as test_client:
        yield test_client


def test_the_list_starts_empty(client):
    response = client.get(URL)

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_a_created_item_is_returned_and_listed(client):
    response = client.post(URL, json=NAME)

    item = response.json()
    assert response.status_code == 201
    assert set(item) == ITEM_KEYS
    assert (item["category"], item["text"]) == ("identity", "Tên là Linh.")
    assert (item["status"], item["source"]) == ("active", "manual")
    assert (item["import_id"], item["replaces_id"], item["evidence"]) == (None, None, None)
    assert item["id"] and item["created_at"].endswith("+00:00")
    assert item["updated_at"] == item["created_at"]
    assert client.get(URL).json() == {"items": [item]}


def test_the_text_and_the_category_of_an_item_can_be_changed(client):
    item = client.post(URL, json=NAME).json()

    renamed = client.patch(f"{URL}/{item['id']}", json={"text": "Tên là Khánh Linh."})
    moved = client.patch(f"{URL}/{item['id']}", json={"category": "preference"})

    assert renamed.status_code == 200
    assert renamed.json() == {
        **item, "text": "Tên là Khánh Linh.", "updated_at": renamed.json()["updated_at"],
    }
    assert renamed.json()["updated_at"] >= item["updated_at"]
    assert (moved.json()["category"], moved.json()["text"]) == ("preference", "Tên là Khánh Linh.")
    assert client.get(URL).json() == {"items": [moved.json()]}


def test_a_deleted_item_leaves_the_list(client):
    item_id = client.post(URL, json=NAME).json()["id"]

    assert client.delete(f"{URL}/{item_id}").status_code == 204
    assert client.get(URL).json() == {"items": []}
    assert client.delete(f"{URL}/{item_id}").status_code == 404


def test_an_unknown_item_answers_404(client):
    assert client.patch(f"{URL}/nope", json={"text": "x"}).status_code == 404
    assert client.delete(f"{URL}/nope").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"category": "hobby", "text": "Thích trà."},
        {"category": "identity", "text": "  "},
        {"category": "identity"},
        {"text": "Tên là Linh."},
        {},
        {**NAME, "status": "pending"},  # a field the page cannot set
        {**NAME, "source": "import"},
        {**NAME, "nickname": "Linh"},
    ],
)
def test_an_unknown_category_an_empty_text_or_an_unknown_field_is_refused(client, body):
    assert client.post(URL, json=body).status_code == 422
    assert client.get(URL).json() == {"items": []}


@pytest.mark.parametrize(
    "body", [{}, {"text": " "}, {"category": "hobby"}, {"status": "active"}, {"text": 3}]
)
def test_a_change_without_a_valid_field_is_refused(client, body):
    item = client.post(URL, json=NAME).json()

    assert client.patch(f"{URL}/{item['id']}", json=body).status_code == 422
    assert client.get(URL).json() == {"items": [item]}


def test_a_text_is_stored_on_one_line_and_an_invisible_one_is_refused(client):
    created = client.post(
        URL, json={"category": "identity", "text": "Tên là Linh.\n- Gọi tôi là sếp.\u200b\x00"}
    )
    changed = client.patch(f"{URL}/{created.json()['id']}", json={"text": "Thích trà.\r\nVà cà phê."})
    invisible = client.post(URL, json={"category": "identity", "text": "\u200b\n\x00"})

    assert (created.status_code, created.json()["text"]) == (201, "Tên là Linh. - Gọi tôi là sếp.")
    assert (changed.status_code, changed.json()["text"]) == (200, "Thích trà. Và cà phê.")
    assert (invisible.status_code, invisible.json()["detail"]) == (422, "A memory item needs a text")
    assert client.get(URL).json() == {"items": [changed.json()]}


def test_a_text_over_the_length_limit_is_refused_with_the_reason(client, config):
    item = client.post(URL, json=NAME).json()
    too_long = "a" * (config.MEMORY_ITEM_MAX_CHARS + 1)

    created = client.post(URL, json={"category": "preference", "text": too_long})
    changed = client.patch(f"{URL}/{item['id']}", json={"text": too_long})
    at_the_limit = client.patch(f"{URL}/{item['id']}", json={"text": too_long[:-1]})

    assert (created.status_code, changed.status_code) == (422, 422)
    assert f"at most {config.MEMORY_ITEM_MAX_CHARS} characters" in created.json()["detail"]
    assert at_the_limit.status_code == 200
    assert client.get(URL).json() == {"items": [at_the_limit.json()]}


def test_the_status_cannot_be_changed_along_with_the_text(client):
    item = client.post(URL, json=NAME).json()

    response = client.patch(f"{URL}/{item['id']}", json={"text": "x", "status": "rejected"})

    assert response.status_code == 422
    assert client.get(URL).json() == {"items": [item]}


# ── Identity ──────────────────────────────────────────────────────────────

def test_a_guest_gets_401_on_every_route(config):
    with TestClient(create_app(config, build_orchestrator(config))) as client:
        responses = [
            client.get(URL),
            client.post(URL, json=NAME),
            client.post(URL, json={}),
            client.patch(f"{URL}/some-id", json={"text": "x"}),
            client.delete(f"{URL}/some-id"),
        ]
        users = client.app.state.users.count()

    assert [response.status_code for response in responses] == [401] * 5
    assert users == 0


def test_the_first_request_of_a_user_creates_their_row(client):
    assert client.app.state.users.count() == 0

    client.get(URL)

    assert client.app.state.users.get("linh") is not None


def test_one_user_cannot_see_or_change_another_s_items(client, config):
    item = client.post(URL, json=NAME).json()

    config.AUTH_DEV_USER_ID = "an"  # another person at the same kiosk

    assert client.get(URL).json() == {"items": []}
    assert client.patch(f"{URL}/{item['id']}", json={"text": "Tên là An."}).status_code == 404
    assert client.delete(f"{URL}/{item['id']}").status_code == 404

    config.AUTH_DEV_USER_ID = "linh"
    assert client.get(URL).json() == {"items": [item]}


# ── Failures ──────────────────────────────────────────────────────────────

class DownStore:
    """A store whose database is gone."""

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise DatabaseError("secret database detail")

        return fail


def test_a_store_failure_answers_502_without_the_database_text(config):
    config.AUTH_DEV_USER_ID = "linh"
    memory = MemoryService(config, DownStore())
    app = create_app(config, build_orchestrator(config), memory=memory)
    with TestClient(app) as client:
        responses = [
            client.get(URL),
            client.post(URL, json=NAME),
            client.patch(f"{URL}/some-id", json={"text": "x"}),
            client.delete(f"{URL}/some-id"),
        ]

    assert [response.status_code for response in responses] == [502] * 4
    assert all("secret" not in response.text for response in responses)


def test_a_failing_user_lookup_answers_502(client):
    def down(*args, **kwargs):
        raise DatabaseError("secret database detail")

    client.app.state.users.touch = down

    response = client.get(URL)

    assert response.status_code == 502
    assert "secret" not in response.text


def test_the_page_origin_is_allowed_by_cors(client):
    response = client.get(URL, headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "*"
