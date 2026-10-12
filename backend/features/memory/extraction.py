"""
What the LLM answered to an extraction request → validated memory items. Pure: no I/O,
no clock, no store.

The LLM is asked for a JSON array and the answer is parsed as exactly that: a code fence
or a sentence around the array makes the whole answer invalid. Inside a valid array each
entry stands alone: one that names no known category or holds no acceptable text is
dropped and the others are kept.
"""
import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from backend.models import MEMORY_CATEGORIES


class InvalidExtraction(ValueError):
    """The answer is not the JSON array that was asked for."""


@dataclass(frozen=True)
class ExtractedItem:
    """One thing to remember about the person, with the quote it comes from."""
    category: str
    text: str
    evidence: Optional[str] = None


def parse_items(answer: str, clean_text: Callable[[str], str]) -> list[ExtractedItem]:
    """The items of an answer shaped `[{"category", "text", "evidence"}, ...]`, in its
    order. `clean_text` is the rule every item text goes through (it returns the text as
    it is stored, or raises ValueError): an entry that does not pass is dropped. An empty
    array gives no item. Raises InvalidExtraction when the answer is not a JSON array."""
    items = []
    for entry in _entries(answer):
        item = _item(entry, clean_text)
        if item is not None:
            items.append(item)
    return items


def _entries(answer: str) -> list[Any]:
    """The entries of the JSON array the answer must be, whatever they hold."""
    try:
        parsed = json.loads(answer)
    except ValueError as e:
        raise InvalidExtraction(f"The answer is not JSON: {e}") from e
    if not isinstance(parsed, list):
        raise InvalidExtraction(f"The answer is a JSON {type(parsed).__name__}, not an array")
    return parsed


def _item(entry: Any, clean_text: Callable[[str], str]) -> Optional[ExtractedItem]:
    """The entry as an item, or None when it cannot be one."""
    if not isinstance(entry, dict):
        return None
    category, text = entry.get("category"), entry.get("text")
    if category not in MEMORY_CATEGORIES or not isinstance(text, str):
        return None
    try:
        text = clean_text(text)
    except ValueError:
        return None
    return ExtractedItem(category, text, _evidence(entry.get("evidence")))


def _evidence(quote: Any) -> Optional[str]:
    """The quote, when the answer gives one."""
    if not isinstance(quote, str) or not quote.strip():
        return None
    return quote.strip()
