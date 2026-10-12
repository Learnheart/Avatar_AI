"""
What the LLM answered to an extraction request → validated memory items (an import) or
validated operations on the items (a conversation). Pure: no I/O, no clock, no store.

The LLM is asked for a JSON array and the answer is parsed as exactly that: a code fence
or a sentence around the array makes the whole answer invalid. Inside a valid array each
entry stands alone: one that names no known category or holds no acceptable text (or,
for an operation, an item that does not exist) is dropped and the others are kept.
"""
import json
from dataclasses import dataclass
from typing import Any, Callable, Collection, Optional

from backend.models import MEMORY_CATEGORIES


class InvalidExtraction(ValueError):
    """The answer is not the JSON array that was asked for."""


@dataclass(frozen=True)
class ExtractedItem:
    """One thing to remember about the person, with the quote it comes from."""
    category: str
    text: str
    evidence: Optional[str] = None


# What a conversation may do to the items: propose a new one, state an existing one
# again, contradict an existing one.
OP_ADD, OP_CONFIRM, OP_REPLACE = "add", "confirm", "replace"


@dataclass(frozen=True)
class Operation:
    """One thing a conversation does to the memory items. `id` names an existing item
    (`confirm`, `replace`), as the request listed it; `item` is what to remember (`add`,
    `replace`)."""
    op: str
    id: Optional[str] = None
    item: Optional[ExtractedItem] = None


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


def parse_operations(
    answer: str, clean_text: Callable[[str], str], known_ids: Collection[str]
) -> list[Operation]:
    """The operations of an answer shaped `[{"op": "add", "category", "text",
    "evidence"}, {"op": "confirm", "id"}, {"op": "replace", "id", "category", "text",
    "evidence"}]`, in its order. `known_ids` are the ids the request listed, the only
    ones an operation may name; an id may be answered as a string or as a number. An
    entry that is no such operation is dropped: an unknown `op`, an unknown id, or an
    item that `parse_items` would drop. Raises InvalidExtraction when the answer is not
    a JSON array."""
    operations = []
    for entry in _entries(answer):
        operation = _operation(entry, clean_text, known_ids)
        if operation is not None:
            operations.append(operation)
    return operations


def _operation(
    entry: Any, clean_text: Callable[[str], str], known_ids: Collection[str]
) -> Optional[Operation]:
    """The entry as an operation, or None when it cannot be one."""
    if not isinstance(entry, dict):
        return None
    op = entry.get("op")
    if op not in (OP_ADD, OP_CONFIRM, OP_REPLACE):
        return None
    item_id = item = None
    if op != OP_ADD:
        item_id = _id(entry.get("id"))
        if item_id not in known_ids:
            return None
    if op != OP_CONFIRM:
        item = _item(entry, clean_text)
        if item is None:
            return None
    return Operation(op, item_id, item)


def _id(value: Any) -> Optional[str]:
    """The id an entry names, as text; a number is accepted for it, nothing else."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return str(value).strip()


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
