from __future__ import annotations

import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from agent_api.knowledge.wiki.index_jobs import source_matches_chunk

EXCERPT_CONTEXT_CHARS = 180


@dataclass(frozen=True, slots=True)
class WikiSourceExcerpt:
    parent_chunk_id: UUID | None
    excerpt: str
    focus_start: int | None
    focus_end: int | None


@dataclass(frozen=True, slots=True)
class VerifiedWikiSource:
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID | None
    parent_chunk_id: UUID | None
    document_title: str
    filename: str
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    excerpt: str
    focus_start: int | None
    focus_end: int | None
    valid: bool
    invalid_reason: str | None


@dataclass(frozen=True, slots=True)
class WikiMetadataHit:
    page_id: UUID
    exact: bool
    matched_field: Literal["title", "alias", "slug"]


@dataclass(frozen=True, slots=True)
class VerifiedWikiClaim:
    claim_id: UUID
    page_id: UUID
    page_version_id: UUID
    page_title: str
    page_slug: str
    page_type: str
    claim_key: str
    claim_type: str
    text: str
    position: int
    trust_state: str
    reason: str | None
    sources: tuple[VerifiedWikiSource, ...]
    valid: bool
    invalid_reason: str | None


def build_source_excerpt(parent: Chunk, child: Chunk) -> WikiSourceExcerpt:
    """基于父块完整正文生成摘录，并保留父块坐标系中的高亮范围。"""

    parent_text = parent.content
    child_text = child.content
    focus_start: int | None = None
    focus_end: int | None = None
    parent_chunk_id: UUID | None = parent.id

    if (
        child.parent_chunk_id == parent.id
        and child.char_start is not None
        and child.char_end is not None
        and 0 <= child.char_start <= child.char_end <= len(parent_text)
        and parent_text[child.char_start : child.char_end] == child_text
    ):
        focus_start = child.char_start
        focus_end = child.char_end

    if focus_start is None:
        found_at = parent_text.find(child_text)
        if found_at >= 0:
            focus_start = found_at
            focus_end = found_at + len(child_text)

    if focus_start is None or focus_end is None:
        parent_text = child_text
        parent_chunk_id = child.id
        focus_start = 0
        focus_end = len(child_text)

    excerpt_start = max(0, focus_start - EXCERPT_CONTEXT_CHARS)
    excerpt_end = min(len(parent_text), focus_end + EXCERPT_CONTEXT_CHARS)
    return WikiSourceExcerpt(
        parent_chunk_id=parent_chunk_id,
        excerpt=parent_text[excerpt_start:excerpt_end],
        focus_start=focus_start,
        focus_end=focus_end,
    )


def _normalize_metadata(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s+", " ", normalized)


def _escape_like_pattern(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _main_claim_statement(
    claim_ids: tuple[UUID, ...],
    scope_ids: tuple[UUID, ...],
) -> Any:
    chunk_match = or_(
        and_(WikiClaimSource.chunk_id.is_not(None), Chunk.id == WikiClaimSource.chunk_id),
        and_(
            WikiClaimSource.chunk_id.is_(None),
            Chunk.document_version_id == WikiClaimSource.document_version_id,
            Chunk.kind == WikiClaimSource.chunk_kind,
            Chunk.ordinal == WikiClaimSource.chunk_ordinal,
        ),
    )
    return (
        select(WikiPage, WikiPageVersion, WikiClaim, WikiClaimSource, Document, Chunk)
        .join(WikiPageVersion, WikiClaim.page_version_id == WikiPageVersion.id)
        .join(WikiPage, WikiPageVersion.page_id == WikiPage.id)
        .outerjoin(WikiClaimSource, WikiClaimSource.claim_id == WikiClaim.id)
        .outerjoin(Document, Document.id == WikiClaimSource.document_id)
        .outerjoin(Chunk, chunk_match)
        .where(
            WikiClaim.id.in_(claim_ids),
            WikiPage.knowledge_base_id.in_(scope_ids),
            WikiPage.deleted_at.is_(None),
        )
        .order_by(WikiClaim.position, WikiClaim.id, WikiClaimSource.id)
    )


def _metadata_statement(query: str, scope_ids: tuple[UUID, ...], limit: int) -> Any:
    normalized = _normalize_metadata(query)
    slug_query = normalized.replace(" ", "-")
    title_prefix = f"{_escape_like_pattern(normalized)}%"
    slug_prefix = f"{_escape_like_pattern(slug_query)}%"
    return (
        select(
            WikiPage.id.label("page_id"),
            WikiPage.slug,
            WikiPage.title,
            WikiPageAlias.alias,
        )
        .join(WikiPageVersion, WikiPage.current_version_id == WikiPageVersion.id)
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
                func.lower(WikiPage.slug).like(slug_prefix, escape="\\"),
                func.lower(WikiPage.title).like(title_prefix, escape="\\"),
                func.lower(WikiPageAlias.alias).like(title_prefix, escape="\\"),
                WikiPageAlias.normalized_alias == normalized,
            ),
        )
        .order_by(WikiPage.id)
        .limit(max(limit * 4, limit))
    )


def _unpack_main_row(row: object) -> tuple[Any, Any, Any, Any, Any, Any]:
    if all(
        hasattr(row, name) for name in ("page", "version", "claim", "source", "document", "chunk")
    ):
        named = cast(Any, row)
        return named.page, named.version, named.claim, named.source, named.document, named.chunk
    page, version, claim, source, document, chunk = cast(tuple[Any, Any, Any, Any, Any, Any], row)
    return page, version, claim, source, document, chunk


def _unpack_parent_row(row: object) -> Chunk:
    if isinstance(row, Chunk):
        return row
    if hasattr(row, "Chunk"):
        return cast(Chunk, cast(Any, row).Chunk)
    values = cast(tuple[Chunk, ...], row)
    return values[0]


class WikiReadModel:
    """集中读取 Wiki 当前可信事实和来源摘录，避免调用方各自做 N+1 校验。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load_verified_claims(
        self,
        claim_ids: tuple[UUID, ...],
        scope_ids: tuple[UUID, ...],
        require_index_ready: bool,
    ) -> tuple[VerifiedWikiClaim, ...]:
        if not claim_ids or not scope_ids:
            return ()
        rows = list(
            (await self._session.execute(_main_claim_statement(claim_ids, scope_ids))).all()
        )
        parent_ids = {
            chunk.parent_chunk_id
            for *_, chunk in (_unpack_main_row(row) for row in rows)
            if chunk is not None and chunk.parent_chunk_id is not None
        }
        parents: dict[UUID, Chunk] = {}
        if parent_ids:
            parent_rows = list(
                (
                    await self._session.execute(
                        select(Chunk).where(Chunk.id.in_(tuple(parent_ids)))
                    )
                ).all()
            )
            parents = {parent.id: parent for parent in map(_unpack_parent_row, parent_rows)}

        grouped: OrderedDict[UUID, _ClaimBuildState] = OrderedDict()
        for row in rows:
            page, version, claim, source, document, chunk = _unpack_main_row(row)
            claim_reason = self._claim_invalid_reason(
                page,
                version,
                claim,
                require_index_ready=require_index_ready,
            )
            state = grouped.setdefault(
                claim.id,
                _ClaimBuildState(
                    claim=claim,
                    page=page,
                    version=version,
                    invalid_reason=claim_reason,
                    sources=[],
                ),
            )
            if source is None:
                continue
            source_item = self._build_source(source, document, chunk, parents)
            state.sources.append(source_item)
            if state.invalid_reason is None and not source_item.valid:
                state.invalid_reason = source_item.invalid_reason

        output: list[VerifiedWikiClaim] = []
        for state in grouped.values():
            valid = (
                state.invalid_reason is None
                and bool(state.sources)
                and all(source.valid for source in state.sources)
            )
            output.append(
                VerifiedWikiClaim(
                    claim_id=state.claim.id,
                    page_id=state.page.id,
                    page_version_id=state.version.id,
                    page_title=state.page.title,
                    page_slug=state.page.slug,
                    page_type=state.page.page_type,
                    claim_key=state.claim.claim_key,
                    claim_type=state.claim.claim_type,
                    text=state.claim.text,
                    position=state.claim.position,
                    trust_state=state.claim.trust_state,
                    reason=state.claim.reason,
                    sources=tuple(state.sources),
                    valid=valid,
                    invalid_reason=state.invalid_reason,
                )
            )
        return tuple(output)

    async def search_metadata(
        self,
        query: str,
        scope_ids: tuple[UUID, ...],
        limit: int,
    ) -> tuple[WikiMetadataHit, ...]:
        if limit <= 0 or not scope_ids:
            return ()
        normalized = _normalize_metadata(query)
        if not normalized:
            return ()
        slug_query = normalized.replace(" ", "-")
        rows = list(
            (await self._session.execute(_metadata_statement(query, scope_ids, limit))).all()
        )
        best: dict[UUID, WikiMetadataHit] = {}
        for row in rows:
            page_id = row.page_id
            candidates: list[WikiMetadataHit] = []
            title = _normalize_metadata(row.title)
            alias = _normalize_metadata(row.alias) if row.alias else ""
            slug = row.slug.casefold()
            if title == normalized:
                candidates.append(WikiMetadataHit(page_id, True, "title"))
            if alias == normalized:
                candidates.append(WikiMetadataHit(page_id, True, "alias"))
            if slug.startswith(slug_query):
                candidates.append(WikiMetadataHit(page_id, False, "slug"))
            if title.startswith(normalized):
                candidates.append(WikiMetadataHit(page_id, False, "title"))
            if alias.startswith(normalized):
                candidates.append(WikiMetadataHit(page_id, False, "alias"))
            for candidate in candidates:
                current = best.get(page_id)
                if current is None or _metadata_sort_key(candidate) < _metadata_sort_key(current):
                    best[page_id] = candidate
        return tuple(sorted(best.values(), key=_metadata_sort_key)[:limit])

    @staticmethod
    def _claim_invalid_reason(
        page: Any,
        version: Any,
        claim: Any,
        *,
        require_index_ready: bool,
    ) -> str | None:
        if page.current_version_id != version.id:
            return "page_version_not_current"
        if require_index_ready and version.index_state != "ready":
            return "page_index_not_ready"
        if claim.trust_state != "verified":
            return "claim_not_verified"
        return None

    @staticmethod
    def _build_source(
        source: Any,
        document: Any,
        chunk: Chunk | None,
        parents: dict[UUID, Chunk],
    ) -> VerifiedWikiSource:
        invalid_reason = _source_invalid_reason(source, document, chunk)
        parent = (
            parents.get(chunk.parent_chunk_id)
            if chunk is not None and chunk.parent_chunk_id
            else chunk
        )
        excerpt = (
            build_source_excerpt(parent, chunk)
            if chunk is not None and parent is not None
            else WikiSourceExcerpt(None, "", None, None)
        )
        return VerifiedWikiSource(
            document_id=source.document_id,
            document_version_id=source.document_version_id,
            chunk_id=chunk.id if chunk is not None else source.chunk_id,
            parent_chunk_id=excerpt.parent_chunk_id,
            document_title=source.document_title,
            filename=document.filename if document is not None else "",
            heading_path=tuple(source.heading_path),
            page_start=source.page_start,
            page_end=source.page_end,
            excerpt=excerpt.excerpt,
            focus_start=excerpt.focus_start,
            focus_end=excerpt.focus_end,
            valid=invalid_reason is None,
            invalid_reason=invalid_reason,
        )


@dataclass(slots=True)
class _ClaimBuildState:
    claim: Any
    page: Any
    version: Any
    invalid_reason: str | None
    sources: list[VerifiedWikiSource]


def _source_invalid_reason(source: Any, document: Any, chunk: Chunk | None) -> str | None:
    if document is None or document.deleted_at is not None or document.status != "ready":
        return "document_not_ready"
    if document.active_version_id != source.document_version_id:
        return "document_version_not_active"
    if chunk is None:
        return "chunk_not_found"
    if source.chunk_kind != chunk.kind:
        return "chunk_kind_mismatch"
    if source.chunk_ordinal != chunk.ordinal:
        return "chunk_ordinal_mismatch"
    if not source_matches_chunk(source, chunk):
        expected_hash = sha256(chunk.content.encode("utf-8")).hexdigest()
        if source.content_sha256 != expected_hash:
            return "chunk_hash_mismatch"
        return "chunk_identity_mismatch"
    return None


def _metadata_sort_key(hit: WikiMetadataHit) -> tuple[int, int, str]:
    field_priority = {"slug": 0, "title": 1, "alias": 2}
    return (-int(hit.exact), field_priority[hit.matched_field], str(hit.page_id))
