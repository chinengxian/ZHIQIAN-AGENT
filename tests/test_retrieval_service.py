from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from agent_api.knowledge.application.citations import Source
from agent_api.knowledge.application.retrieval import KnowledgeRetrievalService
from agent_api.knowledge.infrastructure.milvus.index import SearchHit


class RecordingChunkIndex:
    def __init__(self, hits: list[SearchHit]) -> None:
        self.hits = hits
        self.last_limit: int | None = None

    async def search(
        self,
        query: str,
        *,
        vector: list[float] | None,
        knowledge_base_ids: tuple[UUID, ...],
        mode: str,
        limit: int = 20,
    ) -> list[SearchHit]:
        del query, vector, knowledge_base_ids, mode
        self.last_limit = limit
        return self.hits[:limit]


class StubEmbedder:
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        assert texts
        return [[0.1, 0.2]]


class CandidateRetrievalService(KnowledgeRetrievalService):
    def __init__(self, index: RecordingChunkIndex, sources: dict[UUID, Source]) -> None:
        super().__init__(None, index, StubEmbedder())  # type: ignore[arg-type]
        self._sources = sources

    async def _fetch_sources(
        self,
        hits: list[SearchHit],
        scope_ids: tuple[UUID, ...],
        *,
        deduplicate_parents: bool,
    ) -> list[Source]:
        del scope_ids, deduplicate_parents
        return [self._sources[hit.chunk_id] for hit in hits]


def _source(chunk_id: UUID, rank: int) -> Source:
    return Source(
        citation_id=f"[{rank}]",
        chunk_id=chunk_id,
        document_id=uuid4(),
        document_version_id=uuid4(),
        title=f"文档 {rank}",
        filename=f"doc-{rank}.txt",
        page_start=None,
        page_end=None,
        heading_path=(),
        excerpt=f"身份认证证据 {rank}",
        score=1 / rank,
        rank=rank,
    )


async def test_search_candidates_uses_expanded_pool_without_public_limit() -> None:
    base_id = uuid4()
    chunk_ids = [uuid4() for _ in range(50)]
    hits = [
        SearchHit(chunk_id, uuid4(), base_id, 1 / rank)
        for rank, chunk_id in enumerate(chunk_ids, 1)
    ]
    sources = {chunk_id: _source(chunk_id, rank) for rank, chunk_id in enumerate(chunk_ids, 1)}
    index = RecordingChunkIndex(hits)
    service = CandidateRetrievalService(index, sources)

    result, degraded = await service.search_candidates(
        "身份认证",
        (base_id,),
        mode="hybrid",
        limit=40,
    )

    assert len(result) == 40
    assert index.last_limit == 80
    assert degraded is False
    assert result[-1].chunk_id == chunk_ids[39]


async def test_public_search_keeps_eight_result_contract() -> None:
    service = CandidateRetrievalService(RecordingChunkIndex([]), {})

    with pytest.raises(ValueError, match="invalid_search_options"):
        await service.search("身份认证", (uuid4(),), limit=9)


async def test_search_candidates_rejects_out_of_range_limit() -> None:
    service = CandidateRetrievalService(RecordingChunkIndex([]), {})

    with pytest.raises(ValueError, match="invalid_candidate_limit"):
        await service.search_candidates("身份认证", (uuid4(),), limit=101)
