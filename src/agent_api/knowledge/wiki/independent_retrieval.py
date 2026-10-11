from __future__ import annotations

import asyncio
import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from time import perf_counter
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_api.knowledge.application.citations import Source
from agent_api.knowledge.infrastructure.database.models import WikiClaim, WikiPage, WikiPageVersion
from agent_api.knowledge.infrastructure.milvus.wiki_index import WikiIndexHit
from agent_api.knowledge.infrastructure.observability.events import record_wiki_search
from agent_api.knowledge.wiki.cursor import (
    SearchCursor,
    WikiCursorError,
    decode_cursor,
    encode_cursor,
)
from agent_api.knowledge.wiki.ranking import (
    WikiScoredCandidate,
    WikiScorer,
    reciprocal_rank_fusion,
)
from agent_api.knowledge.wiki.read_model import (
    VerifiedWikiClaim,
    VerifiedWikiSource,
    WikiMetadataHit,
    WikiReadModel,
)
from agent_api.knowledge.wiki.relevance import WikiRelevancePolicy, fact_fingerprint
from agent_api.knowledge.wiki.retrieval import (
    WikiMatchedClaim,
    WikiMatchedPage,
    WikiMatchedPageSummary,
    WikiRetrievalError,
    WikiSearchResult,
)

RETRIEVABLE_CLAIM_TYPES = ("summary_fact", "topic_fact")


class WikiIndependentIndex(Protocol):
    async def search_dense(
        self, vector: list[float], knowledge_base_ids: tuple[UUID, ...], limit: int
    ) -> list[WikiIndexHit]: ...

    async def search_keyword(
        self, query: str, knowledge_base_ids: tuple[UUID, ...], limit: int
    ) -> list[WikiIndexHit]: ...


class WikiEmbedder(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class WikiReadModelLike(Protocol):
    async def search_metadata(
        self, query: str, scope_ids: tuple[UUID, ...], limit: int
    ) -> tuple[WikiMetadataHit, ...]: ...

    async def load_verified_claims(
        self,
        claim_ids: tuple[UUID, ...],
        scope_ids: tuple[UUID, ...],
        require_index_ready: bool,
    ) -> tuple[VerifiedWikiClaim, ...]: ...


@runtime_checkable
class WikiMetadataClaimsReadModel(WikiReadModelLike, Protocol):
    async def metadata_claim_ids(
        self,
        page_ids: tuple[UUID, ...],
        scope_ids: tuple[UUID, ...],
        limit: int,
    ) -> dict[UUID, tuple[UUID, ...]]: ...


@dataclass(frozen=True, slots=True)
class _Candidate:
    unit_id: UUID
    page_id: UUID
    page_version_id: UUID
    claim_id: UUID
    content: str
    rrf_score: float
    dense_score: float | None
    keyword_score: float | None
    matched_fields: tuple[str, ...]
    exact_metadata_match: bool


@dataclass(frozen=True, slots=True)
class _Accepted:
    candidate: _Candidate
    claim: VerifiedWikiClaim
    score: float
    scored: WikiScoredCandidate
    fingerprint: str


def _normalize_query(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s+", " ", normalized)


def _query_tokens(value: str) -> set[str]:
    normalized = _normalize_query(value)
    return {
        *re.findall(r"[a-z0-9]+", normalized),
        *re.findall(r"[\u3400-\u9fff]", normalized),
    }


def _keyword_coverage(query: str, text: str) -> float:
    query_tokens = _query_tokens(query)
    if not query_tokens:
        return 0.0
    return len(query_tokens & _query_tokens(text)) / len(query_tokens)


def _source_response(source: VerifiedWikiSource) -> Source:
    if source.chunk_id is None:
        raise ValueError("wiki_source_chunk_missing")
    return Source(
        citation_id="",
        chunk_id=source.chunk_id,
        document_id=source.document_id,
        document_version_id=source.document_version_id,
        title=source.document_title,
        filename=source.filename,
        page_start=source.page_start,
        page_end=source.page_end,
        heading_path=source.heading_path,
        excerpt=source.excerpt,
        score=0.0,
        rank=0,
        parent_chunk_id=source.parent_chunk_id,
        focus_start=source.focus_start,
        focus_end=source.focus_end,
        valid=source.valid,
        invalid_reason=source.invalid_reason,
    )


class WikiIndependentRetrievalService:
    """独立 Wiki 索引检索：并行召回、PG 复核、阈值拒答和 seek 分页。"""

    def __init__(
        self,
        *,
        index: WikiIndependentIndex,
        embedder: WikiEmbedder,
        collection_version: str,
        dense_threshold: float,
        rerank_threshold: float,
        keyword_coverage_threshold: float,
        sessions: async_sessionmaker[AsyncSession] | None = None,
        read_model: WikiReadModelLike | None = None,
        scorer: WikiScorer | None = None,
        index_backlog_provider: Callable[[], int] | None = None,
    ) -> None:
        self._sessions = sessions
        self._index = index
        self._embedder = embedder
        self._read_model = read_model
        self._scorer = scorer
        self._index_backlog_provider = index_backlog_provider
        self._collection_version = collection_version
        self._policy = WikiRelevancePolicy(
            dense_threshold=dense_threshold,
            rerank_threshold=rerank_threshold,
            keyword_threshold=keyword_coverage_threshold,
        )

    async def search(
        self,
        query: str,
        scope_ids: tuple[UUID, ...],
        *,
        mode: str = "hybrid",
        page_limit: int = 5,
        claim_limit: int = 12,
        cursor: str | None = None,
    ) -> WikiSearchResult:
        normalized = _normalize_query(query)
        if not normalized or len(normalized) > 200 or not _query_tokens(normalized):
            raise ValueError("invalid_wiki_query")
        if mode not in {"hybrid", "semantic", "keyword"}:
            raise ValueError("invalid_search_options")
        if not 1 <= page_limit <= 20 or not 1 <= claim_limit <= 50:
            raise ValueError("invalid_search_options")
        if not scope_ids:
            return WikiSearchResult(
                (),
                False,
                None,
                None,
                no_answer=True,
                no_answer_reason="empty_scope",
                collection_version=self._collection_version,
            )

        started = perf_counter()
        query_fingerprint = sha256(normalized.encode("utf-8")).hexdigest()[:16]
        decoded_cursor = (
            decode_cursor(
                cursor,
                query_fingerprint=query_fingerprint,
                collection_version=self._collection_version,
            )
            if cursor is not None
            else None
        )
        candidate_limit = max(page_limit * 10, 50)
        try:
            read_model = self._read_model
            if read_model is not None:
                dense_hits, keyword_hits, metadata_hits = await self._recall_candidates(
                    normalized,
                    scope_ids,
                    mode,
                    candidate_limit,
                    read_model,
                )
            else:
                if self._sessions is None:
                    raise RuntimeError("wiki_read_model_unavailable")
                async with self._sessions() as session:
                    read_model = WikiReadModel(session)
                    dense_hits, keyword_hits, metadata_hits = await self._recall_candidates(
                        normalized,
                        scope_ids,
                        mode,
                        candidate_limit,
                        read_model,
                    )
        except WikiCursorError:
            raise
        except Exception as error:
            raise WikiRetrievalError("wiki_retrieval_unavailable") from error

        try:
            candidates = await self._build_candidates(
                normalized,
                scope_ids,
                dense_hits,
                keyword_hits,
                tuple(metadata_hits),
                candidate_limit,
            )
            accepted, degraded, degradation_reason, filtered_count = await self._score_and_filter(
                normalized,
                scope_ids,
                candidates,
            )
        except Exception as error:
            raise WikiRetrievalError("wiki_retrieval_unavailable") from error

        ordered = self._deduplicate(accepted)
        if not ordered:
            self._record_search_event(
                query_fingerprint,
                started,
                len(dense_hits),
                len(keyword_hits),
                len(metadata_hits),
                0,
                filtered_count,
                degraded,
                degradation_reason,
                no_answer=True,
            )
            return WikiSearchResult(
                (),
                degraded,
                degradation_reason,
                None,
                no_answer=True,
                no_answer_reason="below_relevance_threshold",
                collection_version=self._collection_version,
            )

        page_items = self._page_items(ordered)
        if decoded_cursor is not None:
            page_items = [
                (page_id, items, sort_key)
                for page_id, items, sort_key in page_items
                if sort_key > decoded_cursor.last_page_sort_key
            ]
        if not page_items:
            self._record_search_event(
                query_fingerprint,
                started,
                len(dense_hits),
                len(keyword_hits),
                len(metadata_hits),
                0,
                filtered_count,
                degraded,
                degradation_reason,
                no_answer=True,
            )
            return WikiSearchResult(
                (),
                degraded,
                degradation_reason,
                None,
                no_answer=True,
                no_answer_reason="below_relevance_threshold",
                collection_version=self._collection_version,
            )

        pages = self._build_pages(
            page_items,
            accepted,
            scope_ids,
            page_limit=page_limit,
            claim_limit=claim_limit,
        )
        last = self._cursor_boundary_after_pages(page_items, pages)
        next_cursor = None
        if last is not None:
            last_page_id, _, last_sort_key = last
            boundary_index = max(
                index for index, (page_id, _, _) in enumerate(page_items) if page_id == last_page_id
            )
        else:
            boundary_index = -1
        if last is not None and boundary_index + 1 < len(page_items):
            next_cursor = encode_cursor(
                SearchCursor(
                    query_fingerprint=query_fingerprint,
                    collection_version=self._collection_version,
                    last_page_sort_key=last_sort_key,
                    last_page_id=last_page_id,
                )
            )
        self._record_search_event(
            query_fingerprint,
            started,
            len(dense_hits),
            len(keyword_hits),
            len(metadata_hits),
            len(pages),
            filtered_count,
            degraded,
            degradation_reason,
            no_answer=False,
        )
        return WikiSearchResult(
            pages,
            degraded,
            degradation_reason,
            "page_limit" if next_cursor is not None else None,
            no_answer=False,
            next_cursor=next_cursor,
            collection_version=self._collection_version,
        )

    async def _recall_candidates(
        self,
        query: str,
        scope_ids: tuple[UUID, ...],
        mode: str,
        candidate_limit: int,
        read_model: WikiReadModelLike,
    ) -> tuple[list[WikiIndexHit], list[WikiIndexHit], tuple[WikiMetadataHit, ...]]:
        metadata_task = asyncio.create_task(
            read_model.search_metadata(query, scope_ids, candidate_limit)
        )
        keyword_task = (
            asyncio.create_task(self._index.search_keyword(query, scope_ids, candidate_limit))
            if mode in {"hybrid", "keyword"}
            else None
        )
        dense_task = None
        if mode in {"hybrid", "semantic"}:
            vector = (await self._embedder.embed_documents([query]))[0]
            dense_task = asyncio.create_task(
                self._index.search_dense(vector, scope_ids, candidate_limit)
            )
        dense_hits = await dense_task if dense_task is not None else []
        keyword_hits = await keyword_task if keyword_task is not None else []
        metadata_hits = await metadata_task
        return dense_hits, keyword_hits, tuple(metadata_hits)

    async def _build_candidates(
        self,
        query: str,
        scope_ids: tuple[UUID, ...],
        dense_hits: list[WikiIndexHit],
        keyword_hits: list[WikiIndexHit],
        metadata_hits: tuple[WikiMetadataHit, ...],
        candidate_limit: int,
    ) -> list[_Candidate]:
        metadata_units = await self._metadata_units(metadata_hits, scope_ids, candidate_limit)
        dense_ids = [hit.unit_id for hit in dense_hits]
        keyword_ids = [hit.unit_id for hit in keyword_hits]
        metadata_ids = [unit_id for unit_id, _, _ in metadata_units]
        rrf = reciprocal_rank_fusion([dense_ids, keyword_ids, metadata_ids])
        dense_by_unit = {hit.unit_id: hit for hit in dense_hits}
        keyword_by_unit = {hit.unit_id: hit for hit in keyword_hits}
        metadata_by_unit = {unit_id: hit for unit_id, hit, _ in metadata_units}
        metadata_by_claim = {unit_id: hit for unit_id, hit, _ in metadata_units}
        metadata_content = {unit_id: content for unit_id, _, content in metadata_units}
        candidates: list[_Candidate] = []
        for unit_id, rrf_score in rrf.items():
            dense_hit = dense_by_unit.get(unit_id)
            keyword_hit = keyword_by_unit.get(unit_id)
            metadata_hit = metadata_by_unit.get(unit_id)
            hit = dense_hit or keyword_hit
            claim_metadata_hit = None
            if hit is None and metadata_hit is not None:
                claim_id = unit_id
                page_id = metadata_hit.page_id
                page_version_id = UUID(int=0)
                content = metadata_content.get(unit_id, "")
            elif hit is not None:
                claim_id = hit.claim_id
                claim_metadata_hit = metadata_by_claim.get(claim_id)
                page_id = hit.page_id
                page_version_id = hit.page_version_id
                content = hit.content
            else:
                continue
            fields: list[str] = []
            if dense_hit is not None:
                fields.append("dense")
            if keyword_hit is not None:
                fields.append("keyword")
            combined_metadata_hit = metadata_hit or claim_metadata_hit
            if combined_metadata_hit is not None:
                fields.append(combined_metadata_hit.matched_field)
            candidates.append(
                _Candidate(
                    unit_id=unit_id,
                    page_id=page_id,
                    page_version_id=page_version_id,
                    claim_id=claim_id,
                    content=content,
                    rrf_score=rrf_score,
                    dense_score=dense_hit.score if dense_hit else None,
                    keyword_score=keyword_hit.score if keyword_hit else None,
                    matched_fields=tuple(dict.fromkeys(fields)),
                    exact_metadata_match=bool(
                        combined_metadata_hit and combined_metadata_hit.exact
                    ),
                )
            )
        return candidates

    async def _metadata_units(
        self,
        metadata_hits: tuple[WikiMetadataHit, ...],
        scope_ids: tuple[UUID, ...],
        candidate_limit: int,
    ) -> list[tuple[UUID, WikiMetadataHit, str]]:
        if not metadata_hits:
            return []
        page_ids = tuple(hit.page_id for hit in metadata_hits)
        if isinstance(self._read_model, WikiMetadataClaimsReadModel):
            by_page = await self._read_model.metadata_claim_ids(
                page_ids, scope_ids, candidate_limit
            )
        else:
            by_page = await self._metadata_claim_ids(page_ids, scope_ids, candidate_limit)
        claim_ids = tuple(claim_id for ids in by_page.values() for claim_id in ids)
        claims = await self._load_claims(claim_ids, scope_ids)
        by_claim_id = {claim.claim_id: claim for claim in claims if claim.valid}
        output: list[tuple[UUID, WikiMetadataHit, str]] = []
        for hit in metadata_hits:
            for claim_id in by_page.get(hit.page_id, ()):
                claim = by_claim_id.get(claim_id)
                if claim is not None:
                    output.append((claim.claim_id, hit, claim.text))
        return output

    async def _metadata_claim_ids(
        self,
        page_ids: tuple[UUID, ...],
        scope_ids: tuple[UUID, ...],
        candidate_limit: int,
    ) -> dict[UUID, tuple[UUID, ...]]:
        if self._sessions is None or not page_ids:
            return {}
        per_page_limit = max(1, candidate_limit // len(page_ids))
        remaining = candidate_limit
        grouped: dict[UUID, list[UUID]] = defaultdict(list)
        async with self._sessions() as session:
            for page_id in page_ids:
                if remaining <= 0:
                    break
                rows = list(
                    (
                        await session.execute(
                            select(WikiPage.id, WikiClaim.id)
                            .join(
                                WikiPageVersion,
                                WikiPage.current_version_id == WikiPageVersion.id,
                            )
                            .join(WikiClaim, WikiClaim.page_version_id == WikiPageVersion.id)
                            .where(
                                WikiPage.id == page_id,
                                WikiPage.knowledge_base_id.in_(scope_ids),
                                WikiPage.deleted_at.is_(None),
                                WikiPage.page_type != "index",
                                WikiPageVersion.index_state == "ready",
                                WikiClaim.trust_state == "verified",
                                WikiClaim.claim_type.in_(RETRIEVABLE_CLAIM_TYPES),
                            )
                            .order_by(WikiClaim.position, WikiClaim.id)
                            .limit(min(per_page_limit, remaining))
                        )
                    ).all()
                )
                for matched_page_id, claim_id in rows:
                    grouped[matched_page_id].append(claim_id)
                    remaining -= 1
                    if remaining <= 0:
                        break
        return {page_id: tuple(claim_ids) for page_id, claim_ids in grouped.items()}

    async def _load_claims(
        self, claim_ids: tuple[UUID, ...], scope_ids: tuple[UUID, ...]
    ) -> tuple[VerifiedWikiClaim, ...]:
        if not claim_ids:
            return ()
        if self._read_model is not None:
            return await self._read_model.load_verified_claims(
                claim_ids,
                scope_ids=scope_ids,
                require_index_ready=True,
            )
        if self._sessions is None:
            return ()
        async with self._sessions() as session:
            return await WikiReadModel(session).load_verified_claims(
                claim_ids,
                scope_ids=scope_ids,
                require_index_ready=True,
            )

    async def _score_and_filter(
        self,
        query: str,
        scope_ids: tuple[UUID, ...],
        candidates: list[_Candidate],
    ) -> tuple[list[_Accepted], bool, str | None, int]:
        claims = await self._load_claims(
            tuple(candidate.claim_id for candidate in candidates),
            scope_ids,
        )
        claims_by_id = {claim.claim_id: claim for claim in claims}
        valid_pairs = [
            (candidate, claim)
            for candidate in candidates
            if (claim := claims_by_id.get(candidate.claim_id)) is not None
            and claim.valid
            and bool(claim.sources)
            and all(source.valid for source in claim.sources)
            and (
                candidate.page_version_id == UUID(int=0)
                or candidate.page_version_id == claim.page_version_id
            )
        ]
        degraded = False
        degradation_reason: str | None = None
        rerank_scores: list[float | None] = [None] * len(valid_pairs)
        if self._scorer is not None and valid_pairs:
            try:
                scores = await self._scorer.score(query, [claim.text for _, claim in valid_pairs])
                if len(scores) != len(valid_pairs):
                    raise ValueError("invalid_rerank_score_count")
                normalized_scores = [float(score) for score in scores]
                if not all(math.isfinite(score) for score in normalized_scores):
                    raise ValueError("invalid_rerank_score")
                for index, score in enumerate(normalized_scores):
                    rerank_scores[index] = score
            except Exception:
                degraded = True
                degradation_reason = "reranker_unavailable"
                rerank_scores = [None] * len(valid_pairs)
        accepted: list[_Accepted] = []
        filtered_count = 0
        for index, (candidate, claim) in enumerate(valid_pairs):
            scored = WikiScoredCandidate(
                unit_id=candidate.unit_id,
                page_id=claim.page_id,
                claim_id=claim.claim_id,
                rrf_score=candidate.rrf_score,
                dense_score=candidate.dense_score,
                keyword_score=candidate.keyword_score,
                keyword_coverage=_keyword_coverage(query, claim.text),
                rerank_score=rerank_scores[index],
                exact_metadata_match=candidate.exact_metadata_match,
            )
            if not self._policy.accept(scored):
                filtered_count += 1
                continue
            source_chunk_ids = tuple(
                source.chunk_id for source in claim.sources if source.chunk_id is not None
            )
            active_document_version_ids = tuple(
                source.document_version_id for source in claim.sources
            )
            accepted.append(
                _Accepted(
                    candidate=replace_candidate_page(candidate, claim),
                    claim=claim,
                    score=_candidate_score(scored),
                    scored=scored,
                    fingerprint=fact_fingerprint(
                        claim.text,
                        source_chunk_ids=source_chunk_ids,
                        active_document_version_ids=active_document_version_ids,
                    ),
                )
            )
        accepted.sort(key=lambda item: (-item.score, str(item.candidate.unit_id)))
        return accepted, degraded, degradation_reason, filtered_count

    def _deduplicate(self, accepted: list[_Accepted]) -> list[_Accepted]:
        grouped: dict[str, list[_Accepted]] = defaultdict(list)
        for item in accepted:
            grouped[item.fingerprint].append(item)
        output: list[_Accepted] = []
        for items in grouped.values():
            items.sort(key=lambda item: (-item.score, str(item.candidate.unit_id)))
            output.append(items[0])
        output.sort(key=lambda item: (-item.score, str(item.candidate.unit_id)))
        return output

    def _page_items(
        self,
        ordered: list[_Accepted],
    ) -> list[tuple[UUID, list[_Accepted], tuple[int, int, int, float, str]]]:
        by_page: dict[UUID, list[_Accepted]] = defaultdict(list)
        for item in ordered:
            by_page[item.claim.page_id].append(item)
        return sorted(
            ((page_id, items, self._page_sort_key(items)) for page_id, items in by_page.items()),
            key=lambda entry: entry[2],
        )

    def _build_pages(
        self,
        page_items: list[tuple[UUID, list[_Accepted], tuple[int, int, int, float, str]]],
        all_accepted: list[_Accepted],
        scope_ids: tuple[UUID, ...],
        *,
        page_limit: int,
        claim_limit: int,
    ) -> tuple[WikiMatchedPage, ...]:
        pages: list[WikiMatchedPage] = []
        remaining_claims = claim_limit
        for page_id, items, _ in page_items:
            if len(pages) >= page_limit or remaining_claims <= 0:
                break
            claims: list[WikiMatchedClaim] = []
            for item in items[:remaining_claims]:
                also_in_pages = tuple(
                    self._page_summary(other.claim, scope_ids)
                    for other in all_accepted
                    if other.fingerprint == item.fingerprint and other.claim.page_id != page_id
                )
                claims.append(
                    WikiMatchedClaim(
                        id=item.claim.claim_id,
                        text=item.claim.text,
                        position=item.claim.position,
                        claim_type=item.claim.claim_type,
                        score=item.score,
                        sources=tuple(
                            _source_response(source)
                            for source in item.claim.sources
                            if source.valid
                        ),
                        also_in_pages=also_in_pages,
                    )
                )
            if not claims:
                continue
            first = items[0].claim
            fields: list[str] = []
            for item in items:
                fields.extend(item.candidate.matched_fields)
            pages.append(
                WikiMatchedPage(
                    id=page_id,
                    knowledge_base_id=scope_ids[0],
                    slug=first.page_slug,
                    title=first.page_title,
                    page_type=first.page_type,
                    score=max(item.score for item in items),
                    matched_fields=tuple(dict.fromkeys(fields)),
                    matched_claims=tuple(claims),
                    omitted_claim_count=max(0, len(items) - len(claims)),
                )
            )
            remaining_claims -= len(claims)
        return tuple(pages)

    @staticmethod
    def _page_summary(
        claim: VerifiedWikiClaim,
        scope_ids: tuple[UUID, ...],
    ) -> WikiMatchedPageSummary:
        return WikiMatchedPageSummary(
            id=claim.page_id,
            knowledge_base_id=scope_ids[0],
            slug=claim.page_slug,
            title=claim.page_title,
            page_type=claim.page_type,
            current_version_id=claim.page_version_id,
            index_state="ready",
        )

    @staticmethod
    def _page_sort_key(items: list[_Accepted]) -> tuple[int, int, int, float, str]:
        first = items[0]
        exact = any(item.candidate.exact_metadata_match for item in items)
        multi_source = any(len(item.claim.sources) > 1 for item in items)
        summary_page = first.claim.page_type == "summary"
        return (
            -int(exact),
            -int(multi_source),
            int(summary_page),
            -max(item.score for item in items),
            str(first.claim.page_id),
        )

    @staticmethod
    def _cursor_boundary_after_pages(
        page_items: list[tuple[UUID, list[_Accepted], tuple[int, int, int, float, str]]],
        pages: tuple[WikiMatchedPage, ...],
    ) -> tuple[UUID, list[_Accepted], tuple[int, int, int, float, str]] | None:
        returned_page_ids = {page.id for page in pages}
        boundary = None
        for page_item in page_items:
            page_id, _, _ = page_item
            if page_id in returned_page_ids:
                # cursor 使用最终页面排序边界，避免页面排序提升时漏掉未返回页面。
                boundary = page_item
        return boundary

    def _index_backlog_count(self) -> int:
        if self._index_backlog_provider is None:
            return 0
        try:
            return max(0, int(self._index_backlog_provider()))
        except Exception:
            return 0

    def _record_search_event(
        self,
        query_fingerprint: str,
        started: float,
        dense_count: int,
        keyword_count: int,
        metadata_count: int,
        page_count: int,
        threshold_filtered_count: int,
        degraded: bool,
        degradation_reason: str | None,
        *,
        no_answer: bool,
        cursor_expired: bool = False,
    ) -> None:
        record_wiki_search(
            query_fingerprint=query_fingerprint,
            latency_ms=round((perf_counter() - started) * 1000, 2),
            dense_hit_count=dense_count,
            keyword_hit_count=keyword_count,
            alias_hit_count=metadata_count,
            page_count=page_count,
            no_answer=no_answer,
            threshold_filtered_count=threshold_filtered_count,
            reranker_degraded=degraded and degradation_reason == "reranker_unavailable",
            degradation_reason=degradation_reason,
            index_backlog_count=self._index_backlog_count(),
            cursor_expired=cursor_expired,
        )


def replace_candidate_page(candidate: _Candidate, claim: VerifiedWikiClaim) -> _Candidate:
    return _Candidate(
        unit_id=candidate.unit_id,
        page_id=claim.page_id,
        page_version_id=claim.page_version_id,
        claim_id=claim.claim_id,
        content=candidate.content or claim.text,
        rrf_score=candidate.rrf_score,
        dense_score=candidate.dense_score,
        keyword_score=candidate.keyword_score,
        matched_fields=candidate.matched_fields,
        exact_metadata_match=candidate.exact_metadata_match,
    )


def _candidate_score(candidate: WikiScoredCandidate) -> float:
    if candidate.rerank_score is not None:
        return candidate.rerank_score
    values = [candidate.rrf_score, candidate.keyword_coverage]
    if candidate.dense_score is not None:
        values.append(candidate.dense_score)
    if candidate.keyword_score is not None:
        values.append(candidate.keyword_score)
    return max(values)
