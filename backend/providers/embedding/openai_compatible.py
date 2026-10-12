"""
Embedding backend for any OpenAI-compatible `/embeddings` API.

Serves hosted APIs and self-hosted servers, and the embedding endpoints of a Databricks
workspace: the endpoint, the key and the model are passed in by
`backend/providers/catalog.py`.
"""
from typing import Awaitable, Callable, Union

from openai import AsyncOpenAI, OpenAIError

from backend.config import AppConfig
from backend.core.contracts import Embedder, EmbeddingError
from backend.utils.errors import summarize_error
from backend.utils.telemetry import tracer


class OpenAICompatibleEmbedder(Embedder):
    def __init__(
        self,
        config: AppConfig,
        *,
        base_url: str,
        api_key: Union[str, Callable[[], Awaitable[str]]],
        model: str,
    ) -> None:
        self.config = config
        self._model = model
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=config.EMBEDDING_TIMEOUT_SEC,
            max_retries=config.GROQ_MAX_RETRIES,
        )

    @property
    def signature(self) -> str:
        return f"openai_compatible:{self._model}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        with tracer.start_as_current_span(
            "embedding", attributes={"embedding.model": self._model, "embedding.texts": len(texts)}
        ):
            return await self._embed(texts)

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        size = self.config.EMBEDDING_BATCH_SIZE
        try:
            for start in range(0, len(texts), size):
                response = await self._client.embeddings.create(
                    model=self._model,
                    input=texts[start:start + size],
                    # Plain floats: not every server implements the SDK's base64 default.
                    encoding_format="float",
                )
                # `index` is the position in the request; do not rely on the order.
                batch = sorted(response.data, key=lambda item: item.index)
                vectors.extend(list(item.embedding) for item in batch)
        except OpenAIError as e:
            summary = summarize_error(e, self.config.ERROR_SUMMARY_MAX_CHARS)
            raise EmbeddingError(f"Embedding request failed: {summary}") from e
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"Embedding returned {len(vectors)} vectors for {len(texts)} texts"
            )
        return vectors
