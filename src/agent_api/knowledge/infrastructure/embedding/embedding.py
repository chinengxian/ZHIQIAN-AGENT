from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from langchain_openai import OpenAIEmbeddings

from agent_api.core.config import EmbeddingProvider, Settings


class EmbeddingClient(Protocol):
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingDimensionError(RuntimeError):
    pass


class OpenAIEmbeddingAdapter:
    """按配置批量调用 Embedding 客户端，并校验向量维度。"""

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


class EmbeddingStrategy(ABC):
    """封装不同 Embedding 供应商的客户端创建逻辑。"""

    @abstractmethod
    def create_client(self, settings: Settings) -> EmbeddingClient:
        raise NotImplementedError


class OpenAIEmbeddingStrategy(EmbeddingStrategy):
    """创建 OpenAI-compatible Embedding 客户端。"""

    def create_client(self, settings: Settings) -> EmbeddingClient:
        if settings.embedding_base_url is None or settings.embedding_api_key is None:
            raise ValueError("embedding configuration is incomplete")
        if settings.embedding_model is None:
            raise ValueError("embedding model is missing")
        return OpenAIEmbeddings(
            model=settings.embedding_model,
            api_key=settings.embedding_api_key,
            base_url=str(settings.embedding_base_url),
            dimensions=settings.embedding_dimension,
        )


class DashScopeEmbeddingStrategy(EmbeddingStrategy):
    """创建 DashScope 原生 Embedding 客户端。"""

    def create_client(self, settings: Settings) -> EmbeddingClient:
        if settings.embedding_api_key is None:
            raise ValueError("embedding configuration is incomplete")
        if settings.embedding_model is None:
            raise ValueError("embedding model is missing")
        try:
            from langchain_community.embeddings.dashscope import DashScopeEmbeddings
        except ImportError as error:
            raise RuntimeError("dashscope embedding dependency is missing") from error
        return DashScopeEmbeddings(
            model=settings.embedding_model,
            dashscope_api_key=settings.embedding_api_key.get_secret_value(),
        )


_EMBEDDING_STRATEGIES: dict[EmbeddingProvider, EmbeddingStrategy] = {
    EmbeddingProvider.DASHSCOPE: DashScopeEmbeddingStrategy(),
    EmbeddingProvider.OPENAI: OpenAIEmbeddingStrategy(),
}


def create_embedding_adapter(settings: Settings) -> OpenAIEmbeddingAdapter:
    strategy = _EMBEDDING_STRATEGIES[settings.embedding_provider]
    client = strategy.create_client(settings)
    return OpenAIEmbeddingAdapter(
        client,
        dimension=settings.embedding_dimension,
        batch_size=settings.embedding_batch_size,
    )
