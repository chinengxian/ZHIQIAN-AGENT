from __future__ import annotations

import logging
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from agent_api.knowledge.application.citations import Source
from agent_api.knowledge.wiki.retrieval import (
    WikiRetrievalService,
    _ClaimRecord,
    _PageRecord,
    _build_metadata_statement,
    _escape_like_pattern,
)


def _source(chunk_id: UUID, rank: int, text: str) -> Source:
    return Source(
        citation_id=f"[{rank}]",
        chunk_id=chunk_id,
        document_id=uuid4(),
        document_version_id=uuid4(),
        title="认证规范",
        filename="auth.md",
        page_start=None,
        page_end=None,
        heading_path=("认证",),
        excerpt=text,
        score=1 / rank,
        rank=rank,
    )


class FakeSourceRetrieval:
    def __init__(self, sources: list[Source] | Exception) -> None:
        self.sources = sources

    async def search_candidates(
        self,
        query: str,
        scope_ids: tuple[UUID, ...],
        *,
        mode: str,
        limit: int,
    ) -> tuple[list[Source], bool]:
        del query, scope_ids, mode, limit
        if isinstance(self.sources, Exception):
            raise self.sources
        return self.sources, False


class ReverseReranker:
    async def rerank(self, query: str, texts: list[str]) -> list[int]:
        del query
        return list(reversed(range(len(texts))))


class FailingReranker:
    async def rerank(self, query: str, texts: list[str]) -> list[int]:
        del query, texts
        raise RuntimeError("reranker offline")


class StubWikiRetrieval(WikiRetrievalService):
    def __init__(
        self,
        pages: dict[UUID, _PageRecord],
        metadata: list[UUID],
        source_pages: dict[UUID, UUID],
        source_retrieval: FakeSourceRetrieval,
        reranker: ReverseReranker | FailingReranker | None = None,
    ) -> None:
        super().__init__(None, source_retrieval, reranker)  # type: ignore[arg-type]
        self.pages = pages
        self.metadata = metadata
        self.source_pages = source_pages

    async def _metadata_page_ids(
        self,
        query: str,
        scope_ids: tuple[UUID, ...],
    ) -> tuple[list[UUID], dict[UUID, tuple[str, ...]]]:
        del query, scope_ids
        return self.metadata, {page_id: ("title",) for page_id in self.metadata}

    async def _source_page_ids(
        self,
        sources: list[Source],
        scope_ids: tuple[UUID, ...],
    ) -> list[UUID]:
        del scope_ids
        return [self.source_pages[source.chunk_id] for source in sources]

    async def _hydrate_pages(
        self,
        page_ids: list[UUID],
        scope_ids: tuple[UUID, ...],
    ) -> dict[UUID, _PageRecord]:
        del scope_ids
        return {page_id: self.pages[page_id] for page_id in page_ids}


def _page(
    page_id: UUID,
    base_id: UUID,
    title: str,
    claims: tuple[_ClaimRecord, ...],
) -> _PageRecord:
    return _PageRecord(
        id=page_id,
        knowledge_base_id=base_id,
        slug=f"topic/{page_id}",
        title=title,
        page_type="topic",
        summary=f"{title}摘要",
        aliases=(),
        claims=claims,
    )


async def test_search_ranks_globally_and_returns_relevant_claim_after_eighth() -> None:
    kb_a, kb_b = uuid4(), uuid4()
    page_a, page_b = uuid4(), uuid4()
    irrelevant_source = _source(uuid4(), 2, "普通说明")
    matched_source = _source(uuid4(), 1, "双因素校验要求两个独立认证因素")
    pages = {
        page_a: _page(
            page_a,
            kb_a,
            "一般说明",
            (_ClaimRecord(uuid4(), "普通事实", 0, "summary_fact", (irrelevant_source,)),),
        ),
        page_b: _page(
            page_b,
            kb_b,
            "身份认证",
            (
                _ClaimRecord(
                    uuid4(),
                    "排在前面的普通事实",
                    0,
                    "summary_fact",
                    (irrelevant_source,),
                ),
                _ClaimRecord(
                    uuid4(),
                    "第九条但最相关的结论：需要两个独立认证因素",
                    9,
                    "summary_fact",
                    (matched_source,),
                ),
            ),
        ),
    }
    retrieval = StubWikiRetrieval(
        pages,
        [page_a],
        {matched_source.chunk_id: page_b},
        FakeSourceRetrieval([matched_source]),
        ReverseReranker(),
    )

    result = await retrieval.search(
        "双因素校验要求",
        (kb_a, kb_b),
        mode="hybrid",
        page_limit=5,
        claim_limit=12,
    )

    assert result.pages[0].knowledge_base_id == kb_b
    assert result.pages[0].matched_claims[0].position == 9
    assert result.pages[0].matched_fields == ("source_chunk",)
    assert result.degraded is False


async def test_source_failure_returns_metadata_candidates_with_reason() -> None:
    base_id, page_id = uuid4(), uuid4()
    source = _source(uuid4(), 1, "元数据事实")
    retrieval = StubWikiRetrieval(
        {
            page_id: _page(
                page_id,
                base_id,
                "身份认证",
                (_ClaimRecord(uuid4(), "认证事实", 0, "summary_fact", (source,)),),
            )
        },
        [page_id],
        {},
        FakeSourceRetrieval(RuntimeError("milvus offline")),
    )

    result = await retrieval.search("身份认证", (base_id,))

    assert [page.id for page in result.pages] == [page_id]
    assert result.degraded is True
    assert result.degradation_reason == "source_retrieval_unavailable"


async def test_reranker_failure_keeps_rrf_order() -> None:
    base_id, first_id, second_id = uuid4(), uuid4(), uuid4()
    source = _source(uuid4(), 1, "事实")
    pages = {
        first_id: _page(
            first_id,
            base_id,
            "第一",
            (_ClaimRecord(uuid4(), "第一事实", 0, "summary_fact", (source,)),),
        ),
        second_id: _page(
            second_id,
            base_id,
            "第二",
            (_ClaimRecord(uuid4(), "第二事实", 0, "summary_fact", (source,)),),
        ),
    }
    retrieval = StubWikiRetrieval(
        pages,
        [first_id, second_id],
        {},
        FakeSourceRetrieval([]),
        FailingReranker(),
    )

    result = await retrieval.search("事实", (base_id,))

    assert [page.id for page in result.pages] == [first_id, second_id]
    assert result.degraded is True
    assert result.degradation_reason == "reranker_unavailable"


async def test_character_budget_reports_omitted_claims() -> None:
    base_id, page_id = uuid4(), uuid4()
    source = _source(uuid4(), 1, "预算证据")
    claims = tuple(
        _ClaimRecord(uuid4(), f"事实 {index} " + "长" * 30, index, "summary_fact", (source,))
        for index in range(4)
    )
    retrieval = StubWikiRetrieval(
        {page_id: _page(page_id, base_id, "预算", claims)},
        [page_id],
        {},
        FakeSourceRetrieval([]),
    )

    result = await retrieval.search("事实", (base_id,), character_budget=50)

    assert len(result.pages[0].matched_claims) == 1
    assert result.pages[0].omitted_claim_count == 3
    assert result.truncation_reason == "character_budget"


async def test_search_logs_fingerprint_without_plain_query(caplog: object) -> None:
    base_id = uuid4()
    retrieval = StubWikiRetrieval({}, [], {}, FakeSourceRetrieval([]))
    secret_query = "内部机密双因素策略"

    with caplog.at_level(logging.INFO, logger="agent_api.knowledge.wiki.retrieval"):  # type: ignore[attr-defined]
        await retrieval.search(secret_query, (base_id,))

    rendered = caplog.text  # type: ignore[attr-defined]
    assert "query_fingerprint" in rendered
    assert secret_query not in rendered


async def test_search_rejects_punctuation_only_query() -> None:
    retrieval = StubWikiRetrieval({}, [], {}, FakeSourceRetrieval([]))

    with pytest.raises(ValueError, match="invalid_wiki_query"):
        await retrieval.search("%_---", (uuid4(),))


def test_metadata_statement_escapes_slug_wildcards_and_orders_before_limit() -> None:
    assert _escape_like_pattern("vpn_%\\") == "vpn\\_\\%\\\\"
    statement = _build_metadata_statement("vpn_2026", (uuid4(),))
    sql = str(statement.compile(dialect=postgresql.dialect())).upper()

    assert " ESCAPE " in sql
    assert sql.index("ORDER BY") < sql.index("LIMIT")
