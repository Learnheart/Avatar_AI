"""Cut the text of a document into the chunks that are embedded and retrieved."""
import re
from typing import Iterator

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
# Inside a paragraph too long for one chunk: after . ! ? …, or at a line break.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+|\n+")


def _squash(text: str) -> str:
    return " ".join(text.split())


def _units(text: str, size: int) -> Iterator[str]:
    """The pieces a chunk is assembled from, none longer than `size`: whole paragraphs,
    or the sentences of a long paragraph, or slices of a sentence that is still too long."""
    for paragraph in _PARAGRAPH_BREAK.split(text):
        whole = _squash(paragraph)
        if len(whole) <= size:
            pieces = [whole]
        else:
            pieces = [
                sentence[start:start + size]
                for sentence in map(_squash, _SENTENCE_BREAK.split(paragraph))
                for start in range(0, len(sentence), size)
            ]
        yield from filter(None, pieces)


def split_text(text: str, size: int, overlap: int) -> list[str]:
    """Pack the text into chunks of at most `size` characters. A chunk starts with the
    last pieces of the previous one, up to `overlap` characters, so a fact that straddles
    two chunks is whole in one of them."""
    chunks: list[str] = []
    current: list[str] = []
    for unit in _units(text, size):
        if current and len("\n".join(current)) + 1 + len(unit) > size:
            chunks.append("\n".join(current))
            tail: list[str] = []
            for previous in reversed(current):
                carried = len("\n".join([previous] + tail))
                if carried > overlap or carried + 1 + len(unit) > size:
                    break
                tail.insert(0, previous)
            current = tail
        current.append(unit)
    if current:
        chunks.append("\n".join(current))
    return chunks
