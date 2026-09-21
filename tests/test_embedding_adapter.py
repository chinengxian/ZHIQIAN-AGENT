import pytest

from agent_api.knowledge.infrastructure.embedding.openai import (
    EmbeddingDimensionError,
    OpenAIEmbeddingAdapter,
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
    adapter = OpenAIEmbeddingAdapter(client, dimension=2, batch_size=2)  # type: ignore[arg-type]

    vectors = await adapter.embed_documents(["一", "二", "三"])

    assert client.batches == [["一", "二"], ["三"]]
    assert vectors == [[0.0, 0.5], [1.0, 0.5], [0.0, 0.5]]


async def test_embedding_adapter_rejects_dimension_mismatch() -> None:
    client = FakeEmbeddingClient(dimension=1)
    adapter = OpenAIEmbeddingAdapter(client, dimension=2, batch_size=2)  # type: ignore[arg-type]

    with pytest.raises(EmbeddingDimensionError, match="embedding_dimension_mismatch"):
        await adapter.embed_documents(["正文"])
