"""
Sign-in against the real backend: a person creates an account, presents its session on
the WebSocket and is answered as that user (their avatar's persona), then signs out on
the same connection and is answered as a guest. The backend runs on a database of its
own, with no configured user: the session token decides.
"""
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
ACCOUNT = {"username": "e2e-linh", "password": "mật khẩu của bài test"}


@pytest.fixture(scope="module")
def ws_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    data_dir = tmp_path_factory.mktemp("sign-in-data")
    yield from run_backend(tmp_path_factory, {
        "AUTH_DEV_USER_ID": "",
        "DATABASE_URL": f"sqlite:///{data_dir / 'app.db'}",
        "OBJECT_STORE_DIR": str(data_dir / "objects"),
    })


@pytest.fixture
async def session(ws_url: str) -> AsyncIterator[Session]:
    async for client in connect(ws_url):
        yield client


async def _present(session: Session, token) -> dict:
    """Say who the page is; the mode the backend answers."""
    asked = len(session.messages)
    await session.send({"type": "auth", "token": token})
    _, _, told = await session.wait_for(lambda m: m["type"] == "session", timeout=10, after=asked)
    return told


def _named(text: str) -> bool:
    return NAME.lower() in text.lower()


async def test_a_signed_in_person_is_answered_as_themselves_and_a_guest_is_not(
    session: Session, ws_url: str, name_question_pcm: bytes
):
    api = ws_url.replace("ws://", "http://").removesuffix("/ws")
    async with httpx.AsyncClient(timeout=60) as http:
        registered = await http.post(f"{api}/auth/register", json=ACCOUNT)
        assert registered.status_code == 201, registered.text
        signed_in = registered.json()
        headers = {"Authorization": f"Bearer {signed_in['token']}"}
        assert (await http.get(f"{api}/persona")).status_code == 401  # a guest
        saved = await http.put(f"{api}/persona", headers=headers, json={"fields": {"name": NAME}})
        assert saved.status_code == 200, saved.text

        assert (await _present(session, None))["user"] is None
        guest_text, _, _ = await ask(session, name_question_pcm)

        assert (await _present(session, signed_in["token"]))["user"] == signed_in["user"]
        user_text, first_audio, end_to_end = await ask(session, name_question_pcm)

        assert (await http.post(f"{api}/auth/logout", headers=headers)).status_code == 204
        assert (await _present(session, signed_in["token"]))["user"] is None  # it is over
        after_text, _, _ = await ask(session, name_question_pcm)

    print(
        f"\n[sign-in] first audio {first_audio:.2f}s  end to end {end_to_end:.2f}s"
        f"\n[sign-in] answer to a guest: {guest_text!r}"
        f"\n[sign-in] answer to the signed-in user: {user_text!r}"
        f"\n[sign-in] answer after signing out: {after_text!r}"
    )

    assert session.of_type("error") == []
    assert not _named(guest_text), guest_text
    assert _named(user_text), user_text
    assert not _named(after_text), after_text
    assert first_audio < FIRST_AUDIO_BUDGET_SEC
    assert end_to_end < END_TO_END_BUDGET_SEC
