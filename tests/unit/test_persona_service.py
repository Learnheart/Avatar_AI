"""PersonaService: the fields a user gives the avatar and the prompt written from them."""
from datetime import datetime, timezone
from typing import Iterator

import pytest

from backend.config import AppConfig
from backend.core.contracts import PersonaError
from backend.features.accounts.store import UsersStore
from backend.features.persona.persona import InvalidPersona, PersonaService
from backend.features.persona.store import PersonaStore
from backend.models import PERSONA_FIELDS
from backend.utils.database import Database, DatabaseError

NOW = datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc)
MAI = {"name": "Mai", "addressing": "xưng em, gọi anh"}


class CountingStore(PersonaStore):
    """The real store, counting the reads; `down` makes every call fail."""

    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self.reads = 0
        self.down = False

    def get(self, user_id):
        if self.down:
            raise DatabaseError("database is gone")
        self.reads += 1
        return super().get(user_id)

    def replace(self, user_id, fields, now):
        if self.down:
            raise DatabaseError("database is gone")
        super().replace(user_id, fields, now)


@pytest.fixture
def store(config) -> Iterator[CountingStore]:
    database = Database(config.DATABASE_URL)
    database.start()
    users = UsersStore(database)
    for user_id in ("linh", "an"):
        users.touch(user_id, user_id, NOW)
    yield CountingStore(database)
    database.close()


@pytest.fixture
def persona(config, store) -> PersonaService:
    return PersonaService(config, store, clock=lambda: NOW)


def _lines(prompt: str) -> list[str]:
    """The fields of the prompt, in their order: the block between the introduction
    and the rules of PERSONA_PROMPT."""
    if not prompt:
        return []
    _, fields, _ = prompt.split("\n\n", 2)
    return fields.splitlines()


# ── Texts ─────────────────────────────────────────────────────────────────

def test_every_field_has_its_label_and_is_named_in_the_import_prompt():
    config = AppConfig()

    assert set(config.PERSONA_FIELD_LABELS) == set(PERSONA_FIELDS)
    for name in PERSONA_FIELDS:
        assert f"- {name}:" in config.PERSONA_IMPORT_PROMPT


# ── The prompt of the turn ────────────────────────────────────────────────

async def test_without_persona_the_prompt_is_empty(persona):
    assert await persona.prompt("linh") == ""


async def test_the_prompt_lists_the_set_fields_in_their_order_with_their_labels(persona, config):
    await persona.save("linh", {"answer_style": "Ngắn gọn", "name": "Mai", "role": "Gia sư"})

    prompt = await persona.prompt("linh")

    assert _lines(prompt) == [
        "- Tên của trợ lý: Mai", "- Vai trò: Gia sư", "- Cách trả lời: Ngắn gọn",
    ]
    assert prompt == config.PERSONA_PROMPT.format(fields="\n".join(_lines(prompt)))


async def test_the_prompt_is_read_once_then_answered_from_ram(persona, store):
    await persona.save("linh", MAI)

    first, second = await persona.prompt("linh"), await persona.prompt("linh")

    assert first == second != ""
    assert store.reads == 1


async def test_an_empty_prompt_is_cached_too(persona, store):
    await persona.prompt("linh")
    await persona.prompt("linh")

    assert store.reads == 1


async def test_a_save_and_a_clear_change_the_prompt_of_the_next_turn(persona):
    await persona.save("linh", MAI)
    assert "Mai" in await persona.prompt("linh")

    await persona.save("linh", {"name": "Lan"})
    renamed = await persona.prompt("linh")
    await persona.clear("linh")

    assert _lines(renamed) == ["- Tên của trợ lý: Lan"]
    assert await persona.prompt("linh") == ""


async def test_one_user_s_persona_is_not_in_another_s_prompt(persona):
    await persona.save("linh", MAI)

    assert await persona.prompt("an") == ""
    assert await persona.fields("an") == {}


async def test_braces_in_a_value_do_not_break_the_prompt(persona):
    await persona.save("linh", {"avoid": "Không dùng {fields} hay {0}"})

    assert _lines(await persona.prompt("linh")) == ["- Điều cần tránh: Không dùng {fields} hay {0}"]


async def test_a_stored_field_that_is_no_longer_one_is_left_out(persona, store):
    store.replace("linh", {"name": "Mai", "voice": "trầm"}, NOW)

    assert _lines(await persona.prompt("linh")) == ["- Tên của trợ lý: Mai"]
    assert await persona.fields("linh") == {"name": "Mai"}


# ── The fields ────────────────────────────────────────────────────────────

async def test_the_saved_fields_come_back_in_the_order_of_the_fields(persona):
    saved = await persona.save("linh", {"avoid": "Chính trị", "name": "Mai"})

    assert list(saved) == ["name", "avoid"]
    assert await persona.fields("linh") == saved


async def test_a_value_is_stored_on_one_line_of_plain_characters(persona):
    saved = await persona.save("linh", {"personality": "  Vui vẻ,\n\thay đùa​  "})

    assert saved == {"personality": "Vui vẻ, hay đùa"}


async def test_an_empty_value_leaves_its_field_out(persona):
    await persona.save("linh", MAI)

    saved = await persona.save("linh", {"name": "Mai", "addressing": " \n ", "role": ""})

    assert saved == {"name": "Mai"}
    assert await persona.fields("linh") == {"name": "Mai"}


@pytest.mark.parametrize(
    "fields",
    [{"voice": "trầm"}, {"name": "Mai", "Name": "Lan"}, {"name": "x" * 301}],
)
async def test_an_unknown_field_or_a_long_value_is_refused_and_nothing_is_stored(persona, fields):
    await persona.save("linh", MAI)

    with pytest.raises(InvalidPersona):
        await persona.save("linh", fields)

    assert await persona.fields("linh") == MAI


async def test_the_length_limit_follows_the_config(persona, config):
    config.PERSONA_FIELD_MAX_CHARS = 3

    assert await persona.save("linh", {"name": "Mai"}) == {"name": "Mai"}
    with pytest.raises(InvalidPersona, match="at most 3 characters"):
        await persona.save("linh", {"name": "Mai!"})


async def test_a_failing_store_raises_persona_error(persona, store):
    store.down = True

    with pytest.raises(PersonaError):
        await persona.prompt("linh")
    with pytest.raises(PersonaError):
        await persona.fields("linh")
    with pytest.raises(PersonaError):
        await persona.save("linh", MAI)
    with pytest.raises(PersonaError):
        await persona.clear("linh")


async def test_a_prompt_read_before_a_failed_save_is_read_again(persona, store):
    await persona.prompt("linh")
    store.down = True
    with pytest.raises(PersonaError):
        await persona.save("linh", MAI)
    store.down = False

    await persona.prompt("linh")

    assert store.reads == 2
