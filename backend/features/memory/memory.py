"""
MemoryService — what the avatar remembers about a logged-in user.

A memory item is one short sentence about the user. The active items are written into
the profile the LLM is given with each turn; the profile is cached per user and dropped
when one of the user's items changes, so a turn reads from RAM and an edit applies to the
next turn. The store is given by `backend/main.py`. What a user imports is read into
items by `imports.py`.
"""
import asyncio
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, TypeVar

from backend.config import AppConfig
from backend.core.contracts import Memory, MemoryServiceError
from backend.features.memory.store import MemoryStore
from backend.models import (
    MEMORY_CATEGORIES,
    MEMORY_SOURCE_MANUAL,
    MEMORY_STATUS_ACTIVE,
    MemoryItem,
    MemoryProfile,
)
from backend.utils.database import DatabaseError
from backend.utils.logging import get_logger

logger = get_logger("memory")

T = TypeVar("T")

# Unicode categories of characters that show nothing: control (Cc) and format (Cf).
_INVISIBLE_CATEGORIES = ("Cc", "Cf")

# Placed first in the profile, in this order: who the person is and how to address them.
_FIRST_CATEGORIES = ("identity", "addressing")


class InvalidMemoryItem(ValueError):
    """The item cannot be stored: unknown category, no visible text, or a text too long."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryService(Memory):
    def __init__(
        self, config: AppConfig, store: MemoryStore, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        self.config = config
        self._store = store
        self._clock = clock
        self._profiles: dict[str, MemoryProfile] = {}
        # Counts the writes per user: a profile read from the store while one of the
        # user's items was changing is not cached.
        self._writes: dict[str, int] = {}

    # ── Profile (the `Memory` contract) ───────────────────────────────────

    async def profile(self, user_id: str) -> MemoryProfile:
        cached = self._profiles.get(user_id)
        if cached is not None:
            return cached
        writes = self._writes.get(user_id, 0)
        items = await self._call(self._store.list_items, user_id, MEMORY_STATUS_ACTIVE)
        profile = self._render(items)
        if self._writes.get(user_id, 0) == writes:
            self._profiles[user_id] = profile
        return profile

    def _render(self, items: list[MemoryItem]) -> MemoryProfile:
        """`identity` and `addressing` first, then the most recently updated, up to
        MEMORY_PROMPT_MAX_ITEMS."""
        def rank(item: MemoryItem) -> int:
            if item.category in _FIRST_CATEGORIES:
                return _FIRST_CATEGORIES.index(item.category)
            return len(_FIRST_CATEGORIES)

        recent_first = sorted(items, key=lambda item: item.updated_at, reverse=True)
        chosen = sorted(recent_first, key=rank)[: self.config.MEMORY_PROMPT_MAX_ITEMS]
        if not chosen:
            return MemoryProfile()
        listed = "\n".join(
            self.config.MEMORY_PROMPT_ITEM.format(text=item.text) for item in chosen
        )
        return MemoryProfile(self.config.MEMORY_PROMPT.format(items=listed), len(chosen))

    def forget_profile(self, user_id: str) -> None:
        """Drop the cached profile of the user: their items changed, or may have. The
        next turn reads them again."""
        self._writes[user_id] = self._writes.get(user_id, 0) + 1
        self._profiles.pop(user_id, None)

    # ── Items (the profile page) ──────────────────────────────────────────

    async def list_items(self, user_id: str) -> list[MemoryItem]:
        return await self._call(self._store.list_items, user_id)

    async def add_item(self, user_id: str, category: str, text: str) -> MemoryItem:
        """Keep what the user typed about themselves: used from the next turn on.
        Raises InvalidMemoryItem."""
        now = self._clock()
        item = MemoryItem(
            id=uuid.uuid4().hex,
            user_id=user_id,
            category=_category(category),
            text=self._text(text),
            created_at=now,
            updated_at=now,
            status=MEMORY_STATUS_ACTIVE,
            source=MEMORY_SOURCE_MANUAL,
        )
        await self._write(user_id, self._store.add_item, item)
        logger.info({"msg": "Memory item added", "id": item.id, "category": item.category})
        return item

    async def update_item(
        self,
        user_id: str,
        item_id: str,
        text: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Optional[MemoryItem]:
        """Change the text and / or the category. None when the user has no such item.
        Raises InvalidMemoryItem."""
        item = await self._write(
            user_id,
            self._store.update_item,
            user_id,
            item_id,
            self._clock(),
            None if text is None else self._text(text),
            None if category is None else _category(category),
        )
        if item:
            logger.info({"msg": "Memory item changed", "id": item_id})
        return item

    async def delete_item(self, user_id: str, item_id: str) -> bool:
        """False when the user has no such item."""
        deleted = await self._write(user_id, self._store.delete_item, user_id, item_id)
        if deleted:
            logger.info({"msg": "Memory item deleted", "id": item_id})
        return deleted

    # ── Internals ─────────────────────────────────────────────────────────

    def _text(self, text: str) -> str:
        return item_text(text, self.config.MEMORY_ITEM_MAX_CHARS)

    async def _write(self, user_id: str, call: Callable[..., T], *args) -> T:
        """Run a store call that changes the user's items, then drop their profile."""
        try:
            return await self._call(call, *args)
        finally:
            self.forget_profile(user_id)

    async def _call(self, call: Callable[..., T], *args) -> T:
        try:
            return await asyncio.to_thread(call, *args)
        except DatabaseError as e:
            raise MemoryServiceError(f"The memory store failed: {e}") from e


def item_text(text: str, max_chars: int) -> str:
    """The text of an item as it is stored, whatever wrote it: one line, not empty, at
    most `max_chars` characters. Raises InvalidMemoryItem."""
    text = one_line(text)
    if not text:
        raise InvalidMemoryItem("A memory item needs a text")
    if len(text) > max_chars:
        raise InvalidMemoryItem(
            f"A memory item is one short sentence: at most {max_chars} characters, "
            f"this text has {len(text)}"
        )
    return text


def one_line(text: str) -> str:
    """The text on one line of plain characters. An item is one line of the profile the
    LLM is given, so a line break in it would write further lines of that message. Every
    run of whitespace (line breaks and tabs included) becomes one space; control and
    invisible format characters (NUL, zero-width space, ...) are removed. Letters and
    their accents are left as given, composed or not."""
    visible = "".join(
        char for char in text
        if char.isspace() or unicodedata.category(char) not in _INVISIBLE_CATEGORIES
    )
    return " ".join(visible.split())


def _category(category: str) -> str:
    if category not in MEMORY_CATEGORIES:
        raise InvalidMemoryItem(
            f"Unknown category '{category}': one of {', '.join(MEMORY_CATEGORIES)}"
        )
    return category
