"""
The vector DB of the knowledge base: one SQLite file with the sqlite-vec extension.

It holds the documents, the text of their chunks and one vector per chunk. All SQL is
here: another vector DB replaces this file. Every method blocks; call them off the event
loop.
"""
import sqlite3
import threading
from pathlib import Path
from typing import Optional

import sqlite_vec

from backend.models import KnowledgeDocument, Passage

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    position INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
_SIGNATURE = "embedding_signature"
# Ids of the chunks a lookup may return: those of the enabled documents.
_ENABLED_CHUNKS = (
    "SELECT chunks.id FROM chunks JOIN documents ON documents.id = chunks.document_id "
    "WHERE documents.enabled = 1"
)
_DOCUMENT_COLUMNS = "id, name, size_bytes, chunk_count, created_at, enabled"


def _document(row: tuple) -> KnowledgeDocument:
    return KnowledgeDocument(*row[:5], enabled=bool(row[5]))


class SqliteVecStore:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._db: sqlite3.Connection
        # One connection shared by the worker threads: one statement group at a time.
        self._lock = threading.Lock()

    def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self._path, check_same_thread=False)
        self._db.enable_load_extension(True)
        sqlite_vec.load(self._db)
        self._db.enable_load_extension(False)
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)
            # A store created before documents could be switched off: all enabled.
            columns = [row[1] for row in self._db.execute("PRAGMA table_info(documents)")]
            if "enabled" not in columns:
                self._db.execute(
                    "ALTER TABLE documents ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
                )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ── Documents ─────────────────────────────────────────────────────────

    def add_document(
        self,
        document: KnowledgeDocument,
        chunks: list[str],
        vectors: list[list[float]],
        signature: str,
    ) -> None:
        """Store a document with its chunks and their vectors, replacing any document of
        the same name. All or nothing."""
        with self._lock, self._db:
            for (existing,) in self._db.execute(
                "SELECT id FROM documents WHERE name = ?", (document.name,)
            ).fetchall():
                self._delete(existing)
            self._db.execute(
                f"INSERT INTO documents ({_DOCUMENT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
                (document.id, document.name, document.size_bytes, len(chunks),
                 document.created_at, int(document.enabled)),
            )
            self._create_vectors(len(vectors[0]))
            for position, (text, vector) in enumerate(zip(chunks, vectors)):
                chunk_id = self._db.execute(
                    "INSERT INTO chunks (document_id, position, text) VALUES (?, ?, ?)",
                    (document.id, position, text),
                ).lastrowid
                self._insert_vector(chunk_id, vector)
            self._set_signature(signature)

    def delete_document(self, document_id: str) -> bool:
        """False if there is no such document."""
        with self._lock, self._db:
            return self._delete(document_id)

    def set_enabled(self, document_id: str, enabled: bool) -> Optional[KnowledgeDocument]:
        """Put a document in or out of the lookups. None if there is no such document."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE documents SET enabled = ? WHERE id = ?", (int(enabled), document_id)
            )
            row = self._db.execute(
                f"SELECT {_DOCUMENT_COLUMNS} FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        return _document(row) if row else None

    def list_documents(self) -> list[KnowledgeDocument]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_DOCUMENT_COLUMNS} FROM documents ORDER BY created_at DESC, name"
            ).fetchall()
        return [_document(row) for row in rows]

    def count_chunks(self, enabled_only: bool = False) -> int:
        """Chunks in the store; with `enabled_only`, those a lookup can return."""
        query = "SELECT COUNT(*) FROM chunks"
        if enabled_only:
            query += f" WHERE id IN ({_ENABLED_CHUNKS})"
        with self._lock:
            return self._db.execute(query).fetchone()[0]

    # ── Search ────────────────────────────────────────────────────────────

    def search(self, vector: list[float], limit: int) -> list[Passage]:
        """The `limit` chunks nearest to the vector among the enabled documents, best
        first. The filter is part of the search, so disabled documents do not use up
        places in the result."""
        with self._lock:
            if not self._has_vectors():
                return []
            rows = self._db.execute(
                "SELECT chunks.text, documents.name, nearest.distance "
                "FROM (SELECT rowid, distance FROM vec_chunks "
                "      WHERE embedding MATCH ? AND k = ? "
                f"      AND rowid IN ({_ENABLED_CHUNKS})) AS nearest "
                "JOIN chunks ON chunks.id = nearest.rowid "
                "JOIN documents ON documents.id = chunks.document_id "
                "ORDER BY nearest.distance",
                (sqlite_vec.serialize_float32(vector), limit),
            ).fetchall()
        # Cosine distance → similarity.
        return [Passage(text, name, 1.0 - distance) for text, name, distance in rows]

    # ── Re-indexing with another embedding ────────────────────────────────

    @property
    def signature(self) -> str:
        """Signature of the embedder that built the vectors; "" for a new store."""
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM meta WHERE key = ?", (_SIGNATURE,)
            ).fetchone()
        return row[0] if row else ""

    def chunk_texts(self) -> list[tuple[int, str]]:
        with self._lock:
            return self._db.execute("SELECT id, text FROM chunks ORDER BY id").fetchall()

    def replace_vectors(self, signature: str, vectors: list[tuple[int, list[float]]]) -> None:
        """Drop every vector and store these (chunk id, vector) pairs, built by the
        embedder of `signature`. Their dimension may differ from the old one."""
        with self._lock, self._db:
            self._db.execute("DROP TABLE IF EXISTS vec_chunks")
            if vectors:
                self._create_vectors(len(vectors[0][1]))
            for chunk_id, vector in vectors:
                self._insert_vector(chunk_id, vector)
            self._set_signature(signature)

    # ── Internals (the caller holds the lock) ─────────────────────────────

    def _has_vectors(self) -> bool:
        return self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'vec_chunks'"
        ).fetchone() is not None

    def _create_vectors(self, dimension: int) -> None:
        # The dimension is part of the table: it is an int of ours, not user input.
        self._db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks "
            f"USING vec0(embedding float[{int(dimension)}] distance_metric=cosine)"
        )

    def _insert_vector(self, chunk_id: int, vector: list[float]) -> None:
        self._db.execute(
            "INSERT INTO vec_chunks (rowid, embedding) VALUES (?, ?)",
            (chunk_id, sqlite_vec.serialize_float32(vector)),
        )

    def _set_signature(self, signature: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (_SIGNATURE, signature)
        )

    def _delete(self, document_id: str) -> bool:
        if self._has_vectors():
            self._db.execute(
                "DELETE FROM vec_chunks WHERE rowid IN "
                "(SELECT id FROM chunks WHERE document_id = ?)",
                (document_id,),
            )
        self._db.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        deleted = self._db.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return deleted.rowcount > 0
