"""
Sign-in against the real backend: a person creates an account with the code sent to
their email, presents its session on the WebSocket and is answered as that user (their
avatar's persona), then signs out on the same connection and is answered as a guest.
The backend runs on a database of its own, with no configured user (the session token
decides) and with the mails written to its log, where the test reads the code.
"""
import json
import re
from typing import AsyncIterator, Iterator

import httpx
import pytest

from tests.e2e.helpers import E2E_MARKS
from tests.e2e.test_real_turn import (
    BACKEND_LOGS,
    END_TO_END_BUDGET_SEC,
    FIRST_AUDIO_BUDGET_SEC,
    Session,
    ask,
    connect,
    run_backend,
)

pytestmark = E2E_MARKS

NAME = "Bông"
ACCOUNT = {"email": "e2e-linh@example.com", "password": "mật khẩu của bài test"}


@pytest.fixture(scope="module")
def ws_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    data_dir = tmp_path_factory.mktemp("sign-in-data")
    yield from run_backend(tmp_path_factory, {
        "AUTH_DEV_USER_ID": "",
        "MAIL_BACKEND": "log",
        "DATABASE_URL": f"sqlite:///{data_dir / 'app.db'}",
        "OBJECT_STORE_DIR": str(data_dir / "objects"),
    })


@pytest.fixture
async def session(ws_url: str) -> AsyncIterator[Session]:
    async for client in connect(ws_url):
        yield client


async def _present(session: Session, token, frame_id: str) -> dict:
    """Say who the page is, in an `auth` frame with this `id`; the `session` message
    that answers that frame."""
    asked = len(session.messages)
    await session.send({"type": "auth", "token": token, "id": frame_id})
    _, _, told = await session.wait_for(
        lambda m: m["type"] == "session" and m.get("reply_to") == frame_id,
        timeout=10, after=asked,
    )
    return told


def _mailed_code(ws_url: str, email: str) -> str:
    """The code of the last mail the backend wrote to its log for this address."""
    mails = []
    for line in BACKEND_LOGS[ws_url].read_text().splitlines():
        if "MAIL_BACKEND=log" in line and email in line:
            mails.append(json.loads(line)["body"])
    return re.search(r"\b(\d{6})\b", mails[-1]).group(1)


def _named(text: str) -> bool:
    return NAME.lower() in text.lower()


async def test_a_signed_in_person_is_answered_as_themselves_and_a_guest_is_not(
    session: Session, ws_url: str, name_question_pcm: bytes
):
    api = ws_url.replace("ws://", "http://").removesuffix("/ws")
    async with httpx.AsyncClient(timeout=60) as http:
        asked = await http.post(f"{api}/auth/register", json={"email": ACCOUNT["email"]})
        assert asked.status_code == 202, asked.text
        assert (await http.post(f"{api}/auth/login", json=ACCOUNT)).status_code == 401  # not yet
        registered = await http.post(f"{api}/auth/register/verify", json={
            **ACCOUNT, "code": _mailed_code(ws_url, ACCOUNT["email"]),
        })
        assert registered.status_code == 201, registered.text
        signed_in = registered.json()
        headers = {"Authorization": f"Bearer {signed_in['token']}"}
        assert (await http.get(f"{api}/persona")).status_code == 401  # a guest
        saved = await http.put(f"{api}/persona", headers=headers, json={"fields": {"name": NAME}})
        assert saved.status_code == 200, saved.text

        told = await _present(session, None, "auth-1")
        assert (told["user"], told["status"]) == (None, "guest")
        guest_text, _, _ = await ask(session, name_question_pcm)

        told = await _present(session, signed_in["token"], "auth-2")
        assert (told["user"], told["status"]) == (signed_in["user"], "signed_in")
        user_text, first_audio, end_to_end = await ask(session, name_question_pcm)

        assert (await http.post(f"{api}/auth/logout", headers=headers)).status_code == 204
        told = await _present(session, signed_in["token"], "auth-3")  # it is over
        assert (told["user"], told["status"]) == (None, "rejected")
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
