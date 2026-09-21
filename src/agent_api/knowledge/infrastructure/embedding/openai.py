from __future__ import annotations

from typing import Protocol

from langchain_openai import OpenAIEmbeddings

from agent_api.core.config import Settings


class EmbeddingClient(Protocol):
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingDimensionError(RuntimeError):
    pass


class OpenAIEmbeddingAdapter:
    """按配置批量调用 OpenAI-compatible Embedding，并校验向量维度。"""

    def __init__(
        self,
        client: EmbeddingClient,
        *,
        dimension: int,
        batch_size: int,
    ) -> None:
        self._client = client
        self._dimension = dimension
        self._batch_size = batch_size

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            batch_vectors = await self._client.aembed_documents(batch)
            if len(batch_vectors) != len(batch) or any(
                len(vector) != self._dimension for vector in batch_vectors
            ):
                raise EmbeddingDimensionError("embedding_dimension_mismatch")
            vectors.extend(batch_vectors)
        return vectors


def create_embedding_adapter(settings: Settings) -> OpenAIEmbeddingAdapter:
    if settings.embedding_base_url is None or settings.embedding_api_key is None:
        raise ValueError("embedding configuration is incomplete")
    if settings.embedding_model is None:
        raise ValueError("embedding model is missing")
    client = OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.embedding_api_key,
        base_url=str(settings.embedding_base_url),
        dimensions=settings.embedding_dimension,
    )
    return OpenAIEmbeddingAdapter(
        client,
        dimension=settings.embedding_dimension,
        batch_size=settings.embedding_batch_size,
    )
