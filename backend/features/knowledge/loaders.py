"""Turn an uploaded file into plain text. The file type is taken from the extension."""
import csv
import io
from pathlib import Path
from typing import Callable

from docx import Document as open_docx
from pypdf import PdfReader


class UnsupportedDocument(ValueError):
    """The file type is not one the knowledge base can read."""


class UnreadableDocument(ValueError):
    """The file is of a supported type but could not be parsed."""


def _plain(data: bytes) -> str:
    # utf-8-sig drops the BOM that Windows editors and Excel put first.
    return data.decode("utf-8-sig", errors="replace")


def _csv(data: bytes) -> str:
    rows = csv.reader(io.StringIO(_plain(data)))
    return "\n".join(", ".join(cell.strip() for cell in row) for row in rows)


def _pdf(data: bytes) -> str:
    # The text layer only: a scanned PDF has none and yields "".
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _docx(data: bytes) -> str:
    document = open_docx(io.BytesIO(data))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        parts.extend(" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows)
    return "\n\n".join(parts)


_LOADERS: dict[str, Callable[[bytes], str]] = {
    ".txt": _plain,
    ".md": _plain,
    ".csv": _csv,
    ".pdf": _pdf,
    ".docx": _docx,
}

SUPPORTED_EXTENSIONS = tuple(_LOADERS)


def extract_text(name: str, data: bytes) -> str:
    extension = Path(name).suffix.lower()
    if extension not in _LOADERS:
        raise UnsupportedDocument(
            f"Unsupported file type '{extension}'. Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
        )
    try:
        return _LOADERS[extension](data)
    except Exception as e:  # the parsers raise their own, unrelated error types
        raise UnreadableDocument(f"Cannot read '{name}': {type(e).__name__}") from e
