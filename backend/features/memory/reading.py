"""
What the jobs that read for the memory share: an import (`imports.py`) and a closed
conversation (`consolidation.py`) are both given to the LLM as material between
delimiters, both keep a quote only when the material really holds it, and both wait for
a store call that is running before they act on what it wrote.
"""
import asyncio
import re
import unicodedata
from typing import Callable, Optional, TypeVar

from backend.features.memory.memory import one_line

T = TypeVar("T")

# The name of the tags of a delimiter template, read from its opening tag.
_TAG_NAME = re.compile(r"<\s*(\w+)\s*>")


def as_document(template: str, text: str) -> str:
    """The text as the LLM is given it: between the two tags of `template`
    (`<name>\\n{text}\\n</name>`), so that it is read as material and not as
    instructions. Every writing of those tags is taken out of the text first (opening or
    closing, any case, spaces inside), again and again until none is left: removing one
    can join the pieces of another. A text cannot close the block it is given in."""
    name = _TAG_NAME.search(template).group(1)
    tag = re.compile(rf"<\s*/?\s*{re.escape(name)}\s*>", re.IGNORECASE)
    stripped = tag.sub("", text)
    while stripped != text:
        text, stripped = stripped, tag.sub("", stripped)
    return template.format(text=text)


def comparable(text: str) -> str:
    """The text as two writings of it are compared: on one line, letters composed."""
    return unicodedata.normalize("NFC", one_line(text))


def quote(evidence: Optional[str], material: str) -> Optional[str]:
    """The quote an item is said to come from, on one line, when the material (given as
    `comparable`) really holds it. The LLM may invent a quote, or be led by the material
    to write something else there: such an item is kept without evidence."""
    found = one_line(evidence or "")
    if not found or comparable(found) not in material:
        return None
    return found


async def finish(call: Callable[..., T], *args) -> T:
    """Run a blocking call of a store in a thread and wait for its end, also when the
    caller is cancelled meanwhile. A thread cannot be stopped: the cancellation is raised
    once the call is over, so what it wrote is acted on after it, never while it runs."""
    task = asyncio.ensure_future(asyncio.to_thread(call, *args))
    dropped = False
    while not task.done():
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError:
            dropped = True
    if dropped:
        if not task.cancelled():
            task.exception()  # read, so that it is not reported as never retrieved
        raise asyncio.CancelledError
    return task.result()
