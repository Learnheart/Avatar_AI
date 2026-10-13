"""
PersonaService — the personality a logged-in user gives the avatar.

A persona is a dictionary of predefined fields (`PERSONA_FIELDS`), each one short line
of text; any of them may be left out. The fields that are set are written into the text
the LLM is given with each turn of that user; the text is cached per user and dropped
when the user saves or removes their persona, so a turn reads from RAM and a change
applies to the next turn. The store is given by `backend/main.py`.
"""
import asyncio
from datetime import datetime, timezone
from typing import Callable, Mapping, TypeVar

from backend.config import AppConfig
from backend.core.contracts import Persona, PersonaError
from backend.features.memory.memory import one_line
from backend.features.persona.store import PersonaStore
from backend.models import PERSONA_FIELDS
from backend.utils.database import DatabaseError
from backend.utils.logging import get_logger

logger = get_logger("persona")

T = TypeVar("T")


class InvalidPersona(ValueError):
    """The persona cannot be stored: an unknown field, or a text too long."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PersonaService(Persona):
    def __init__(
        self, config: AppConfig, store: PersonaStore, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self.config = config
        self._store = store
        self._clock = clock
        self._prompts: dict[str, str] = {}
        # Counts the writes per user: a prompt read from the store while the user's
        # persona was changing is not cached.
        self._writes: dict[str, int] = {}

    # ── The text of the turn (the `Persona` contract) ─────────────────────

    async def prompt(self, user_id: str) -> str:
        cached = self._prompts.get(user_id)
        if cached is not None:
            return cached
        writes = self._writes.get(user_id, 0)
        prompt = self._render(await self._call(self._store.get, user_id))
        if self._writes.get(user_id, 0) == writes:
            self._prompts[user_id] = prompt
        return prompt

    def _render(self, fields: Mapping[str, str]) -> str:
        """The fields that are set, in the order of PERSONA_FIELDS, each with its
        label; "" when none is. A stored field that is no longer one is left out."""
        listed = "\n".join(
            self.config.PERSONA_PROMPT_FIELD.format(
                label=self.config.PERSONA_FIELD_LABELS[name], value=fields[name]
            )
            for name in PERSONA_FIELDS
            if name in fields
        )
        return self.config.PERSONA_PROMPT.format(fields=listed) if listed else ""

    # ── The fields (the profile page) ─────────────────────────────────────

    async def fields(self, user_id: str) -> dict[str, str]:
        """The fields the user set, in the order of PERSONA_FIELDS; {} when none is."""
        stored = await self._call(self._store.get, user_id)
        return {name: stored[name] for name in PERSONA_FIELDS if name in stored}

    async def save(self, user_id: str, fields: Mapping[str, str]) -> dict[str, str]:
        """Make `fields` the whole persona of the user, used from the next turn on: a
        field that is left out or empty is not part of it. The fields as they are
        stored. Raises InvalidPersona."""
        cleaned = clean_fields(fields, self.config.PERSONA_FIELD_MAX_CHARS)
        await self._write(user_id, cleaned)
        logger.info({"msg": "Persona saved", "fields": list(cleaned)})
        return cleaned

    async def clear(self, user_id: str) -> None:
        """Remove the persona of the user: the avatar is the default character again."""
        await self._write(user_id, {})
        logger.info({"msg": "Persona removed"})

    # ── Internals ─────────────────────────────────────────────────────────

    async def _write(self, user_id: str, fields: Mapping[str, str]) -> None:
        """Replace the persona of the user, then drop their cached prompt."""
        try:
            await self._call(self._store.replace, user_id, fields, self._clock())
        finally:
            self._writes[user_id] = self._writes.get(user_id, 0) + 1
            self._prompts.pop(user_id, None)

    async def _call(self, call: Callable[..., T], *args) -> T:
        try:
            return await asyncio.to_thread(call, *args)
        except DatabaseError as e:
            raise PersonaError(f"The persona store failed: {e}") from e


def clean_fields(fields: Mapping[str, str], max_chars: int) -> dict[str, str]:
    """The fields as they are stored, in the order of PERSONA_FIELDS: each value on one
    line of plain characters, the empty ones left out. Raises InvalidPersona for a
    field that is not one of PERSONA_FIELDS or a value longer than `max_chars`."""
    unknown = [name for name in fields if name not in PERSONA_FIELDS]
    if unknown:
        raise InvalidPersona(
            f"Unknown field '{unknown[0]}': one of {', '.join(PERSONA_FIELDS)}"
        )
    cleaned = {}
    for name in PERSONA_FIELDS:
        value = one_line(fields.get(name) or "")
        if len(value) > max_chars:
            raise InvalidPersona(
                f"'{name}' is one short line: at most {max_chars} characters, "
                f"this text has {len(value)}"
            )
        if value:
            cleaned[name] = value
    return cleaned
