import sys
from types import ModuleType

import pytest
from pydantic import SecretStr

import agent_api.knowledge.infrastructure.embedding.embedding as embedding_module
from agent_api.core.config import EmbeddingProvider, Settings
from agent_api.knowledge.infrastructure.embedding.embedding import (
    EmbeddingDimensionError,
    OpenAIEmbeddingAdapter,
    create_embedding_adapter,
)


class FakeEmbeddingClient:
    def __init__(self, *, dimension: int = 2) -> None:
        self.dimension = dimension
        self.batches: list[list[str]] = []

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(texts)
        return [[float(index), 0.5][: self.dimension] for index, _ in enumerate(texts)]


async def test_embedding_adapter_batches_and_preserves_order() -> None:
    client = FakeEmbeddingClient()
    adapter = OpenAIEmbeddingAdapter(client, dimension=2, batch_size=2)

    vectors = await adapter.embed_documents(["一", "二", "三"])

    assert client.batches == [["一", "二"], ["三"]]
    assert vectors == [[0.0, 0.5], [1.0, 0.5], [0.0, 0.5]]


async def test_embedding_adapter_rejects_dimension_mismatch() -> None:
    client = FakeEmbeddingClient(dimension=1)
    adapter = OpenAIEmbeddingAdapter(client, dimension=2, batch_size=2)

    with pytest.raises(EmbeddingDimensionError, match="embedding_dimension_mismatch"):
        await adapter.embed_documents(["正文"])


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "model_provider": "openai",
        "openai_base_url": "https://chat.example.test/v1",
        "openai_api_key": SecretStr("chat-secret"),
        "openai_model": "chat-model",
        "knowledge_enabled": True,
        "database_url": SecretStr("postgresql+asyncpg://agent:secret@db:5432/agent"),
        "redis_url": SecretStr("redis://redis:6379/0"),
        "milvus_uri": "http://milvus:19530",
        "milvus_collection": "knowledge_chunks",
        "embedding_api_key": SecretStr("embedding-secret"),
        "embedding_model": "text-embedding-v1",
        "embedding_dimension": 3,
        "embedding_batch_size": 2,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg,arg-type]


def _install_fake_dashscope(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    created: dict[str, object] = {}

    class FakeDashScopeEmbeddings:
        def __init__(self, *, model: str, dashscope_api_key: str) -> None:
            created["model"] = model
            created["dashscope_api_key"] = dashscope_api_key

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 2.0, 3.0] for _ in texts]

    package = ModuleType("langchain_community")
    embeddings = ModuleType("langchain_community.embeddings")
    dashscope = ModuleType("langchain_community.embeddings.dashscope")
    dashscope.__dict__["DashScopeEmbeddings"] = FakeDashScopeEmbeddings
    monkeypatch.setitem(sys.modules, "langchain_community", package)
    monkeypatch.setitem(sys.modules, "langchain_community.embeddings", embeddings)
    monkeypatch.setitem(sys.modules, "langchain_community.embeddings.dashscope", dashscope)
    return created


async def test_create_embedding_adapter_defaults_to_dashscope_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _install_fake_dashscope(monkeypatch)

    adapter = create_embedding_adapter(_settings(embedding_base_url=None))
    vectors = await adapter.embed_documents(["正文"])

    assert created == {"model": "text-embedding-v1", "dashscope_api_key": "embedding-secret"}
    assert vectors == [[1.0, 2.0, 3.0]]


def test_create_embedding_adapter_can_switch_to_openai_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}

    class FakeOpenAIEmbeddings:
        def __init__(
            self, *, model: str, api_key: SecretStr, base_url: str, dimensions: int
        ) -> None:
            created["model"] = model
            created["api_key"] = api_key.get_secret_value()
            created["base_url"] = base_url
            created["dimensions"] = dimensions

    monkeypatch.setattr(embedding_module, "OpenAIEmbeddings", FakeOpenAIEmbeddings)

    create_embedding_adapter(
        _settings(
            embedding_provider=EmbeddingProvider.OPENAI,
            embedding_base_url="https://embedding.example.test/v1",
            embedding_model="text-embedding-3-small",
            embedding_dimension=1536,
        )
    )

    assert created == {
        "model": "text-embedding-3-small",
        "api_key": "embedding-secret",
        "base_url": "https://embedding.example.test/v1",
        "dimensions": 1536,
    }
