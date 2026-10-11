from __future__ import annotations

from dataclasses import replace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from agent_api.core.config import WikiSearchBackend
from agent_api.knowledge.infrastructure.milvus.wiki_index import WikiIndexHit
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
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        assert texts == ["mfa"]
        return [[0.1, 0.2]]


class FakeIndex:
    def __init__(
        self,
        dense_hits: list[WikiIndexHit],
        keyword_hits: list[WikiIndexHit],
    ) -> None:
        self.dense_hits = dense_hits
        self.keyword_hits = keyword_hits

    async def search_dense(
        self, vector: list[float], scope_ids: tuple[UUID, ...], limit: int
    ) -> list[WikiIndexHit]:
        assert vector == [0.1, 0.2]
        assert scope_ids == (BASE_ID,)
        assert limit == 50
        return self.dense_hits

    async def search_keyword(
        self, query: str, scope_ids: tuple[UUID, ...], limit: int
    ) -> list[WikiIndexHit]:
        assert query == "mfa"
        assert scope_ids == (BASE_ID,)
        assert limit == 50
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
    async def score(self, query: str, texts: list[str]) -> list[float]:
        del query
        return [0.9 if "MFA" in text else 0.1 for text in texts]


def _service(
    *,
    dense_hits: list[WikiIndexHit],
    keyword_hits: list[WikiIndexHit],
    claims: dict[UUID, VerifiedWikiClaim],
    metadata_hits: tuple[WikiMetadataHit, ...] = (),
    metadata_claims: dict[UUID, tuple[UUID, ...]] | None = None,
    scorer: FakeScorer | None = None,
) -> WikiIndependentRetrievalService:
    return WikiIndependentRetrievalService(
        index=FakeIndex(dense_hits, keyword_hits),
        embedder=FakeEmbedder(),
        read_model=FakeReadModel(claims, metadata_hits, metadata_claims),
        collection_version="wiki_units_v1",
        dense_threshold=0.55,
        rerank_threshold=0.5,
        keyword_coverage_threshold=0.5,
        scorer=scorer,
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
