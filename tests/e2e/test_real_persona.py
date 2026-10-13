"""
The persona of the avatar against the real backend, for a signed-in person: the real LLM
reads a description into the fields, and with those fields saved a real turn is answered
in that character. The backend runs on a database of its own.
"""
import time
from typing import AsyncIterator, Iterator

import httpx
import pytest

from tests.e2e.helpers import E2E_MARKS
from tests.e2e.test_real_turn import (
    END_TO_END_BUDGET_SEC,
    FIRST_AUDIO_BUDGET_SEC,
    Session,
    ask,
    connect,
    run_backend,
)

pytestmark = E2E_MARKS

NAME = "Bông"
DESCRIPTION = (
    f"Trợ lý của tôi tên là {NAME}. {NAME} xưng em và gọi tôi là anh. "
    f"{NAME} vui vẻ, trả lời thật ngắn gọn."
)


@pytest.fixture(scope="module")
def ws_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    data_dir = tmp_path_factory.mktemp("persona-data")
    yield from run_backend(tmp_path_factory, {
        "AUTH_DEV_USER_ID": "e2e-persona",
        "DATABASE_URL": f"sqlite:///{data_dir / 'app.db'}",
        "OBJECT_STORE_DIR": str(data_dir / "objects"),
    })


@pytest.fixture
async def session(ws_url: str) -> AsyncIterator[Session]:
    async for client in connect(ws_url):
        yield client


def _named(text: str) -> bool:
    return NAME.lower() in text.lower()


async def test_a_description_read_and_saved_shapes_the_answer(
    session: Session, ws_url: str, name_question_pcm: bytes
):
    persona_url = ws_url.replace("ws://", "http://").removesuffix("/ws") + "/persona"
    async with httpx.AsyncClient(timeout=60) as http:
        read_started = time.monotonic()
        read = await http.post(f"{persona_url}/read", json={"text": DESCRIPTION})
        read_sec = time.monotonic() - read_started
        assert read.status_code == 200, read.text
        fields = read.json()["fields"]
        # Read, not stored: the avatar is still the default character.
        assert (await http.get(persona_url)).json() == {"fields": {}}
        default_text, _, _ = await ask(session, name_question_pcm)

        # A new conversation: the default answer is not in the history of the next turn.
        await session.send({"type": "conversation_reset"})
        saved = await http.put(persona_url, json={"fields": fields})
        assert saved.status_code == 200, saved.text
        try:
            answer_text, first_audio, end_to_end = await ask(session, name_question_pcm)
        finally:
            deleted = await http.delete(persona_url)
        assert deleted.status_code == 204

    print(
        f"\n[persona] read {read_sec:.2f}s  first audio {first_audio:.2f}s"
        f"  end to end {end_to_end:.2f}s"
        f"\n[persona] fields read: {fields!r}"
        f"\n[persona] answer, default character: {default_text!r}"
        f"\n[persona] answer, persona saved: {answer_text!r}"
    )

    assert session.of_type("error") == []
    assert _named(fields.get("name", "")), fields
    assert "em" in fields.get("addressing", "").lower(), fields
    assert not _named(default_text), default_text
    assert _named(answer_text), answer_text
    assert first_audio < FIRST_AUDIO_BUDGET_SEC
    assert end_to_end < END_TO_END_BUDGET_SEC
