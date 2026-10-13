"""
What the LLM answered to the reading of a description of the avatar → validated persona
fields. Pure: no I/O, no clock, no store.

The LLM is asked for one JSON object and the answer is parsed as exactly that: a code
fence or a sentence around the object makes the whole answer invalid. Inside a valid
object each field stands alone: a key that is not a field, or a value that is no
acceptable text, is dropped and the others are kept.
"""
import json

from backend.features.memory.memory import one_line
from backend.models import PERSONA_FIELDS


class InvalidPersonaAnswer(ValueError):
    """The answer is not the JSON object that was asked for."""


def parse_fields(answer: str, max_chars: int) -> dict[str, str]:
    """The fields of an answer shaped `{"name": "...", "addressing": "...", ...}`, in
    the order of PERSONA_FIELDS, each value on one line. Dropped: a key that is not a
    field, a value that is not text, an empty one, one longer than `max_chars`. An
    empty object gives no field. Raises InvalidPersonaAnswer when the answer is not a
    JSON object."""
    try:
        parsed = json.loads(answer)
    except ValueError as e:
        raise InvalidPersonaAnswer(f"The answer is not JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise InvalidPersonaAnswer(
            f"The answer is a JSON {type(parsed).__name__}, not an object"
        )
    fields = {}
    for name in PERSONA_FIELDS:
        value = parsed.get(name)
        if not isinstance(value, str):
            continue
        value = one_line(value)
        if value and len(value) <= max_chars:
            fields[name] = value
    return fields
