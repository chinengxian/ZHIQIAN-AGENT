from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from agent_api.core.config import WikiSearchBackend
from agent_api.knowledge.infrastructure.milvus.wiki_index import WikiIndexHit
from agent_api.knowledge.wiki.cursor import WikiCursorError
from agent_api.knowledge.wiki.independent_retrieval import WikiIndependentRetrievalService
from agent_api.knowledge.wiki.read_model import (
    VerifiedWikiClaim,
    VerifiedWikiSource,
    WikiMetadataHit,
)
from agent_api.knowledge.wiki.retrieval import (
    WikiMatchedPage,
    WikiRetrievalService,
    WikiSearchResult,
)

BASE_ID = UUID("00000000-0000-0000-0000-000000000101")


def _uuid(value: int) -> UUID:
    return UUID(int=value)


def _source(chunk_id: UUID | None = None) -> VerifiedWikiSource:
    return VerifiedWikiSource(
        document_id=_uuid(9001),
        document_version_id=_uuid(9002),
        chunk_id=chunk_id or _uuid(9003),
        parent_chunk_id=None,
        document_title="认证规范",
        filename="auth.md",
        heading_path=("认证",),
        page_start=1,
        page_end=1,
        excerpt="必须启用 MFA。",
        focus_start=0,
        focus_end=6,
        valid=True,
        invalid_reason=None,
    )


def _claim(
    claim_id: UUID,
    page_id: UUID,
    page_version_id: UUID,
    text: str,
    *,
    page_title: str = "身份认证",
    page_type: str = "topic",
    position: int = 0,
    source: VerifiedWikiSource | None = None,
    valid: bool = True,
    invalid_reason: str | None = None,
) -> VerifiedWikiClaim:
    return VerifiedWikiClaim(
        claim_id=claim_id,
        page_id=page_id,
        page_version_id=page_version_id,
        page_title=page_title,
        page_slug=f"topic/{page_id}",
        page_type=page_type,
        claim_key=f"claim-{claim_id.int}",
        claim_type="summary_fact",
        text=text,
        position=position,
        trust_state="verified",
        reason=None,
        sources=(source or _source(),),
        valid=valid,
        invalid_reason=invalid_reason,
    )


def _hit(
    unit_id: UUID,
    claim: VerifiedWikiClaim,
    *,
    score: float,
    channel: str,
    page_version_id: UUID | None = None,
) -> WikiIndexHit:
    return WikiIndexHit(
        unit_id=unit_id,
        page_id=claim.page_id,
        page_version_id=page_version_id or claim.page_version_id,
        claim_id=claim.claim_id,
        unit_type="claim",
        content=claim.text,
        score=score,
        channel=channel,  # type: ignore[arg-type]
    )


class FakeEmbedder:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[list[str]] = []

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        assert texts == ["mfa"]
        self.calls.append(texts)
        if self.fail:
            raise RuntimeError("embedding_unavailable")
        return [[0.1, 0.2]]


class FakeIndex:
    def __init__(
        self,
        dense_hits: list[WikiIndexHit],
        keyword_hits: list[WikiIndexHit],
    ) -> None:
        self.dense_hits = dense_hits
        self.keyword_hits = keyword_hits
        self.dense_calls = 0
        self.keyword_calls = 0

    async def search_dense(
        self, vector: list[float], scope_ids: tuple[UUID, ...], limit: int
    ) -> list[WikiIndexHit]:
        self.dense_calls += 1
        assert vector == [0.1, 0.2]
        assert scope_ids == (BASE_ID,)
        assert limit == 100
        return self.dense_hits

    async def search_keyword(
        self, query: str, scope_ids: tuple[UUID, ...], limit: int
    ) -> list[WikiIndexHit]:
        self.keyword_calls += 1
        assert query == "mfa"
        assert scope_ids == (BASE_ID,)
        assert limit == 100
        return self.keyword_hits


class FakeReadModel:
    def __init__(
        self,
        claims: dict[UUID, VerifiedWikiClaim],
        metadata_hits: tuple[WikiMetadataHit, ...] = (),
        metadata_claims: dict[UUID, tuple[UUID, ...]] | None = None,
    ) -> None:
        self.claims = claims
        self.metadata_hits = metadata_hits
        self.metadata_claims = metadata_claims or {}

    async def search_metadata(
        self, query: str, scope_ids: tuple[UUID, ...], limit: int
    ) -> tuple[WikiMetadataHit, ...]:
        del query, scope_ids, limit
        return self.metadata_hits

    async def load_verified_claims(
        self,
        claim_ids: tuple[UUID, ...],
        scope_ids: tuple[UUID, ...],
        require_index_ready: bool,
    ) -> tuple[VerifiedWikiClaim, ...]:
        assert scope_ids == (BASE_ID,)
        assert require_index_ready is True
        return tuple(self.claims[claim_id] for claim_id in claim_ids if claim_id in self.claims)

    async def metadata_claim_ids(
        self,
        page_ids: tuple[UUID, ...],
        scope_ids: tuple[UUID, ...],
        limit: int,
    ) -> dict[UUID, tuple[UUID, ...]]:
        del scope_ids, limit
        return {page_id: self.metadata_claims.get(page_id, ()) for page_id in page_ids}


class FakeScorer:
    def __init__(self, scores: list[float] | None = None) -> None:
        self.scores = scores

    async def score(self, query: str, texts: list[str]) -> list[float]:
        del query
        if self.scores is not None:
            return self.scores
        return [0.9 if "MFA" in text else 0.1 for text in texts]


def _service(
    *,
    dense_hits: list[WikiIndexHit],
    keyword_hits: list[WikiIndexHit],
    claims: dict[UUID, VerifiedWikiClaim],
    metadata_hits: tuple[WikiMetadataHit, ...] = (),
    metadata_claims: dict[UUID, tuple[UUID, ...]] | None = None,
    scorer: FakeScorer | None = None,
    index_backlog_provider: object | None = None,
    index: FakeIndex | None = None,
    embedder: FakeEmbedder | None = None,
) -> WikiIndependentRetrievalService:
    extra_kwargs: dict[str, object] = {}
    if index_backlog_provider is not None:
        extra_kwargs["index_backlog_provider"] = index_backlog_provider
    return WikiIndependentRetrievalService(
        index=index or FakeIndex(dense_hits, keyword_hits),
        embedder=embedder or FakeEmbedder(),
        read_model=FakeReadModel(claims, metadata_hits, metadata_claims),
        collection_version="wiki_units_v1",
        dense_threshold=0.55,
        rerank_threshold=0.5,
        keyword_coverage_threshold=0.5,
        scorer=scorer,
        **extra_kwargs,
    )


async def test_independent_search_fuses_dense_keyword_alias_and_rejects_low_relevance() -> None:
    page_id, version_id, claim_id, unit_id = _uuid(101), _uuid(102), _uuid(103), _uuid(104)
    accepted_claim = _claim(claim_id, page_id, version_id, "MFA 必须启用两个独立认证因素")
    accepted = await _service(
        dense_hits=[_hit(unit_id, accepted_claim, score=0.82, channel="dense")],
        keyword_hits=[_hit(unit_id, accepted_claim, score=4.2, channel="keyword")],
        claims={claim_id: accepted_claim},
        metadata_hits=(WikiMetadataHit(page_id, True, "alias"),),
        metadata_claims={page_id: (claim_id,)},
    ).search("MFA", (BASE_ID,), page_limit=5, claim_limit=3)

    assert accepted.pages[0].matched_fields == ("dense", "keyword", "alias")

    weak_claim = _claim(_uuid(203), _uuid(201), _uuid(202), "普通流程说明")
    result = await _service(
        dense_hits=[_hit(_uuid(204), weak_claim, score=0.2, channel="dense")],
        keyword_hits=[],
        claims={weak_claim.claim_id: weak_claim},
    ).search("MFA", (BASE_ID,), page_limit=5, claim_limit=3)

    assert result.no_answer is True
    assert result.no_answer_reason == "below_relevance_threshold"
    assert result.pages == ()


async def test_keyword_mode_skips_embedding_and_dense_search() -> None:
    claim = _claim(_uuid(1601), _uuid(1602), _uuid(1603), "MFA 关键字命中")
    index = FakeIndex([], [_hit(_uuid(1604), claim, score=4.0, channel="keyword")])
    embedder = FakeEmbedder(fail=True)

    result = await _service(
        dense_hits=[],
        keyword_hits=[],
        claims={claim.claim_id: claim},
        index=index,
        embedder=embedder,
    ).search("MFA", (BASE_ID,), mode="keyword")

    assert [page.id for page in result.pages] == [claim.page_id]
    assert result.pages[0].matched_fields == ("keyword",)
    assert embedder.calls == []
    assert index.dense_calls == 0
    assert index.keyword_calls == 1


async def test_semantic_mode_does_not_return_keyword_only_hits() -> None:
    claim = _claim(_uuid(1611), _uuid(1612), _uuid(1613), "MFA 关键字命中")
    index = FakeIndex([], [_hit(_uuid(1614), claim, score=4.0, channel="keyword")])

    result = await _service(
        dense_hits=[],
        keyword_hits=[],
        claims={claim.claim_id: claim},
        index=index,
    ).search("MFA", (BASE_ID,), mode="semantic")

    assert result.pages == ()
    assert result.no_answer is True
    assert index.dense_calls == 1
    assert index.keyword_calls == 0


async def test_independent_search_uses_seek_cursor_without_repeating_pages() -> None:
    claims: dict[UUID, VerifiedWikiClaim] = {}
    hits: list[WikiIndexHit] = []
    for index in range(3):
        claim = _claim(
            _uuid(300 + index),
            _uuid(400 + index),
            _uuid(500 + index),
            f"MFA 第 {index} 条要求",
            page_title=f"认证 {index}",
        )
        claims[claim.claim_id] = claim
        hits.append(_hit(_uuid(600 + index), claim, score=0.9 - index * 0.01, channel="dense"))
    service = _service(dense_hits=hits, keyword_hits=[], claims=claims)

    first_page = await service.search("MFA", (BASE_ID,), page_limit=1, claim_limit=1)
    second_page = await service.search(
        "MFA", (BASE_ID,), page_limit=1, claim_limit=1, cursor=first_page.next_cursor
    )

    assert first_page.next_cursor is not None
    assert second_page.pages[0].id not in {page.id for page in first_page.pages}


async def test_independent_search_cursor_skips_returned_page_claims() -> None:
    page_id = _uuid(1001)
    version_id = _uuid(1002)
    claims: dict[UUID, VerifiedWikiClaim] = {}
    hits: list[WikiIndexHit] = []
    for index in range(13):
        claim = _claim(
            _uuid(1100 + index),
            page_id,
            version_id,
            f"MFA 同页第 {index} 条要求",
            page_title="身份认证",
            position=index,
        )
        claims[claim.claim_id] = claim
        hits.append(_hit(_uuid(1200 + index), claim, score=0.99 - index * 0.01, channel="dense"))
    next_claim = _claim(
        _uuid(1301),
        _uuid(1302),
        _uuid(1303),
        "MFA 其他页面要求",
        page_title="访问控制",
        position=0,
    )
    claims[next_claim.claim_id] = next_claim
    hits.append(_hit(_uuid(1304), next_claim, score=0.7, channel="dense"))
    service = _service(dense_hits=hits, keyword_hits=[], claims=claims)

    first_page = await service.search("MFA", (BASE_ID,), page_limit=1, claim_limit=12)
    second_page = await service.search(
        "MFA", (BASE_ID,), page_limit=1, claim_limit=12, cursor=first_page.next_cursor
    )

    assert first_page.next_cursor is not None
    assert [page.id for page in first_page.pages] == [page_id]
    assert [page.id for page in second_page.pages] == [next_claim.page_id]
    first_claim_ids = {claim.id for page in first_page.pages for claim in page.matched_claims}
    second_claim_ids = {claim.id for page in second_page.pages for claim in page.matched_claims}
    assert first_claim_ids.isdisjoint(second_claim_ids)


async def test_independent_search_pages_after_promoted_exact_metadata_page() -> None:
    exact_claim = _claim(
        _uuid(1501),
        _uuid(1502),
        _uuid(1503),
        "精确标题页面要求",
        page_title="MFA",
    )
    high_score_claim = _claim(
        _uuid(1511),
        _uuid(1512),
        _uuid(1513),
        "MFA 高分非精确页面要求",
        page_title="访问控制",
    )
    service = _service(
        dense_hits=[
            _hit(_uuid(1514), high_score_claim, score=0.95, channel="dense"),
            _hit(_uuid(1504), exact_claim, score=0.6, channel="dense"),
        ],
        keyword_hits=[],
        claims={
            exact_claim.claim_id: exact_claim,
            high_score_claim.claim_id: high_score_claim,
        },
        metadata_hits=(WikiMetadataHit(exact_claim.page_id, True, "title"),),
        metadata_claims={exact_claim.page_id: (exact_claim.claim_id,)},
    )

    first_page = await service.search("MFA", (BASE_ID,), page_limit=1, claim_limit=2)
    second_page = await service.search(
        "MFA", (BASE_ID,), page_limit=1, claim_limit=2, cursor=first_page.next_cursor
    )

    assert [page.id for page in first_page.pages] == [exact_claim.page_id]
    assert first_page.next_cursor is not None
    assert [page.id for page in second_page.pages] == [high_score_claim.page_id]
    assert second_page.next_cursor is None


async def test_independent_search_tie_break_is_stable_after_uuid_regeneration() -> None:
    async def first_page_title(
        *,
        alpha_page_id: UUID,
        beta_page_id: UUID,
        alpha_unit_id: UUID,
        beta_unit_id: UUID,
    ) -> str:
        alpha = replace(
            _claim(
                _uuid(1911),
                alpha_page_id,
                _uuid(1912),
                "MFA 统一要求",
                page_title="甲页面",
            ),
            page_slug="topic/alpha",
            claim_key="stable-alpha",
        )
        beta = replace(
            _claim(
                _uuid(1921),
                beta_page_id,
                _uuid(1922),
                "MFA 统一要求补充",
                page_title="乙页面",
            ),
            page_slug="topic/beta",
            claim_key="stable-beta",
        )
        result = await _service(
            dense_hits=[
                _hit(alpha_unit_id, alpha, score=0.9, channel="dense"),
                _hit(beta_unit_id, beta, score=0.9, channel="dense"),
            ],
            keyword_hits=[],
            claims={alpha.claim_id: alpha, beta.claim_id: beta},
            scorer=FakeScorer([0.9, 0.9]),
        ).search("MFA", (BASE_ID,), page_limit=1, claim_limit=2)
        return result.pages[0].title

    first_build = await first_page_title(
        alpha_page_id=_uuid(2002),
        beta_page_id=_uuid(2001),
        alpha_unit_id=_uuid(2004),
        beta_unit_id=_uuid(2003),
    )
    rebuilt = await first_page_title(
        alpha_page_id=_uuid(2101),
        beta_page_id=_uuid(2102),
        alpha_unit_id=_uuid(2103),
        beta_unit_id=_uuid(2104),
    )

    assert first_build == rebuilt == "甲页面"


async def test_independent_search_distributes_claim_budget_across_pages() -> None:
    crowded_page_id = _uuid(2201)
    crowded_version_id = _uuid(2202)
    claims: dict[UUID, VerifiedWikiClaim] = {}
    hits: list[WikiIndexHit] = []
    for index in range(8):
        claim = replace(
            _claim(
                _uuid(2210 + index),
                crowded_page_id,
                crowded_version_id,
                f"MFA 甲页面要求 {index}",
                page_title="甲页面",
                position=index,
            ),
            page_slug="topic/alpha",
        )
        claims[claim.claim_id] = claim
        hits.append(
            _hit(_uuid(2230 + index), claim, score=0.9, channel="dense")
        )
    second_page_claim = replace(
        _claim(
            _uuid(2251),
            _uuid(2252),
            _uuid(2253),
            "MFA 乙页面补充要求",
            page_title="乙页面",
        ),
        page_slug="topic/beta",
    )
    claims[second_page_claim.claim_id] = second_page_claim
    hits.append(_hit(_uuid(2254), second_page_claim, score=0.9, channel="dense"))

    result = await _service(
        dense_hits=hits,
        keyword_hits=[],
        claims=claims,
        scorer=FakeScorer([0.9] * len(hits)),
    ).search("MFA", (BASE_ID,), page_limit=2, claim_limit=8)

    assert [page.title for page in result.pages] == ["甲页面", "乙页面"]
    assert sum(len(page.matched_claims) for page in result.pages) == 8


async def test_independent_search_overfetches_candidates_before_reranking() -> None:
    claim = _claim(_uuid(2301), _uuid(2302), _uuid(2303), "MFA 必须启用")

    class LimitRecordingIndex(FakeIndex):
        def __init__(self) -> None:
            super().__init__([_hit(_uuid(2304), claim, score=0.9, channel="dense")], [])
            self.requested_limit: int | None = None

        async def search_dense(
            self,
            vector: list[float],
            scope_ids: tuple[UUID, ...],
            limit: int,
        ) -> list[WikiIndexHit]:
            del vector, scope_ids
            self.requested_limit = limit
            return self.dense_hits

    index = LimitRecordingIndex()
    await _service(
        dense_hits=[],
        keyword_hits=[],
        claims={claim.claim_id: claim},
        index=index,
    ).search("MFA", (BASE_ID,), mode="semantic", page_limit=5, claim_limit=8)

    assert index.requested_limit == 100


async def test_independent_search_records_backlog_metric(caplog: pytest.LogCaptureFixture) -> None:
    claim = _claim(_uuid(1401), _uuid(1402), _uuid(1403), "MFA 必须启用")
    caplog.set_level(logging.INFO, logger="agent_api.knowledge")

    await _service(
        dense_hits=[_hit(_uuid(1404), claim, score=0.9, channel="dense")],
        keyword_hits=[],
        claims={claim.claim_id: claim},
        index_backlog_provider=lambda: 7,
    ).search("MFA", (BASE_ID,), page_limit=5, claim_limit=3)

    events = [json.loads(record.getMessage()) for record in caplog.records]
    search_event = next(event for event in events if event["event"] == "wiki_search")
    assert search_event["index_backlog_count"] == 7
    assert "MFA" not in caplog.text


async def test_independent_search_degrades_on_non_finite_reranker_score() -> None:
    claim = _claim(_uuid(1701), _uuid(1702), _uuid(1703), "MFA 必须启用")

    result = await _service(
        dense_hits=[_hit(_uuid(1704), claim, score=0.9, channel="dense")],
        keyword_hits=[],
        claims={claim.claim_id: claim},
        scorer=FakeScorer([float("nan")]),
    ).search("MFA", (BASE_ID,), page_limit=5, claim_limit=3)

    assert result.degraded is True
    assert result.degradation_reason == "reranker_unavailable"
    assert [page.id for page in result.pages] == [claim.page_id]


async def test_independent_search_rejects_expired_cursor_without_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="agent_api.knowledge")
    service = _service(dense_hits=[], keyword_hits=[], claims={})

    with pytest.raises(WikiCursorError):
        await service.search("MFA", (BASE_ID,), cursor="expired")

    assert caplog.records == []


class _Rows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _MetadataSession:
    def __init__(self, high_page_id: UUID, high_claim_id: UUID, low_page_id: UUID) -> None:
        self.high_page_id = high_page_id
        self.high_claim_id = high_claim_id
        self.low_page_id = low_page_id
        self.page_queries = 0

    async def execute(self, statement: object) -> _Rows:
        sql = str(statement)
        if "wiki_pages.id IN" in sql:
            return _Rows([(self.low_page_id, _uuid(1801)), (self.low_page_id, _uuid(1802))])
        self.page_queries += 1
        if self.page_queries == 1:
            return _Rows([(self.high_page_id, self.high_claim_id)])
        return _Rows([(self.low_page_id, _uuid(1801))])


class _MetadataSessionContext:
    def __init__(self, session: _MetadataSession) -> None:
        self._session = session

    async def __aenter__(self) -> _MetadataSession:
        return self._session

    async def __aexit__(self, *exc_info: object) -> None:
        return None


async def test_metadata_claim_expansion_preserves_metadata_hit_priority() -> None:
    high_page_id = _uuid(1811)
    low_page_id = _uuid(1812)
    high_claim_id = _uuid(1813)
    session = _MetadataSession(high_page_id, high_claim_id, low_page_id)
    service = WikiIndependentRetrievalService(
        index=FakeIndex([], []),
        embedder=FakeEmbedder(),
        sessions=lambda: _MetadataSessionContext(session),  # type: ignore[arg-type]
        collection_version="wiki_units_v1",
        dense_threshold=0.55,
        rerank_threshold=0.5,
        keyword_coverage_threshold=0.5,
    )

    by_page = await service._metadata_claim_ids(  # noqa: SLF001
        (high_page_id, low_page_id),
        (BASE_ID,),
        candidate_limit=2,
    )

    assert by_page[high_page_id] == (high_claim_id,)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda claim, hit: (
                replace(hit, page_version_id=uuid4()),
                claim,
            ),
            "old_index_version",
        ),
        (
            lambda claim, hit: (
                hit,
                replace(claim, valid=False, invalid_reason="page_index_not_ready"),
            ),
            "index_state_not_ready",
        ),
        (
            lambda claim, hit: (
                hit,
                replace(claim, valid=False, invalid_reason="document_not_ready"),
            ),
            "invalid_source",
        ),
        (
            lambda claim, hit: (
                hit,
                replace(claim, valid=False, invalid_reason="chunk_hash_mismatch"),
            ),
            "hash_mismatch",
        ),
    ],
)
async def test_independent_search_drops_untrusted_hits(mutate: object, reason: str) -> None:
    del reason
    claim = _claim(_uuid(701), _uuid(702), _uuid(703), "MFA 需要可信来源")
    hit = _hit(_uuid(704), claim, score=0.9, channel="dense")
    mutated_hit, mutated_claim = mutate(claim, hit)  # type: ignore[operator]

    result = await _service(
        dense_hits=[mutated_hit],
        keyword_hits=[],
        claims={mutated_claim.claim_id: mutated_claim},
    ).search("MFA", (BASE_ID,), page_limit=5, claim_limit=3)

    assert result.pages == ()


async def test_independent_search_deduplicates_facts_and_reports_other_pages() -> None:
    shared_source = _source(_uuid(801))
    first = _claim(_uuid(811), _uuid(812), _uuid(813), "MFA 必须启用", source=shared_source)
    duplicate = _claim(
        _uuid(821),
        _uuid(822),
        _uuid(823),
        "MFA 必须启用",
        page_title="认证镜像",
        source=shared_source,
    )

    result = await _service(
        dense_hits=[
            _hit(_uuid(831), first, score=0.9, channel="dense"),
            _hit(_uuid(832), duplicate, score=0.8, channel="dense"),
        ],
        keyword_hits=[],
        claims={first.claim_id: first, duplicate.claim_id: duplicate},
    ).search("MFA", (BASE_ID,), page_limit=5, claim_limit=5)

    assert len(result.pages) == 1
    assert result.pages[0].matched_claims[0].also_in_pages[0].id == duplicate.page_id


async def test_shadow_backend_returns_legacy_result_and_records_page_diff() -> None:
    legacy_result = WikiSearchResult(
        (
            WikiMatchedPage(
                id=_uuid(901),
                knowledge_base_id=BASE_ID,
                slug="topic/legacy",
                title="旧结果",
                page_type="topic",
                score=1.0,
                matched_fields=("source_chunk",),
                matched_claims=(),
                omitted_claim_count=0,
            ),
        ),
        False,
        None,
        None,
    )
    independent_result = WikiSearchResult(
        (
            WikiMatchedPage(
                id=_uuid(902),
                knowledge_base_id=BASE_ID,
                slug="topic/independent",
                title="新结果",
                page_type="topic",
                score=1.0,
                matched_fields=("dense",),
                matched_claims=(),
                omitted_claim_count=0,
            ),
        ),
        False,
        None,
        None,
    )

    class Legacy:
        async def search(self, *args: object, **kwargs: object) -> WikiSearchResult:
            return legacy_result

    class Independent:
        async def search(self, *args: object, **kwargs: object) -> WikiSearchResult:
            return independent_result

    service = WikiRetrievalService(
        cast(Any, Legacy()),
        cast(Any, Independent()),
        backend=WikiSearchBackend.SHADOW,
    )

    shadow_result = await service.search("MFA", (BASE_ID,))
    shadow_metrics = service.last_shadow_metrics

    assert shadow_result == legacy_result
    assert shadow_metrics is not None
    assert shadow_metrics.independent_page_ids != shadow_metrics.legacy_page_ids
