import hashlib
import json
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.config import AppConfig
from backend.core.contracts import LLMError
from backend.features.memory.memory import MemoryService
from backend.main import create_app
from backend.utils.database import DatabaseError
from tests.unit.fakes import build_orchestrator
from tests.unit.knowledge_files import docx_bytes
from tests.unit.memory_fakes import CompletingLLM

URL = "/memory/items"
IMPORTS = "/memory/imports"
NAME = {"category": "identity", "text": "Tên là Linh."}
ITEM_KEYS = {
    "id", "category", "text", "status", "source", "import_id", "replaces_id", "evidence",
    "created_at", "updated_at",
}


INTRODUCTION = ("Mình là Linh, kỹ sư phần mềm.", "Mình thích uống trà hơn cà phê.")
TEXT = "\n".join(INTRODUCTION)
ANSWER = [
    {"category": "identity", "text": "Tôi tên là Linh.", "evidence": INTRODUCTION[0]},
    {"category": "preference", "text": "Tôi thích trà hơn cà phê.", "evidence": INTRODUCTION[1]},
]
IMPORT_KEYS = {"id", "name", "size_bytes", "item_count", "created_at"}


@pytest.fixture
def llm() -> CompletingLLM:
    """The LLM that reads the imports, scripted."""
    return CompletingLLM(ANSWER)


@pytest.fixture
def client(config: AppConfig, llm: CompletingLLM, monkeypatch) -> Iterator[TestClient]:
    # No memory passed: the app builds the real one on the test's database and object
    # store (tmp directory), and reads the imports with the scripted LLM.
    monkeypatch.setattr(main, "create_llm", lambda config: llm)
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
            client.get(IMPORTS),
            client.post(IMPORTS, data={"text": TEXT}),
            client.post(IMPORTS, files={"file": ("me.txt", TEXT.encode())}),
            client.post(IMPORTS),
            client.get(f"{IMPORTS}/some-id/content"),
            client.delete(f"{IMPORTS}/some-id"),
            client.delete(f"{IMPORTS}/some-id?items=delete"),
        ]
        users = client.app.state.users.count()

    assert [response.status_code for response in responses] == [401] * 12
    assert users == 0
    assert not Path(config.OBJECT_STORE_DIR).exists()


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


def test_a_store_failure_on_the_import_routes_answers_502_without_the_database_text(client):
    client.app.state.imports._store = DownStore()

    responses = [
        client.get(IMPORTS),
        client.post(IMPORTS, data={"text": TEXT}),
        client.get(f"{IMPORTS}/some-id/content"),
        client.delete(f"{IMPORTS}/some-id"),
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


# ── Imports ───────────────────────────────────────────────────────────────

def _upload(client: TestClient, name: str, data: bytes):
    return client.post(IMPORTS, files={"file": (name, data)})


def _paste(client: TestClient, text: str):
    return client.post(IMPORTS, data={"text": text})


def _nothing_is_kept(client: TestClient, config: AppConfig) -> bool:
    objects = [path for path in Path(config.OBJECT_STORE_DIR).rglob("*") if path.is_file()]
    return (
        objects == []
        and client.get(IMPORTS).json() == {"imports": []}
        and client.get(URL).json() == {"items": []}
    )


def test_the_list_of_imports_starts_empty(client):
    response = client.get(IMPORTS)

    assert (response.status_code, response.json()) == (200, {"imports": []})


def test_an_uploaded_file_is_returned_with_its_items_and_listed(client):
    data = docx_bytes(*INTRODUCTION)

    response = _upload(client, "giới thiệu.docx", data)

    body = response.json()
    assert response.status_code == 201
    assert set(body) == {"import", "items"}
    assert set(body["import"]) == IMPORT_KEYS  # neither the object key nor the user
    assert (body["import"]["name"], body["import"]["size_bytes"]) == ("giới thiệu.docx", len(data))
    assert body["import"]["item_count"] == 2
    assert body["import"]["id"] and body["import"]["created_at"].endswith("+00:00")
    assert [set(item) for item in body["items"]] == [ITEM_KEYS] * 2
    assert [(item["category"], item["text"], item["evidence"]) for item in body["items"]] == [
        (entry["category"], entry["text"], entry["evidence"]) for entry in ANSWER
    ]
    assert {(item["status"], item["source"], item["import_id"]) for item in body["items"]} == {
        ("active", "import", body["import"]["id"])
    }
    assert client.get(IMPORTS).json() == {"imports": [body["import"]]}
    assert client.get(URL).json() == {"items": body["items"]}


def test_a_pasted_text_is_an_import_too(client, config, llm):
    response = _paste(client, TEXT)

    body = response.json()
    assert response.status_code == 201
    assert body["import"]["name"] == config.MEMORY_IMPORT_TEXT_NAME
    assert body["import"]["size_bytes"] == len(TEXT.encode("utf-8"))
    assert len(body["items"]) == body["import"]["item_count"] == 2
    assert llm.requests[0][1] == config.MEMORY_IMPORT_DOCUMENT.format(text=TEXT)
    assert client.get(IMPORTS).json() == {"imports": [body["import"]]}


def test_the_content_is_the_original_bytes_as_a_download(client):
    data = docx_bytes(*INTRODUCTION)
    import_id = _upload(client, "giới thiệu.docx", data).json()["import"]["id"]

    response = client.get(f"{IMPORTS}/{import_id}/content")

    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == hashlib.sha256(data).hexdigest()
    assert response.headers["content-type"].endswith("wordprocessingml.document")
    assert response.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''gi%E1%BB%9Bi%20thi%E1%BB%87u.docx"
    )
    assert response.headers["x-content-type-options"] == "nosniff"


def test_the_content_of_a_pasted_text_is_plain_text_never_a_page(client):
    page = "<script>alert(1)</script> Mình là Linh."
    import_id = _paste(client, page).json()["import"]["id"]

    response = client.get(f"{IMPORTS}/{import_id}/content")

    assert response.content == page.encode("utf-8")
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_a_file_name_cannot_write_a_header_or_a_path(client):
    response = _upload(client, "../a\r\nSet-Cookie: x=1\".txt", TEXT.encode())

    import_id = response.json()["import"]["id"]
    content = client.get(f"{IMPORTS}/{import_id}/content")
    assert "/" not in response.json()["import"]["name"]
    assert "set-cookie" not in content.headers
    assert "\n" not in content.headers["content-disposition"]


def test_deleting_an_import_keeps_its_items_by_default(client):
    body = _paste(client, TEXT).json()
    kept = [{**item, "import_id": None} for item in body["items"]]

    response = client.delete(f"{IMPORTS}/{body['import']['id']}")

    assert response.status_code == 204
    assert client.get(IMPORTS).json() == {"imports": []}
    assert client.get(URL).json() == {"items": kept}
    assert client.get(f"{IMPORTS}/{body['import']['id']}/content").status_code == 404
    assert client.delete(f"{IMPORTS}/{body['import']['id']}").status_code == 404


def test_deleting_an_import_with_items_keep_keeps_them(client):
    body = _paste(client, TEXT).json()

    assert client.delete(f"{IMPORTS}/{body['import']['id']}?items=keep").status_code == 204
    assert [item["id"] for item in client.get(URL).json()["items"]] == [
        item["id"] for item in body["items"]
    ]


def test_deleting_an_import_with_items_delete_removes_them(client, config):
    typed = client.post(URL, json=NAME).json()
    body = _paste(client, TEXT).json()

    response = client.delete(f"{IMPORTS}/{body['import']['id']}?items=delete")

    assert response.status_code == 204
    assert client.get(IMPORTS).json() == {"imports": []}
    assert client.get(URL).json() == {"items": [typed]}
    assert [p for p in Path(config.OBJECT_STORE_DIR).rglob("*") if p.is_file()] == []


@pytest.mark.parametrize("items", ["", "all", "DELETE", "true"])
def test_another_value_of_items_is_refused_and_deletes_nothing(client, items):
    body = _paste(client, TEXT).json()

    response = client.delete(f"{IMPORTS}/{body['import']['id']}?items={items}")

    assert response.status_code == 422
    assert client.get(IMPORTS).json() == {"imports": [body["import"]]}


def test_an_unknown_import_answers_404(client):
    assert client.get(f"{IMPORTS}/nope/content").status_code == 404
    assert client.delete(f"{IMPORTS}/nope").status_code == 404
    assert client.delete(f"{IMPORTS}/nope?items=delete").status_code == 404


def test_one_user_cannot_see_read_or_delete_another_s_import(client, config):
    body = _paste(client, TEXT).json()
    import_id = body["import"]["id"]

    config.AUTH_DEV_USER_ID = "an"  # another person at the same kiosk

    assert client.get(IMPORTS).json() == {"imports": []}
    assert client.get(f"{IMPORTS}/{import_id}/content").status_code == 404
    assert client.delete(f"{IMPORTS}/{import_id}").status_code == 404
    assert client.delete(f"{IMPORTS}/{import_id}?items=delete").status_code == 404

    config.AUTH_DEV_USER_ID = "linh"
    assert client.get(IMPORTS).json() == {"imports": [body["import"]]}
    assert client.get(f"{IMPORTS}/{import_id}/content").content == TEXT.encode("utf-8")
    assert client.get(URL).json() == {"items": body["items"]}


# ── Imports that are refused: nothing of them is kept ─────────────────────

def test_a_file_over_the_size_limit_answers_413(client, config, llm):
    config.MEMORY_IMPORT_MAX_FILE_MB = 0.001  # 1048 bytes

    response = _upload(client, "me.txt", b"a" * 1049)

    assert response.status_code == 413
    assert "0.001 MB" in response.json()["detail"]
    assert llm.requests == []
    assert _nothing_is_kept(client, config)
    assert _upload(client, "me.txt", b"a" * 1048).status_code == 201


def test_a_text_over_the_character_limit_answers_413(client, config, llm):
    config.MEMORY_IMPORT_MAX_CHARS = len(TEXT) - 1

    responses = [_paste(client, TEXT), _upload(client, "me.docx", docx_bytes(TEXT))]

    assert [response.status_code for response in responses] == [413, 413]
    assert f"{len(TEXT) - 1} characters" in responses[0].json()["detail"]
    assert llm.requests == []
    assert _nothing_is_kept(client, config)


@pytest.mark.parametrize("name", ["me.exe", "me.html", "me"])
def test_an_unsupported_type_answers_415(client, config, name):
    response = _upload(client, name, TEXT.encode())

    assert response.status_code == 415
    assert _nothing_is_kept(client, config)


@pytest.mark.parametrize(
    ("name", "data"),
    [("me.pdf", b"not a pdf"), ("me.docx", b"not a docx"), ("me.txt", b""), ("me.txt", b" \n ")],
)
def test_an_unreadable_or_empty_file_answers_422(client, config, llm, name, data):
    response = _upload(client, name, data)

    assert response.status_code == 422
    assert llm.requests == []
    assert _nothing_is_kept(client, config)


def test_an_empty_pasted_text_answers_422(client, config):
    assert _paste(client, "  \n ").status_code == 422
    assert _nothing_is_kept(client, config)


def test_a_file_and_a_text_together_or_neither_answers_422(client, config, llm):
    both = client.post(IMPORTS, data={"text": TEXT}, files={"file": ("me.txt", TEXT.encode())})
    neither = client.post(IMPORTS)
    as_json = client.post(IMPORTS, json={"text": TEXT})

    assert [both.status_code, neither.status_code, as_json.status_code] == [422, 422, 422]
    assert llm.requests == []
    assert _nothing_is_kept(client, config)


@pytest.mark.parametrize(
    "answer", [[], [{"category": "hobby", "text": "Tôi thích trà."}]]
)
def test_an_import_with_nothing_to_remember_answers_422(client, config, llm, answer):
    llm.answer = json.dumps(answer)

    response = _paste(client, TEXT)

    assert response.status_code == 422
    assert "Nothing to remember" in response.json()["detail"]
    assert _nothing_is_kept(client, config)


def test_an_llm_failure_answers_502_without_the_provider_text(client, config, llm):
    llm.error = LLMError("401 secret-provider-detail")

    response = _paste(client, TEXT)

    assert response.status_code == 502
    assert "secret" not in response.text
    assert _nothing_is_kept(client, config)


@pytest.mark.parametrize(
    "answer", ["Đây là kết quả: []", "```json\n[]\n```", "{\"items\": []}", ""]
)
def test_an_answer_that_is_not_the_json_asked_for_answers_502(client, config, llm, answer):
    llm.answer = answer

    response = _paste(client, TEXT)

    assert response.status_code == 502
    assert answer not in response.text or answer == ""
    assert _nothing_is_kept(client, config)


def test_the_imported_items_are_edited_and_deleted_like_any_other(client):
    body = _paste(client, TEXT).json()
    first, second = body["items"]

    changed = client.patch(f"{URL}/{first['id']}", json={"text": "Tôi tên là Khánh Linh."})
    deleted = client.delete(f"{URL}/{second['id']}")

    assert (changed.status_code, deleted.status_code) == (200, 204)
    assert changed.json()["import_id"] == body["import"]["id"]
    assert client.get(IMPORTS).json()["imports"][0]["item_count"] == 1
