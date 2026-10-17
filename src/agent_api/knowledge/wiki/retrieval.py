from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from time import perf_counter
from typing import Any, Protocol, cast
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_api.core.config import WikiSearchBackend
from agent_api.knowledge.application.citations import Source
from agent_api.knowledge.application.management import DEFAULT_WORKSPACE_ID
from agent_api.knowledge.infrastructure.database.models import (
    Chunk,
    Document,
    KnowledgeBase,
    WikiClaim,
    WikiClaimSource,
    WikiConfig,
    WikiPage,
    WikiPageAlias,
    WikiPageVersion,
)
from agent_api.knowledge.infrastructure.observability.events import record_wiki_shadow_diff
from agent_api.knowledge.wiki.cursor import WikiCursorError
from agent_api.knowledge.wiki.ranking import reciprocal_rank_fusion, select_mmr
from agent_api.knowledge.wiki.read_model import VerifiedWikiClaim, WikiReadModel

logger = logging.getLogger(__name__)

RETRIEVABLE_CLAIM_TYPES = ("summary_fact", "topic_fact")


class SourceRetrieval(Protocol):
    async def search_candidates(
        self,
        query: str,
        scope_ids: tuple[UUID, ...],
        *,
        mode: str = "hybrid",
        limit: int = 40,
    ) -> tuple[list[Source], bool]: ...


class WikiReranker(Protocol):
    async def rerank(self, query: str, texts: list[str]) -> list[int]: ...


class WikiRetrievalError(RuntimeError):
    """Wiki 检索无法安全降级时返回稳定错误码。"""


class WikiIndependentRetrieval(Protocol):
    async def search(
        self,
        query: str,
        scope_ids: tuple[UUID, ...],
        *,
        mode: str = "hybrid",
        page_limit: int = 5,
        claim_limit: int = 12,
        cursor: str | None = None,
    ) -> WikiSearchResult: ...


@dataclass(frozen=True, slots=True)
class WikiMatchedPageSummary:
    id: UUID
    knowledge_base_id: UUID
    slug: str
    title: str
    page_type: str
    current_version_id: UUID | None = None
    index_state: str = "not_required"
    index_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class WikiMatchedClaim:
    id: UUID
    text: str
    position: int
    claim_type: str
    score: float
    sources: tuple[Source, ...]
    also_in_pages: tuple[WikiMatchedPageSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class WikiMatchedPage:
    id: UUID
    knowledge_base_id: UUID
    slug: str
    title: str
    page_type: str
    score: float
    matched_fields: tuple[str, ...]
    matched_claims: tuple[WikiMatchedClaim, ...]
    omitted_claim_count: int


@dataclass(frozen=True, slots=True)
class WikiSearchResult:
    pages: tuple[WikiMatchedPage, ...]
    degraded: bool
    degradation_reason: str | None
    truncation_reason: str | None
    no_answer: bool = False
    no_answer_reason: str | None = None
    next_cursor: str | None = None
    collection_version: str | None = None


@dataclass(frozen=True, slots=True)
class WikiShadowMetrics:
    query_fingerprint: str
    legacy_page_ids: tuple[UUID, ...]
    independent_page_ids: tuple[UUID, ...]
    legacy_claim_ids: tuple[UUID, ...]
    independent_claim_ids: tuple[UUID, ...]
    legacy_elapsed_ms: float | None
    independent_elapsed_ms: float | None
    legacy_no_answer: bool
    independent_no_answer: bool | None
    independent_degradation_reason: str | None


@dataclass(frozen=True, slots=True)
class _ClaimRecord:
    id: UUID
    text: str
    position: int
    claim_type: str
    sources: tuple[Source, ...]


@dataclass(frozen=True, slots=True)
class _PageRecord:
    id: UUID
    knowledge_base_id: UUID
    slug: str
    title: str
    page_type: str
    summary: str
    aliases: tuple[str, ...]
    claims: tuple[_ClaimRecord, ...]


def _search_source_response(source: Any) -> Source:
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


def _normalize_query(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s+", " ", normalized)


def _query_tokens(value: str) -> set[str]:
    normalized = _normalize_query(value)
    return {
        *re.findall(r"[a-z0-9]+", normalized),
        *re.findall(r"[\u3400-\u9fff]", normalized),
    }


def _text_relevance(query: str, text: str) -> float:
    query_tokens = _query_tokens(query)
    if not query_tokens:
        return 0.0
    text_tokens = _query_tokens(text)
    return len(query_tokens & text_tokens) / len(query_tokens)


def _escape_like_pattern(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_metadata_statement(query: str, scope_ids: tuple[UUID, ...]) -> Any:
    title_matched = func.lower(WikiPage.title).like(f"{_escape_like_pattern(query)}%", escape="\\")
    alias_matched = func.coalesce(
        func.bool_or(
            func.lower(WikiPageAlias.alias).like(f"{_escape_like_pattern(query)}%", escape="\\")
        ),
        False,
    )
    alias_exact = func.coalesce(func.bool_or(func.lower(WikiPageAlias.alias) == query), False)
    slug_query = query.replace(" ", "-")
    slug_matched = func.lower(WikiPage.slug).like(
        f"{_escape_like_pattern(slug_query)}%", escape="\\"
    )
    exact_match = or_(
        func.lower(WikiPage.title) == query,
        alias_exact,
    )
    return (
        select(
            WikiPage.id,
            WikiPage.title,
            WikiPage.slug,
            title_matched.label("title_matched"),
            alias_matched.label("alias_matched"),
            slug_matched.label("slug_matched"),
            exact_match.label("exact_match"),
        )
        .join(
            WikiPageVersion,
            WikiPage.current_version_id == WikiPageVersion.id,
        )
        .join(KnowledgeBase, KnowledgeBase.id == WikiPage.knowledge_base_id)
        .join(WikiConfig, WikiConfig.knowledge_base_id == KnowledgeBase.id)
        .outerjoin(WikiPageAlias, WikiPageAlias.page_id == WikiPage.id)
        .where(
            WikiPage.knowledge_base_id.in_(scope_ids),
            WikiPage.deleted_at.is_(None),
            WikiPage.page_type != "index",
            KnowledgeBase.workspace_id == DEFAULT_WORKSPACE_ID,
            KnowledgeBase.enabled.is_(True),
            WikiConfig.enabled.is_(True),
            or_(
                title_matched,
                alias_matched,
                alias_exact,
                slug_matched,
            ),
        )
        .group_by(
            WikiPage.id,
            WikiPage.title,
            WikiPage.slug,
        )
        .order_by(
            exact_match.desc(),
            slug_matched.desc(),
            title_matched.desc(),
            alias_matched.desc(),
            WikiPage.id,
        )
        .limit(200)
    )


class LegacyWikiRetrievalService:
    """融合页面元数据和原文 chunk 命中，返回当前且可追溯的 Wiki 事实。"""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        source_retrieval: SourceRetrieval,
        reranker: WikiReranker | None = None,
    ) -> None:
        self._sessions = sessions
        self._source_retrieval = source_retrieval
        self._reranker = reranker

    async def search(
        self,
        query: str,
        scope_ids: tuple[UUID, ...],
        *,
        mode: str = "hybrid",
        page_limit: int = 5,
        claim_limit: int = 12,
        character_budget: int = 12_000,
    ) -> WikiSearchResult:
        normalized = _normalize_query(query)
        if not normalized or len(normalized) > 200 or not _query_tokens(normalized):
            raise ValueError("invalid_wiki_query")
        if mode not in {"hybrid", "semantic", "keyword"}:
            raise ValueError("invalid_search_options")
        if not 1 <= page_limit <= 20 or not 1 <= claim_limit <= 50:
            raise ValueError("invalid_search_options")
        if character_budget <= 0:
            raise ValueError("invalid_character_budget")
        if not scope_ids:
            return WikiSearchResult((), False, None, None)

        started = perf_counter()
        fingerprint = sha256(normalized.encode()).hexdigest()[:16]
        try:
            metadata_ids, metadata_fields = await self._metadata_page_ids(normalized, scope_ids)
        except Exception as error:
            raise WikiRetrievalError("wiki_retrieval_unavailable") from error

        degraded = False
        degradation_reason: str | None = None
        sources: list[Source] = []
        try:
            sources, source_degraded = await self._source_retrieval.search_candidates(
                normalized,
                scope_ids,
                mode=mode,
                limit=40,
            )
            if source_degraded:
                degraded = True
                degradation_reason = "source_reranker_unavailable"
        except Exception:
            degraded = True
            degradation_reason = "source_retrieval_unavailable"
        try:
            source_ids = await self._source_page_ids(sources, scope_ids) if sources else []
        except Exception as error:
            raise WikiRetrievalError("wiki_retrieval_unavailable") from error
        source_ids = list(dict.fromkeys(source_ids))
        fused = reciprocal_rank_fusion([metadata_ids, source_ids])
        if not fused:
            self._log_search(
                fingerprint,
                mode,
                len(scope_ids),
                len(metadata_ids),
                len(sources),
                0,
                0,
                degraded,
                degradation_reason,
                started,
            )
            return WikiSearchResult((), degraded, degradation_reason, None)
        ordered_ids = list(fused)
        try:
            pages_by_id = await self._hydrate_pages(ordered_ids, scope_ids)
        except Exception as error:
            raise WikiRetrievalError("wiki_retrieval_unavailable") from error
        ordered_pages = [pages_by_id[page_id] for page_id in ordered_ids if page_id in pages_by_id]

        if self._reranker is not None and len(ordered_pages) > 1:
            try:
                order = await self._reranker.rerank(
                    normalized,
                    [self._page_passage(page) for page in ordered_pages],
                )
                if sorted(order) != list(range(len(ordered_pages))):
                    raise ValueError("invalid_rerank_result")
                ordered_pages = [ordered_pages[index] for index in order]
            except Exception:
                degraded = True
                if degradation_reason is None:
                    degradation_reason = "reranker_unavailable"

        source_ranks = {source.chunk_id: source.rank for source in sources}
        ranked_claims = {
            page.id: self.rank_page_claims(normalized, page.claims, source_ranks)
            for page in ordered_pages
        }
        remaining_claims = claim_limit
        remaining_chars = character_budget
        truncation_reason: str | None = None
        matched_pages: list[WikiMatchedPage] = []
        per_page_limit = claim_limit if len(ordered_pages) == 1 else 4
        for page in ordered_pages:
            if len(matched_pages) >= page_limit or remaining_claims <= 0:
                truncation_reason = truncation_reason or "claim_limit"
                break
            selected: list[WikiMatchedClaim] = []
            candidates = ranked_claims[page.id]
            for claim, score in candidates:
                if len(selected) >= per_page_limit or remaining_claims <= 0:
                    break
                cost = len(claim.text)
                if cost > remaining_chars and selected:
                    truncation_reason = "character_budget"
                    break
                if cost > remaining_chars and matched_pages:
                    truncation_reason = "character_budget"
                    break
                selected.append(
                    WikiMatchedClaim(
                        id=claim.id,
                        text=claim.text,
                        position=claim.position,
                        claim_type=claim.claim_type,
                        score=score,
                        sources=claim.sources,
                    )
                )
                remaining_claims -= 1
                remaining_chars = max(0, remaining_chars - cost)
            if not selected:
                continue
            fields = list(metadata_fields.get(page.id, ()))
            if page.id in source_ids:
                fields.append("source_chunk")
            matched_pages.append(
                WikiMatchedPage(
                    id=page.id,
                    knowledge_base_id=page.knowledge_base_id,
                    slug=page.slug,
                    title=page.title,
                    page_type=page.page_type,
                    score=fused[page.id],
                    matched_fields=tuple(dict.fromkeys(fields)),
                    matched_claims=tuple(selected),
                    omitted_claim_count=max(0, len(candidates) - len(selected)),
                )
            )
            if remaining_chars <= 0:
                truncation_reason = "character_budget"
                break
        if len(matched_pages) < len(ordered_pages) and truncation_reason is None:
            truncation_reason = "page_limit" if len(matched_pages) >= page_limit else "claim_limit"
        self._log_search(
            fingerprint,
            mode,
            len(scope_ids),
            len(metadata_ids),
            len(sources),
            len(matched_pages),
            sum(len(page.matched_claims) for page in matched_pages),
            degraded,
            degradation_reason,
            started,
        )
        return WikiSearchResult(
            tuple(matched_pages), degraded, degradation_reason, truncation_reason
        )

    def rank_page_claims(
        self,
        query: str,
        claims: Sequence[_ClaimRecord],
        source_ranks: dict[UUID, int] | None = None,
    ) -> list[tuple[_ClaimRecord, float]]:
        """按问题、命中来源和 MMR 对单页事实排序。"""

        ranks = source_ranks or {}
        scored: list[tuple[_ClaimRecord, float, str]] = []
        for claim in claims:
            if claim.claim_type not in RETRIEVABLE_CLAIM_TYPES:
                continue
            matched = [
                ranks[source.chunk_id] for source in claim.sources if source.chunk_id in ranks
            ]
            source_score = 1.0 / min(matched) if matched else 0.0
            lexical = _text_relevance(query, claim.text)
            scored.append((claim, source_score + lexical, claim.text))
        selected = select_mmr(scored, limit=len(scored), lambda_=0.7)
        return [(claim, score) for claim, score, _ in selected]

    async def read_page(
        self,
        knowledge_base_id: UUID,
        page_id: UUID,
        *,
        query: str = "",
        cursor: int = 0,
        limit: int = 20,
    ) -> dict[str, Any] | None:
        """无查询时稳定分页，有查询时按相关性读取当前可信事实。"""

        if cursor < 0 or not 1 <= limit <= 50:
            raise ValueError("invalid_wiki_cursor")
        pages = await self._hydrate_pages([page_id], (knowledge_base_id,))
        page = pages.get(page_id)
        if page is None:
            return None
        if query.strip():
            normalized = _normalize_query(query)
            if len(normalized) > 200:
                raise ValueError("invalid_wiki_query")
            claims = [claim for claim, _ in self.rank_page_claims(normalized, page.claims)]
        else:
            claims = sorted(
                (claim for claim in page.claims if claim.claim_type in RETRIEVABLE_CLAIM_TYPES),
                key=lambda claim: (claim.position, claim.id),
            )
        selected = claims[cursor : cursor + limit]
        next_cursor = cursor + len(selected) if cursor + len(selected) < len(claims) else None
        return {
            "page_id": page.id,
            "knowledge_base_id": page.knowledge_base_id,
            "title": page.title,
            "claims": selected,
            "next_cursor": next_cursor,
            "omitted_claim_count": max(0, len(claims) - cursor - len(selected)),
        }

    async def _metadata_page_ids(
        self,
        query: str,
        scope_ids: tuple[UUID, ...],
    ) -> tuple[list[UUID], dict[UUID, tuple[str, ...]]]:
        async with self._sessions() as session:
            hits = await WikiReadModel(session).search_metadata(query, scope_ids, limit=40)
        matched: dict[UUID, list[str]] = {}
        for hit in hits:
            matched.setdefault(hit.page_id, []).append(hit.matched_field)
        ordered = [hit.page_id for hit in hits]
        return ordered, {page_id: tuple(dict.fromkeys(matched[page_id])) for page_id in ordered}

    async def _source_page_ids(
        self,
        sources: list[Source],
        scope_ids: tuple[UUID, ...],
    ) -> list[UUID]:
        source_order = {source.chunk_id: source.rank for source in sources}
        async with self._sessions() as session:
            rows = list(
                (
                    await session.execute(
                        select(
                            WikiPage.id,
                            WikiClaimSource.chunk_id,
                            WikiClaimSource.chunk_kind,
                            WikiClaimSource.chunk_ordinal,
                            WikiClaimSource.content_sha256,
                            Chunk.content,
                            Chunk.kind,
                            Chunk.ordinal,
                        )
                        .join(
                            WikiPageVersion,
                            WikiPage.current_version_id == WikiPageVersion.id,
                        )
                        .join(WikiClaim, WikiClaim.page_version_id == WikiPageVersion.id)
                        .join(WikiClaimSource, WikiClaimSource.claim_id == WikiClaim.id)
                        .join(Document, Document.id == WikiClaimSource.document_id)
                        .join(Chunk, Chunk.id == WikiClaimSource.chunk_id)
                        .join(KnowledgeBase, KnowledgeBase.id == WikiPage.knowledge_base_id)
                        .join(WikiConfig, WikiConfig.knowledge_base_id == KnowledgeBase.id)
                        .where(
                            WikiPage.knowledge_base_id.in_(scope_ids),
                            WikiPage.deleted_at.is_(None),
                            WikiClaim.trust_state == "verified",
                            WikiClaim.claim_type.in_(RETRIEVABLE_CLAIM_TYPES),
                            WikiClaimSource.chunk_id.in_(tuple(source_order)),
                            Document.active_version_id == WikiClaimSource.document_version_id,
                            Document.knowledge_base_id == WikiPage.knowledge_base_id,
                            Document.status == "ready",
                            Document.deleted_at.is_(None),
                            KnowledgeBase.workspace_id == DEFAULT_WORKSPACE_ID,
                            KnowledgeBase.enabled.is_(True),
                            WikiConfig.enabled.is_(True),
                        )
                    )
                ).all()
            )
        valid = [
            row
            for row in rows
            if row.chunk_id is not None
            and row.chunk_kind == row.kind
            and row.chunk_ordinal == row.ordinal
            and sha256(row.content.encode()).hexdigest() == row.content_sha256
        ]
        valid.sort(key=lambda row: (source_order[row.chunk_id], str(row.id)))
        return [row.id for row in valid]

    async def _hydrate_pages(
        self,
        page_ids: list[UUID],
        scope_ids: tuple[UUID, ...],
    ) -> dict[UUID, _PageRecord]:
        if not page_ids:
            return {}
        async with self._sessions() as session:
            page_rows = list(
                (
                    await session.execute(
                        select(WikiPage, WikiPageVersion)
                        .join(
                            WikiPageVersion,
                            WikiPage.current_version_id == WikiPageVersion.id,
                        )
                        .join(KnowledgeBase, KnowledgeBase.id == WikiPage.knowledge_base_id)
                        .join(WikiConfig, WikiConfig.knowledge_base_id == KnowledgeBase.id)
                        .where(
                            WikiPage.id.in_(page_ids),
                            WikiPage.knowledge_base_id.in_(scope_ids),
                            WikiPage.deleted_at.is_(None),
                            WikiPage.page_type != "index",
                            KnowledgeBase.workspace_id == DEFAULT_WORKSPACE_ID,
                            KnowledgeBase.enabled.is_(True),
                            WikiConfig.enabled.is_(True),
                        )
                    )
                ).all()
            )
            aliases = list(
                (
                    await session.execute(
                        select(WikiPageAlias.page_id, WikiPageAlias.alias).where(
                            WikiPageAlias.page_id.in_(page_ids)
                        )
                    )
                ).all()
            )
            claim_ids = tuple(
                await session.scalars(
                    select(WikiClaim.id)
                    .join(WikiPageVersion, WikiClaim.page_version_id == WikiPageVersion.id)
                    .join(WikiPage, WikiPage.current_version_id == WikiPageVersion.id)
                    .where(
                        WikiPage.id.in_(page_ids),
                        WikiClaim.trust_state == "verified",
                        WikiClaim.claim_type.in_(RETRIEVABLE_CLAIM_TYPES),
                    )
                    .order_by(WikiClaim.position, WikiClaim.id)
                )
            )
            verified_claims = await WikiReadModel(session).load_verified_claims(
                claim_ids,
                scope_ids=scope_ids,
                require_index_ready=False,
            )
        aliases_by_page: dict[UUID, list[str]] = {}
        for page_id, alias in aliases:
            aliases_by_page.setdefault(page_id, []).append(alias)
        claims_by_version: dict[UUID, list[VerifiedWikiClaim]] = {}
        for claim in verified_claims:
            if claim.valid:
                claims_by_version.setdefault(claim.page_version_id, []).append(claim)
        records: dict[UUID, _PageRecord] = {}
        for page, version in page_rows:
            page_claims = tuple(
                _ClaimRecord(
                    id=claim.claim_id,
                    text=claim.text,
                    position=claim.position,
                    claim_type=claim.claim_type,
                    sources=tuple(
                        _search_source_response(source) for source in claim.sources if source.valid
                    ),
                )
                for claim in sorted(
                    claims_by_version.get(version.id, []),
                    key=lambda item: (item.position, str(item.claim_id)),
                )
                if any(source.valid for source in claim.sources)
            )
            records[page.id] = _PageRecord(
                id=page.id,
                knowledge_base_id=page.knowledge_base_id,
                slug=page.slug,
                title=page.title,
                page_type=page.page_type,
                summary=version.summary,
                aliases=tuple(aliases_by_page.get(page.id, [])),
                claims=page_claims,
            )
        return records

    @staticmethod
    def _page_passage(page: _PageRecord) -> str:
        claim = page.claims[0].text if page.claims else ""
        return "\n".join((page.title, " ".join(page.aliases), page.summary, claim))

    @staticmethod
    def _log_search(
        fingerprint: str,
        mode: str,
        scope_count: int,
        metadata_count: int,
        source_count: int,
        page_count: int,
        claim_count: int,
        degraded: bool,
        degradation_reason: str | None,
        started: float,
    ) -> None:
        elapsed_ms = round((perf_counter() - started) * 1000, 2)
        logger.info(
            "wiki_search query_fingerprint=%s mode=%s scope_count=%d "
            "metadata_candidates=%d source_candidates=%d pages=%d claims=%d "
            "degraded=%s degradation_reason=%s elapsed_ms=%.2f",
            fingerprint,
            mode,
            scope_count,
            metadata_count,
            source_count,
            page_count,
            claim_count,
            degraded,
            degradation_reason,
            elapsed_ms,
        )


class WikiRetrievalService:
    """按配置在 legacy、独立检索和 shadow 模式之间路由。"""

    def __init__(
        self,
        legacy: LegacyWikiRetrievalService | async_sessionmaker[AsyncSession],
        independent: WikiIndependentRetrieval | SourceRetrieval | None = None,
        reranker: WikiReranker | None = None,
        *,
        backend: WikiSearchBackend = WikiSearchBackend.LEGACY,
    ) -> None:
        # 兼容旧测试和旧调用方：传 sessions/source_retrieval 时自动包成 legacy。
        if isinstance(legacy, LegacyWikiRetrievalService) or (
            hasattr(legacy, "search") and not callable(legacy)
        ):
            self._legacy = legacy
            self._independent = (
                cast(WikiIndependentRetrieval, independent) if independent is not None else None
            )
        else:
            if independent is None:
                raise TypeError("legacy source retrieval is required")
            self._legacy = LegacyWikiRetrievalService(
                legacy,
                cast(SourceRetrieval, independent),
                reranker,
            )
            self._independent = None
        self._backend = backend
        self.last_shadow_metrics: WikiShadowMetrics | None = None

    async def search(
        self,
        query: str,
        scope_ids: tuple[UUID, ...],
        *,
        mode: str = "hybrid",
        page_limit: int = 5,
        claim_limit: int = 12,
        cursor: str | None = None,
        character_budget: int = 12_000,
    ) -> WikiSearchResult:
        if self._backend is WikiSearchBackend.LEGACY:
            if cursor is not None:
                raise WikiCursorError("wiki_search_cursor_expired")
            return await self._legacy.search(
                query,
                scope_ids,
                mode=mode,
                page_limit=page_limit,
                claim_limit=claim_limit,
                character_budget=character_budget,
            )
        if self._backend is WikiSearchBackend.INDEPENDENT:
            if self._independent is None:
                raise WikiRetrievalError("wiki_retrieval_unavailable")
            return await self._independent.search(
                query,
                scope_ids,
                mode=mode,
                page_limit=page_limit,
                claim_limit=claim_limit,
                cursor=cursor,
            )
        if self._independent is None:
            return await self._legacy.search(
                query,
                scope_ids,
                mode=mode,
                page_limit=page_limit,
                claim_limit=claim_limit,
                character_budget=character_budget,
            )
        if cursor is not None:
            raise WikiCursorError("wiki_search_cursor_expired")
        legacy_started = perf_counter()
        legacy_task = self._legacy.search(
            query,
            scope_ids,
            mode=mode,
            page_limit=page_limit,
            claim_limit=claim_limit,
            character_budget=character_budget,
        )
        independent_started = perf_counter()
        independent_task = self._independent.search(
            query,
            scope_ids,
            mode=mode,
            page_limit=page_limit,
            claim_limit=claim_limit,
            cursor=None,
        )
        gathered: tuple[WikiSearchResult | BaseException, WikiSearchResult | BaseException]
        gathered = await asyncio.gather(
            legacy_task,
            independent_task,
            return_exceptions=True,
        )
        legacy_result, independent_result = gathered
        legacy_elapsed = round((perf_counter() - legacy_started) * 1000, 2)
        independent_elapsed = round((perf_counter() - independent_started) * 1000, 2)
        if isinstance(legacy_result, BaseException):
            raise legacy_result
        self._log_shadow_diff(
            query,
            legacy_result,
            independent_result,
            legacy_elapsed,
            independent_elapsed,
        )
        return legacy_result

    async def read_page(
        self,
        knowledge_base_id: UUID,
        page_id: UUID,
        *,
        query: str = "",
        cursor: int = 0,
        limit: int = 20,
    ) -> dict[str, Any] | None:
        return await self._legacy.read_page(
            knowledge_base_id,
            page_id,
            query=query,
            cursor=cursor,
            limit=limit,
        )

    def rank_page_claims(
        self,
        query: str,
        claims: Sequence[_ClaimRecord],
        source_ranks: dict[UUID, int] | None = None,
    ) -> list[tuple[_ClaimRecord, float]]:
        return self._legacy.rank_page_claims(query, claims, source_ranks)

    def _log_shadow_diff(
        self,
        query: str,
        legacy_result: WikiSearchResult,
        independent_result: WikiSearchResult | BaseException,
        legacy_elapsed_ms: float | None,
        independent_elapsed_ms: float | None,
    ) -> None:
        fingerprint = sha256(_normalize_query(query).encode()).hexdigest()[:16]
        legacy_page_ids = tuple(page.id for page in legacy_result.pages)
        legacy_claim_ids = tuple(
            claim.id for page in legacy_result.pages for claim in page.matched_claims
        )
        if isinstance(independent_result, BaseException):
            self.last_shadow_metrics = WikiShadowMetrics(
                query_fingerprint=fingerprint,
                legacy_page_ids=legacy_page_ids,
                independent_page_ids=(),
                legacy_claim_ids=legacy_claim_ids,
                independent_claim_ids=(),
                legacy_elapsed_ms=legacy_elapsed_ms,
                independent_elapsed_ms=independent_elapsed_ms,
                legacy_no_answer=legacy_result.no_answer,
                independent_no_answer=None,
                independent_degradation_reason=type(independent_result).__name__,
            )
        else:
            self.last_shadow_metrics = WikiShadowMetrics(
                query_fingerprint=fingerprint,
                legacy_page_ids=legacy_page_ids,
                independent_page_ids=tuple(page.id for page in independent_result.pages),
                legacy_claim_ids=legacy_claim_ids,
                independent_claim_ids=tuple(
                    claim.id for page in independent_result.pages for claim in page.matched_claims
                ),
                legacy_elapsed_ms=legacy_elapsed_ms,
                independent_elapsed_ms=independent_elapsed_ms,
                legacy_no_answer=legacy_result.no_answer,
                independent_no_answer=independent_result.no_answer,
                independent_degradation_reason=independent_result.degradation_reason,
            )
        record_wiki_shadow_diff(self.last_shadow_metrics)
