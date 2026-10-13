"""
PersonaService — the personality a logged-in user gives the avatar.

A persona is a dictionary of predefined fields (`PERSONA_FIELDS`), each one short line
of text; any of them may be left out. The fields that are set replace the default ones
in the system prompt of each turn of that user; the text is cached per user and dropped
when the user saves or removes their persona, so a turn reads from RAM and a change
applies to the next turn.

A description of the avatar written freely is read into the fields by one LLM call,
outside any turn. What is read is given back and stored nowhere: the person reviews the
fields and saves them. The store and the LLM are given by `backend/main.py`.
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Callable, Mapping, TypeVar

from opentelemetry.trace import Span

from backend.config import AppConfig
from backend.core.contracts import LLM, LLMError, Persona, PersonaError
from backend.core.conversation.history import persona_lines
from backend.features.memory.memory import one_line
from backend.features.memory.reading import as_document
from backend.features.persona.extraction import InvalidPersonaAnswer, parse_fields
from backend.features.persona.store import PersonaStore
from backend.models import PERSONA_FIELDS
from backend.utils.database import DatabaseError
from backend.utils.logging import get_logger
from backend.utils.telemetry import tracer

logger = get_logger("persona")

T = TypeVar("T")


class InvalidPersona(ValueError):
    """The persona cannot be stored: an unknown field, or a text too long."""


class EmptyDescription(ValueError):
    """The description holds no text."""


class DescriptionTooLong(ValueError):
    """The description is longer than one may be."""


class NothingToRead(ValueError):
    """The description was read and says nothing about the avatar."""


class PersonaReadFailed(Exception):
    """The LLM failed, or answered something else than the fields asked for."""


# `persona.outcome` of the `persona.read` span, by the error that ended the reading.
_OUTCOMES: dict[type, str] = {
    EmptyDescription: "empty",
    DescriptionTooLong: "too_long",
    PersonaReadFailed: "extraction_failed",
    NothingToRead: "nothing",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PersonaService(Persona):
    def __init__(
        self,
        config: AppConfig,
        store: PersonaStore,
        llm: LLM,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.config = config
        self._store = store
        self._llm = llm
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
        """The persona as the system prompt takes it: the fields the user set over the
        default ones; "" when the user set none (the default persona). A stored field
        that is no longer one is left out."""
        known = {name: fields[name] for name in PERSONA_FIELDS if name in fields}
        return persona_lines(self.config, known) if known else ""

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

    # ── Reading a description ─────────────────────────────────────────────

    async def read(self, text: str) -> dict[str, str]:
        """The fields a description of the avatar states, read by the LLM, in the order
        of PERSONA_FIELDS. Nothing is stored: `save` does that, once the person has
        seen them. Raises EmptyDescription, DescriptionTooLong, NothingToRead (the
        text) or PersonaReadFailed (the LLM)."""
        with tracer.start_as_current_span(
            "persona.read", attributes={"persona.chars": len(text)}
        ) as span:
            try:
                fields = await self._read(span, text)
            except Exception as e:
                span.set_attribute("persona.outcome", _OUTCOMES.get(type(e), "error"))
                raise
            span.set_attributes({
                "persona.outcome": "read",
                "persona.fields": json.dumps(fields, ensure_ascii=False),
            })
        logger.info({"msg": "Persona description read", "fields": list(fields)})
        return fields

    async def _read(self, span: Span, text: str) -> dict[str, str]:
        if not text.strip():
            raise EmptyDescription("The description holds no text")
        limit = self.config.PERSONA_IMPORT_MAX_CHARS
        if len(text) > limit:
            raise DescriptionTooLong(
                f"The description is longer than {limit} characters: it has {len(text)}"
            )
        span.set_attribute("persona.description", text)

        max_chars = self.config.PERSONA_FIELD_MAX_CHARS
        prompt = self.config.PERSONA_IMPORT_PROMPT.format(max_chars=max_chars)
        try:
            document = as_document(self.config.PERSONA_IMPORT_DOCUMENT, text)
            answer = await self._llm.complete(prompt, document)
        except LLMError as e:
            raise PersonaReadFailed(f"The LLM failed: {e}") from e
        span.set_attribute("llm.answer", answer)
        try:
            fields = parse_fields(answer, max_chars)
        except InvalidPersonaAnswer as e:
            raise PersonaReadFailed(str(e)) from e
        if not fields:
            raise NothingToRead("Nothing about the avatar in the description")
        return fields

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
