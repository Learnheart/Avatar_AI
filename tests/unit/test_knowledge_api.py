from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.config import AppConfig
from backend.core.contracts import Embedder, EmbeddingError
from backend.features.knowledge.knowledge_base import KnowledgeBase
from backend.features.knowledge.store import SqliteVecStore
from backend.main import create_app
from tests.unit.fakes import build_orchestrator
from tests.unit.knowledge_files import docx_bytes

LEAVE = "Mỗi nhân viên được nghỉ phép năm mười hai ngày.".encode("utf-8")
URL = "/knowledge/documents"


@pytest.fixture
def client(config: AppConfig) -> Iterator[TestClient]:
    # No knowledge base passed: the app builds the configured one (local_hash, tmp file).
    with TestClient(create_app(config, build_orchestrator(config))) as test_client:
        yield test_client


def _upload(client: TestClient, name: str, data: bytes):
    return client.post(URL, files={"file": (name, data)})


def test_an_uploaded_document_is_returned_and_listed(client):
    response = _upload(client, "leave.txt", LEAVE)

    document = response.json()
    assert response.status_code == 201
    assert (document["name"], document["chunk_count"], document["size_bytes"]) == (
        "leave.txt", 1, len(LEAVE)
    )
    assert document["id"] and document["created_at"]
    assert document["enabled"] is True
    assert client.get(URL).json() == {"documents": [document]}


def test_a_docx_upload_is_indexed(client):
    response = _upload(client, "policy.docx", docx_bytes("Quy định nghỉ phép năm."))

    assert response.status_code == 201
    assert response.json()["chunk_count"] == 1


def test_the_list_starts_empty(client):
    assert client.get(URL).json() == {"documents": []}


def test_a_deleted_document_leaves_the_list(client):
    document_id = _upload(client, "leave.txt", LEAVE).json()["id"]

    assert client.delete(f"{URL}/{document_id}").status_code == 204
    assert client.get(URL).json() == {"documents": []}
    assert client.delete(f"{URL}/{document_id}").status_code == 404


def test_a_file_over_the_size_limit_is_refused(client, config):
    config.KNOWLEDGE_MAX_FILE_MB = 0.001  # about 1 KB

    response = _upload(client, "big.txt", b"a" * 2000)

    assert response.status_code == 413
    assert client.get(URL).json() == {"documents": []}


def test_an_unsupported_type_is_refused(client):
    response = _upload(client, "photo.png", b"\x89PNG")

    assert response.status_code == 415
    assert ".png" in response.json()["detail"]


@pytest.mark.parametrize(("name", "data"), [("blank.txt", b"  "), ("broken.pdf", b"nope")])
def test_a_file_without_readable_text_is_refused(client, name, data):
    assert _upload(client, name, data).status_code == 422


def test_a_request_without_a_file_is_refused(client):
    assert client.post(URL).status_code == 422


def test_an_embedding_failure_answers_502_without_the_provider_text(config):
    class DownEmbedder(Embedder):
        signature = "down"

        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise EmbeddingError("secret provider detail")

    knowledge = KnowledgeBase(config, DownEmbedder(), SqliteVecStore(config.KNOWLEDGE_DB_PATH))
    with TestClient(create_app(config, build_orchestrator(config), knowledge)) as client:
        response = _upload(client, "leave.txt", LEAVE)

    assert response.status_code == 502
    assert "secret" not in response.text


def test_the_page_origin_is_allowed_by_cors(client):
    response = client.get(URL, headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "*"


def test_only_the_configured_origins_are_allowed(config):
    config.CORS_ALLOW_ORIGINS = "http://kiosk.test:3000, http://localhost:3000"
    with TestClient(create_app(config, build_orchestrator(config))) as client:
        allowed = client.get(URL, headers={"Origin": "http://kiosk.test:3000"})
        other = client.get(URL, headers={"Origin": "http://evil.test"})

    assert allowed.headers["access-control-allow-origin"] == "http://kiosk.test:3000"
    assert "access-control-allow-origin" not in other.headers


# ── Choosing the documents in use ─────────────────────────────────────────

def test_a_document_is_unselected_then_selected_again(client):
    document = _upload(client, "leave.txt", LEAVE).json()

    unselected = client.patch(f"{URL}/{document['id']}", json={"enabled": False})

    assert unselected.status_code == 200
    assert unselected.json() == {**document, "enabled": False}
    assert client.get(URL).json() == {"documents": [{**document, "enabled": False}]}

    selected = client.patch(f"{URL}/{document['id']}", json={"enabled": True})
    assert selected.json() == document


def test_selecting_an_unknown_document_answers_404(client):
    assert client.patch(f"{URL}/nope", json={"enabled": False}).status_code == 404


@pytest.mark.parametrize("body", [{}, {"enabled": "maybe"}])
def test_a_selection_without_a_boolean_is_refused(client, body):
    document_id = _upload(client, "leave.txt", LEAVE).json()["id"]

    assert client.patch(f"{URL}/{document_id}", json=body).status_code == 422
