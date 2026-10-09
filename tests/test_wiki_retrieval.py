from __future__ import annotations

import logging
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from agent_api.knowledge.application.citations import Source
from agent_api.knowledge.infrastructure.database.models import Chunk
from agent_api.knowledge.wiki.retrieval import (
    WikiRetrievalService,
    _build_metadata_statement,
    _ClaimRecord,
    _escape_like_pattern,
    _PageRecord,
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


class _Rows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _HydrateSession:
    def __init__(self, batches: list[list[object]], claim_ids: list[UUID]) -> None:
        self._batches = batches
        self._claim_ids = claim_ids
        self.execute_count = 0

    async def execute(self, statement: object) -> _Rows:
        del statement
        rows = self._batches[self.execute_count]
        self.execute_count += 1
        return _Rows(rows)

    async def scalars(self, statement: object) -> list[UUID]:
        del statement
        return self._claim_ids


class _HydrateSessionContext:
    def __init__(self, session: _HydrateSession) -> None:
        self._session = session

    async def __aenter__(self) -> _HydrateSession:
        return self._session

    async def __aexit__(self, *exc_info: object) -> None:
        return None


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


async def test_read_page_and_ranking_exclude_presentation_sections() -> None:
    """可读摘要段落只用于页面展示，不能进入 Agent 或搜索事实。"""

    base_id, page_id = uuid4(), uuid4()
    source = _source(uuid4(), 1, "唯一身份与 MFA")
    section = _ClaimRecord(uuid4(), "这是可读概述", 0, "summary_section", (source,))
    fact = _ClaimRecord(uuid4(), "特权操作必须使用 MFA", 1, "summary_fact", (source,))
    retrieval = StubWikiRetrieval(
        {page_id: _page(page_id, base_id, "身份规范", (section, fact))},
        [page_id],
        {source.chunk_id: page_id},
        FakeSourceRetrieval([source]),
    )

    ranked = retrieval.rank_page_claims("MFA", (section, fact))
    page = await retrieval.read_page(base_id, page_id)

    assert [claim.claim_type for claim, _ in ranked] == ["summary_fact"]
    assert [claim.claim_type for claim in page["claims"]] == ["summary_fact"]


async def test_hydrate_pages_uses_precise_parent_excerpt_for_legacy_sources() -> None:
    base_id, page_id, version_id = uuid4(), uuid4(), uuid4()
    document_id, document_version_id = uuid4(), uuid4()
    parent = Chunk(
        id=uuid4(),
        document_version_id=document_version_id,
        ordinal=0,
        kind="parent",
        content="前文。必须启用两个独立认证因素。后文。",
        token_count=10,
    )
    child = Chunk(
        id=uuid4(),
        document_version_id=document_version_id,
        parent_chunk_id=parent.id,
        ordinal=1,
        kind="child",
        content="必须启用两个独立认证因素。",
        char_start=3,
        char_end=15,
        token_count=10,
    )
    claim = SimpleNamespace(
        id=uuid4(),
        page_version_id=version_id,
        claim_key="mfa",
        text="必须启用 MFA",
        trust_state="verified",
        reason=None,
        position=0,
        claim_type="summary_fact",
    )
    source = SimpleNamespace(
        id=uuid4(),
        claim_id=claim.id,
        document_id=document_id,
        document_version_id=document_version_id,
        chunk_id=child.id,
        chunk_kind="child",
        chunk_ordinal=1,
        content_sha256=sha256(child.content.encode("utf-8")).hexdigest(),
        document_title="认证规范",
        heading_path=["认证"],
        page_start=1,
        page_end=1,
    )
    document = SimpleNamespace(
        id=document_id,
        title="认证规范",
        filename="auth.md",
        status="ready",
        deleted_at=None,
        active_version_id=document_version_id,
        knowledge_base_id=base_id,
    )
    page = SimpleNamespace(
        id=page_id,
        knowledge_base_id=base_id,
        slug="topic/auth",
        title="身份认证",
        page_type="topic",
        current_version_id=version_id,
    )
    version = SimpleNamespace(id=version_id, summary="摘要", index_state="ready")
    main_row = SimpleNamespace(
        page=page,
        version=version,
        claim=claim,
        source=source,
        document=document,
        chunk=child,
    )
    session = _HydrateSession(
        batches=[[(page, version)], [], [main_row], [(parent,)]],
        claim_ids=[claim.id],
    )
    retrieval = WikiRetrievalService(
        lambda: _HydrateSessionContext(session), FakeSourceRetrieval([])
    )  # type: ignore[arg-type]

    records = await retrieval._hydrate_pages([page_id], (base_id,))

    hydrated_source = records[page_id].claims[0].sources[0]
    assert session.execute_count == 4
    assert hydrated_source.parent_chunk_id == parent.id
    assert hydrated_source.excerpt == parent.content
    assert parent.content[hydrated_source.focus_start : hydrated_source.focus_end] == child.content


async def test_hydrate_pages_filters_cross_scope_document_sources_for_legacy_search() -> None:
    base_id, page_id, version_id = uuid4(), uuid4(), uuid4()
    document_id, document_version_id = uuid4(), uuid4()
    child = Chunk(
        id=uuid4(),
        document_version_id=document_version_id,
        ordinal=1,
        kind="child",
        content="跨知识库来源不应进入搜索结果。",
        token_count=10,
    )
    claim = SimpleNamespace(
        id=uuid4(),
        page_version_id=version_id,
        claim_key="scope",
        text="搜索只能返回同知识库来源",
        trust_state="verified",
        reason=None,
        position=0,
        claim_type="summary_fact",
    )
    source = SimpleNamespace(
        id=uuid4(),
        claim_id=claim.id,
        document_id=document_id,
        document_version_id=document_version_id,
        chunk_id=child.id,
        chunk_kind="child",
        chunk_ordinal=1,
        content_sha256=sha256(child.content.encode("utf-8")).hexdigest(),
        document_title="外部规范",
        heading_path=["外部"],
        page_start=1,
        page_end=1,
    )
    document = SimpleNamespace(
        id=document_id,
        title="外部规范",
        filename="external.md",
        status="ready",
        deleted_at=None,
        active_version_id=document_version_id,
        knowledge_base_id=uuid4(),
    )
    page = SimpleNamespace(
        id=page_id,
        knowledge_base_id=base_id,
        slug="topic/scope",
        title="范围隔离",
        page_type="topic",
        current_version_id=version_id,
    )
    version = SimpleNamespace(id=version_id, summary="摘要", index_state="ready")
    main_row = SimpleNamespace(
        page=page,
        version=version,
        claim=claim,
        source=source,
        document=document,
        chunk=child,
    )
    session = _HydrateSession(
        batches=[[(page, version)], [], [main_row], []],
        claim_ids=[claim.id],
    )
    retrieval = WikiRetrievalService(
        lambda: _HydrateSessionContext(session), FakeSourceRetrieval([])
    )  # type: ignore[arg-type]

    records = await retrieval._hydrate_pages([page_id], (base_id,))

    assert records[page_id].claims == ()


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
    order_by = sql.split("ORDER BY", 1)[1].split("LIMIT", 1)[0]

    assert " ESCAPE " in sql
    assert sql.index("ORDER BY") < sql.index("LIMIT")
    assert "LIKE" in order_by
