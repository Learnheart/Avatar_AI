"""
KnowledgeBase — the documents the avatar can look things up in.

Upload: file → text → chunks → vectors → store. Search: question → vector → the nearest
chunks. The embedder and the store are given by `backend/main.py`.
"""
import asyncio
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import PurePath
from typing import Optional

from backend.config import AppConfig
from backend.core.contracts import Embedder, EmbeddingError, Knowledge, KnowledgeError
from backend.features.knowledge.chunking import split_text
from backend.features.knowledge.loaders import extract_text
from backend.features.knowledge.store import SqliteVecStore
from backend.models import KnowledgeDocument, Passage
from backend.utils.logging import get_logger
from backend.utils.telemetry import tracer

logger = get_logger("knowledge")


class EmptyDocument(ValueError):
    """The file holds no text to index (empty, or a scanned PDF)."""


class KnowledgeBase(Knowledge):
    def __init__(self, config: AppConfig, embedder: Embedder, store: SqliteVecStore) -> None:
        self.config = config
        self._embedder = embedder
        self._store = store

    async def start(self) -> None:
        """Open the store. Vectors built by another embedder are rebuilt from the stored
        chunk texts, so changing the embedding does not lose the uploaded documents."""
        await asyncio.to_thread(self._store.open)
        built_with = await asyncio.to_thread(lambda: self._store.signature)
        if built_with and built_with != self._embedder.signature:
            await self._reindex(built_with)

    async def close(self) -> None:
        await asyncio.to_thread(self._store.close)

    # ── Documents ─────────────────────────────────────────────────────────

    async def add_document(self, filename: str, data: bytes) -> KnowledgeDocument:
        """Index one uploaded file, selected for the lookups; a document of the same name
        is replaced.
        Raises UnsupportedDocument, UnreadableDocument, EmptyDocument (bad file) or
        KnowledgeError (the embedding or the store failed; nothing is stored)."""
        name = PurePath(filename.replace("\\", "/")).name.strip()
        with tracer.start_as_current_span(
            "knowledge.ingest", attributes={"document.name": name, "document.bytes": len(data)}
        ) as span:
            document = await self._index(name, data)
            span.set_attribute("document.chunks", document.chunk_count)
            return document

    async def _index(self, name: str, data: bytes) -> KnowledgeDocument:
        text = await asyncio.to_thread(extract_text, name, data)
        chunks = split_text(
            text, self.config.KNOWLEDGE_CHUNK_CHARS, self.config.KNOWLEDGE_CHUNK_OVERLAP_CHARS
        )
        if not chunks:
            raise EmptyDocument(f"'{name}' holds no text to index")

        document = KnowledgeDocument(
            id=uuid.uuid4().hex,
            name=name,
            size_bytes=len(data),
            chunk_count=len(chunks),
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        started = time.perf_counter()
        try:
            vectors = await self._embedder.embed(chunks)
            await asyncio.to_thread(
                self._store.add_document, document, chunks, vectors, self._embedder.signature
            )
        except (EmbeddingError, sqlite3.Error) as e:
            raise KnowledgeError(f"Cannot index '{name}': {e}") from e
        logger.info({
            "msg": "Document indexed",
            "name": name,
            "chunks": len(chunks),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        })
        return document

    async def list_documents(self) -> list[KnowledgeDocument]:
        return await asyncio.to_thread(self._store.list_documents)

    async def set_enabled(self, document_id: str, enabled: bool) -> Optional[KnowledgeDocument]:
        """Choose whether the avatar answers from this document. None if there is no
        such document."""
        document = await asyncio.to_thread(self._store.set_enabled, document_id, enabled)
        if document:
            logger.info({"msg": "Document selection changed", "id": document_id, "enabled": enabled})
        return document

    async def delete_document(self, document_id: str) -> bool:
        """False if there is no such document."""
        deleted = await asyncio.to_thread(self._store.delete_document, document_id)
        if deleted:
            logger.info({"msg": "Document deleted", "id": document_id})
        return deleted

    # ── Search ────────────────────────────────────────────────────────────

    async def search(self, query: str) -> list[Passage]:
        try:
            # Nothing uploaded or nothing selected: no embedding call, the turn pays nothing.
            if not await asyncio.to_thread(self._store.count_chunks, True):
                return []
            (vector,) = await self._embedder.embed([query])
            nearest = await asyncio.to_thread(
                self._store.search, vector, self.config.KNOWLEDGE_TOP_K
            )
        except (EmbeddingError, sqlite3.Error) as e:
            raise KnowledgeError(f"Knowledge search failed: {e}") from e
        # Written as `>=` so a NaN score (a query with no usable vector) is dropped.
        return [p for p in nearest if p.score >= self.config.KNOWLEDGE_MIN_SCORE]

    # ── Internals ─────────────────────────────────────────────────────────

    async def _reindex(self, built_with: str) -> None:
        chunks = await asyncio.to_thread(self._store.chunk_texts)
        logger.info({
            "msg": "Embedding changed, re-indexing the knowledge base",
            "from": built_with,
            "to": self._embedder.signature,
            "chunks": len(chunks),
        })
        try:
            vectors = await self._embedder.embed([text for _, text in chunks])
        except EmbeddingError as e:
            raise KnowledgeError(
                f"The knowledge base was indexed with '{built_with}' and could not be "
                f"re-indexed with '{self._embedder.signature}': {e}"
            ) from e
        await asyncio.to_thread(
            self._store.replace_vectors,
            self._embedder.signature,
            [(chunk_id, vector) for (chunk_id, _), vector in zip(chunks, vectors)],
        )
