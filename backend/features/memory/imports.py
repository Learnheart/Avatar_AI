"""
MemoryImports — what a user imports to describe themselves, read into memory items.

An import is a file or a pasted text. Its bytes are kept unchanged in the object store,
its metadata in the application database; one LLM call, outside any turn, reads its text
into active memory items naming it. The text itself is stored nowhere: it is derived from
the bytes. An import that cannot be read into items is not kept at all.

The store, the object store, the LLM and the way to drop a user's cached profile are
given by `backend/main.py`.
"""
import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from typing import Callable, Optional, TypeVar

from opentelemetry.trace import Span

from backend.config import AppConfig
from backend.core.contracts import LLM, LLMError, MemoryServiceError
from backend.features.memory.extraction import InvalidExtraction, parse_items
from backend.features.memory.memory import item_text, one_line
from backend.features.memory.reading import as_document, comparable, finish, quote
from backend.features.memory.store import MemoryStore
from backend.models import (
    MEMORY_SOURCE_IMPORT,
    MEMORY_STATUS_ACTIVE,
    MemoryImport,
    MemoryItem,
)
from backend.utils.database import DatabaseError
from backend.utils.loaders import (
    UnreadableDocument,
    UnsupportedDocument,
    extract_text,
    supported_extension,
)
from backend.utils.logging import get_logger
from backend.utils.object_store import ObjectStore, ObjectStoreError
from backend.utils.telemetry import tracer

logger = get_logger("memory.imports")

T = TypeVar("T")

_MB = 1024 * 1024

# Where the original of an import is kept in the object store. The key names no user:
# who owns it is in the database.
_OBJECT_KEY = "imports/{id}"

# The type the original of an import is given back with, from its extension: one per
# extension the loaders read.
_CONTENT_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

class ImportTooLarge(ValueError):
    """The file is larger, or its text longer, than an import may be."""


class EmptyImport(ValueError):
    """The import holds no text."""


class NothingToRemember(ValueError):
    """The import was read and holds nothing about the person worth keeping."""


class ImportExtractionFailed(Exception):
    """The LLM failed, or answered something else than the items asked for."""


# `memory.outcome` of the `memory.import` span, by the error that ended the import.
_OUTCOMES: dict[type, str] = {
    ImportTooLarge: "too_large",
    UnsupportedDocument: "unsupported",
    UnreadableDocument: "unreadable",
    EmptyImport: "empty",
    ImportExtractionFailed: "extraction_failed",
    NothingToRemember: "nothing",
}


def max_file_bytes(config: AppConfig) -> int:
    """The largest file an import may be (MEMORY_IMPORT_MAX_FILE_MB), in bytes."""
    return int(config.MEMORY_IMPORT_MAX_FILE_MB * _MB)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryImports:
    def __init__(
        self,
        config: AppConfig,
        store: MemoryStore,
        objects: ObjectStore,
        llm: LLM,
        forget_profile: Callable[[str], None],
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        """`forget_profile(user_id)` drops the cached profile of a user whose items
        changed (`MemoryService.forget_profile`)."""
        self.config = config
        self._store = store
        self._objects = objects
        self._llm = llm
        self._forget_profile = forget_profile
        self._clock = clock

    async def import_file(
        self, user_id: str, filename: str, data: bytes
    ) -> tuple[MemoryImport, list[MemoryItem]]:
        """Keep an uploaded file and what it says about the user: the import and its
        items, used from the next turn on.
        Raises ImportTooLarge, UnsupportedDocument, UnreadableDocument, EmptyImport,
        NothingToRemember (the file), ImportExtractionFailed (the LLM) or
        MemoryServiceError (a store). Whatever is raised, nothing of the import is kept."""
        name = one_line(PurePath(filename.replace("\\", "/")).name)
        return await self._import(user_id, name, data)

    async def import_text(
        self, user_id: str, text: str
    ) -> tuple[MemoryImport, list[MemoryItem]]:
        """Keep a pasted text as a `.txt` import. Raises like `import_file`."""
        data = text.encode("utf-8", errors="replace")
        return await self._import(user_id, self.config.MEMORY_IMPORT_TEXT_NAME, data)

    async def list_imports(self, user_id: str) -> list[tuple[MemoryImport, int]]:
        """The imports of the user, oldest first, each with the number of its items."""
        return await self._call(self._store.list_imports, user_id)

    async def import_content(
        self, user_id: str, import_id: str
    ) -> Optional[tuple[MemoryImport, bytes]]:
        """The import and its original bytes, as they were given. None when the user has
        no such import: the bytes are reached through the user's row only."""
        record = await self._call(self._store.get_import, user_id, import_id)
        if record is None:
            return None
        return record, await self._object(self._objects.get, record.object_key)

    async def delete_import(self, user_id: str, import_id: str, delete_items: bool) -> bool:
        """Remove the import and its original; its items go with it, or stay as items
        without a source. False when the user has no such import."""
        record = await self._call(self._store.get_import, user_id, import_id)
        if record is None:
            return False
        # The bytes first: if the row cannot be removed after them, the import is still
        # listed and deleting it again finishes the job.
        await self._object(self._objects.delete, record.object_key)
        deleted = await self._write(
            user_id, self._store.delete_import, user_id, import_id, delete_items
        )
        if deleted:
            logger.info(
                {"msg": "Memory import deleted", "id": import_id, "items_deleted": delete_items}
            )
        return deleted

    # ── The import flow ───────────────────────────────────────────────────

    async def _import(
        self, user_id: str, name: str, data: bytes
    ) -> tuple[MemoryImport, list[MemoryItem]]:
        with tracer.start_as_current_span(
            "memory.import", attributes={"import.name": name, "import.bytes": len(data)}
        ) as span:
            try:
                record, items = await self._store_import(span, user_id, name, data)
            except Exception as e:
                span.set_attribute("memory.outcome", _OUTCOMES.get(type(e), "error"))
                raise
            span.set_attributes({
                "import.id": record.id,
                "memory.items": len(items),
                "memory.outcome": "stored",
                "import.items": json.dumps(
                    [
                        {"category": item.category, "text": item.text, "evidence": item.evidence}
                        for item in items
                    ],
                    ensure_ascii=False,
                ),
            })
        logger.info({"msg": "Memory import stored", "id": record.id, "items": len(items)})
        return record, items

    async def _store_import(
        self, span: Span, user_id: str, name: str, data: bytes
    ) -> tuple[MemoryImport, list[MemoryItem]]:
        if len(data) > max_file_bytes(self.config):
            raise ImportTooLarge(f"File larger than {self.config.MEMORY_IMPORT_MAX_FILE_MB:g} MB")
        # Before anything is written: a type that cannot be read costs no store.
        extension = supported_extension(name)
        import_id = uuid.uuid4().hex
        record = MemoryImport(
            id=import_id,
            user_id=user_id,
            name=name,
            content_type=_CONTENT_TYPES[extension],
            size_bytes=len(data),
            sha256=await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest()),
            object_key=_OBJECT_KEY.format(id=import_id),
            created_at=self._clock(),
        )
        # The original and its row first, then what is read from it. Whatever stops the
        # import (the request being dropped included, at any of these steps), what it
        # wrote is removed again: there is no object without a row, and no import
        # without items.
        try:
            await self._object(self._objects.put, record.object_key, data)
            await self._call(self._store.add_import, record)
            items = await self._read_import(span, record, data)
            await self._write(user_id, self._store.add_items, items)
        except BaseException:
            await self._discard_import(record)
            raise
        return record, items

    async def _read_import(
        self, span: Span, record: MemoryImport, data: bytes
    ) -> list[MemoryItem]:
        """What the import says about the user, as items not stored yet."""
        text = await asyncio.to_thread(extract_text, record.name, data)
        if not text.strip():
            raise EmptyImport(f"'{record.name}' holds no text")
        limit = self.config.MEMORY_IMPORT_MAX_CHARS
        if len(text) > limit:
            raise ImportTooLarge(
                f"The text is longer than {limit} characters: it has {len(text)}"
            )
        span.set_attribute("import.text", text)

        max_chars = self.config.MEMORY_ITEM_MAX_CHARS
        prompt = self.config.MEMORY_IMPORT_PROMPT.format(max_chars=max_chars)
        try:
            document = as_document(self.config.MEMORY_IMPORT_DOCUMENT, text)
            answer = await self._llm.complete(prompt, document)
        except LLMError as e:
            raise ImportExtractionFailed(f"The LLM failed: {e}") from e
        span.set_attribute("llm.answer", answer)
        try:
            extracted = parse_items(answer, lambda found: item_text(found, max_chars))
        except InvalidExtraction as e:
            raise ImportExtractionFailed(str(e)) from e
        if not extracted:
            raise NothingToRemember(f"Nothing to remember in '{record.name}'")

        now = self._clock()
        document = comparable(text)
        return [
            MemoryItem(
                id=uuid.uuid4().hex,
                user_id=record.user_id,
                category=found.category,
                text=found.text,
                # A microsecond apart: the items are listed in the order of the answer.
                created_at=now + timedelta(microseconds=position),
                updated_at=now + timedelta(microseconds=position),
                status=MEMORY_STATUS_ACTIVE,
                source=MEMORY_SOURCE_IMPORT,
                import_id=record.id,
                evidence=quote(found.evidence, document),
            )
            for position, found in enumerate(extracted)
        ]

    async def _discard_import(self, record: MemoryImport) -> None:
        """Remove what a stopped import left: its row (with any item) and its object.
        A failure here is logged and does not hide what stopped the import; a request
        dropped once more does not stop the removal."""
        try:
            for call, args in (
                (self._store.delete_import, (record.user_id, record.id, True)),
                (self._objects.delete, (record.object_key,)),
            ):
                try:
                    await finish(call, *args)
                except (DatabaseError, ObjectStoreError) as e:
                    logger.error({
                        "msg": "A stopped import was not removed", "id": record.id, "error": str(e)
                    })
                except asyncio.CancelledError:
                    # The caller raises what stopped the import, right after this.
                    pass
        finally:
            # Items may have been written and removed again: like any other write.
            self._forget_profile(record.user_id)

    # ── Internals ─────────────────────────────────────────────────────────

    async def _write(self, user_id: str, call: Callable[..., T], *args) -> T:
        """Run a store call that changes the user's items, then drop their profile."""
        try:
            return await self._call(call, *args)
        finally:
            self._forget_profile(user_id)

    async def _call(self, call: Callable[..., T], *args) -> T:
        try:
            return await finish(call, *args)
        except DatabaseError as e:
            raise MemoryServiceError(f"The memory store failed: {e}") from e

    async def _object(self, call: Callable[..., T], *args) -> T:
        """Run a call of the object store. A missing object is a failure too: an import
        that is listed has its original."""
        try:
            return await finish(call, *args)
        except ObjectStoreError as e:
            raise MemoryServiceError(f"The object store failed: {e}") from e
