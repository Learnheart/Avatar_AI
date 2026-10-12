"""
Embedding backend computed locally, with no model: hashed word unigrams and bigrams.

Each word and each pair of neighbouring words adds to one of `EMBEDDING_HASH_DIM`
buckets; the vector is then scaled to unit length. Two texts are close when they share
words — it matches wording, not meaning. It needs no download, no network and no key, so
the knowledge flow runs on any machine; use a real model for retrieval quality.
"""
import math
import re
import unicodedata
import zlib

from backend.config import AppConfig
from backend.core.contracts import Embedder

_WORD = re.compile(r"\w+")


class LocalHashEmbedder(Embedder):
    def __init__(self, config: AppConfig) -> None:
        self._dim = config.EMBEDDING_HASH_DIM

    @property
    def signature(self) -> str:
        return f"local_hash:{self._dim}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        words = _WORD.findall(unicodedata.normalize("NFC", text).lower())
        counts: dict[int, float] = {}
        for term in words + [f"{a} {b}" for a, b in zip(words, words[1:])]:
            # crc32 is stable across processes; the built-in hash() is salted per run.
            bucket = zlib.crc32(term.encode("utf-8")) % self._dim
            counts[bucket] = counts.get(bucket, 0.0) + 1.0

        vector = [0.0] * self._dim
        # Square root: a word repeated many times does not drown the others.
        norm = math.sqrt(sum(counts.values()))
        for bucket, count in counts.items():
            vector[bucket] = math.sqrt(count) / norm
        return vector
