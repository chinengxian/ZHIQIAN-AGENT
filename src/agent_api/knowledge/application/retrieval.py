from __future__ import annotations

from dataclasses import replace
from typing import Protocol
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_api.knowledge.application.citations import Source
from agent_api.knowledge.application.management import DEFAULT_WORKSPACE_ID
from agent_api.knowledge.domain.statuses import ChunkKind
from agent_api.knowledge.infrastructure.database.models import (
    Chunk,
    Document,
    KnowledgeBase,
)
from agent_api.knowledge.infrastructure.milvus.index import SearchHit


class SearchIndex(Protocol):
    async def search(
        self,
        query: str,
        *,
        vector: list[float] | None,
        knowledge_base_ids: tuple[UUID, ...],
        mode: str,
        limit: int = 20,
    ) -> list[SearchHit]: ...


class Embedder(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class Reranker(Protocol):
    async def rerank(self, query: str, texts: list[str]) -> list[int]: ...


class KnowledgeScopeError(ValueError):
    pass


class KnowledgeRetrievalService:
    """以 PostgreSQL 活动版本和工作区边界过滤 Milvus 候选。"""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        index: SearchIndex,
        embedder: Embedder,
        reranker: Reranker | None = None,
    ) -> None:
        self._sessions = sessions
        self._index = index
        self._embedder = embedder
        self._reranker = reranker

    async def resolve_scope(
        self,
        mode: str,
        selected_ids: tuple[UUID, ...] = (),
    ) -> tuple[UUID, ...]:
        if mode not in {"all_enabled", "selected"}:
            raise KnowledgeScopeError("invalid_knowledge_scope")
        if mode == "selected" and not selected_ids:
            raise KnowledgeScopeError("empty_knowledge_scope")
        if mode == "all_enabled" and selected_ids:
            raise KnowledgeScopeError("invalid_knowledge_scope")
        async with self._sessions() as session:
            query = select(KnowledgeBase.id).where(
                KnowledgeBase.workspace_id == DEFAULT_WORKSPACE_ID,
                KnowledgeBase.enabled.is_(True),
            )
            if selected_ids:
                query = query.where(KnowledgeBase.id.in_(selected_ids))
            actual = tuple(await session.scalars(query.order_by(KnowledgeBase.id)))
        if mode == "selected" and set(actual) != set(selected_ids):
            raise KnowledgeScopeError("knowledge_scope_unavailable")
        return actual

    async def search(
        self,
        query: str,
        scope_ids: tuple[UUID, ...],
        *,
        mode: str = "hybrid",
        limit: int = 8,
    ) -> tuple[list[Source], bool]:
        if not query.strip() or not scope_ids:
            return [], False
        if mode not in {"hybrid", "semantic", "keyword"} or not 1 <= limit <= 8:
            raise ValueError("invalid_search_options")
        vector = None
        if mode != "keyword":
            vector = (await self._embedder.embed_documents([query]))[0]
        hits = await self._index.search(
            query, vector=vector, knowledge_base_ids=scope_ids, mode=mode, limit=20
        )
        if not hits:
            return [], False
        async with self._sessions() as session:
            rows = list(
                (
                    await session.execute(
                        select(Chunk, Document)
                        .join(Document, Document.active_version_id == Chunk.document_version_id)
                        .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
                        .where(
                            Chunk.id.in_([hit.chunk_id for hit in hits]),
                            Chunk.kind == ChunkKind.CHILD.value,
                            Document.deleted_at.is_(None),
                            KnowledgeBase.workspace_id == DEFAULT_WORKSPACE_ID,
                            KnowledgeBase.enabled.is_(True),
                            KnowledgeBase.id.in_(scope_ids),
                        )
                    )
                ).all()
            )
            by_id = {chunk.id: (chunk, document) for chunk, document in rows}
            parent_ids = [chunk.parent_chunk_id for chunk, _ in rows if chunk.parent_chunk_id]
            parents = {
                parent.id: parent
                for parent in await session.scalars(select(Chunk).where(Chunk.id.in_(parent_ids)))
            }
        sources: list[Source] = []
        seen: set[UUID] = set()
        remaining = 24_000
        for hit in hits:
            row = by_id.get(hit.chunk_id)
            if row is None:
                continue
            chunk, document = row
            parent_id = chunk.parent_chunk_id or chunk.id
            if parent_id in seen or remaining <= 0:
                continue
            seen.add(parent_id)
            parent = parents.get(parent_id)
            content = parent.content if parent is not None else chunk.content
            excerpt = content[: min(500, remaining)]
            remaining -= len(excerpt)
            sources.append(
                Source(
                    citation_id=f"[{len(sources) + 1}]",
                    chunk_id=chunk.id,
                    document_id=document.id,
                    document_version_id=chunk.document_version_id,
                    title=document.title,
                    filename=document.filename,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    heading_path=tuple(chunk.heading_path),
                    excerpt=excerpt,
                    score=hit.score,
                    rank=len(sources) + 1,
                )
            )
        degraded = False
        if self._reranker is not None and len(sources) > 1:
            try:
                order = await self._reranker.rerank(query, [source.excerpt for source in sources])
                if sorted(order) != list(range(len(sources))):
                    raise ValueError("invalid_rerank_result")
                sources = [sources[index] for index in order]
            except Exception:
                degraded = True
        return [
            replace(source, citation_id=f"[{rank}]", rank=rank)
            for rank, source in enumerate(sources[:limit], 1)
        ], degraded

    async def read(
        self,
        scope_ids: tuple[UUID, ...],
        *,
        document_id: UUID | None = None,
        chunk_id: UUID | None = None,
    ) -> list[Source]:
        if (document_id is None) == (chunk_id is None):
            raise ValueError("document_or_chunk_required")
        if not scope_ids:
            return []
        async with self._sessions() as session:
            base_query = (
                select(Chunk, Document)
                .join(Document, Document.active_version_id == Chunk.document_version_id)
                .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
                .where(
                    Document.deleted_at.is_(None),
                    KnowledgeBase.workspace_id == DEFAULT_WORKSPACE_ID,
                    KnowledgeBase.enabled.is_(True),
                    KnowledgeBase.id.in_(scope_ids),
                )
            )
            if chunk_id is not None:
                matched = (await session.execute(base_query.where(Chunk.id == chunk_id))).first()
                if matched is None:
                    return []
                chunk, document = matched
                parent_id = chunk.parent_chunk_id or chunk.id
                query = base_query.where(
                    Chunk.document_version_id == chunk.document_version_id,
                    Chunk.kind == ChunkKind.PARENT.value,
                    or_(
                        Chunk.id == parent_id,
                        Chunk.ordinal.between(max(0, chunk.ordinal - 2), chunk.ordinal + 2),
                    ),
                )
            else:
                query = base_query.where(
                    Document.id == document_id,
                    Chunk.kind == ChunkKind.PARENT.value,
                )
            rows = list((await session.execute(query.order_by(Chunk.ordinal).limit(3))).all())
        return [
            Source(
                citation_id=f"[{rank}]",
                chunk_id=chunk.id,
                document_id=document.id,
                document_version_id=chunk.document_version_id,
                title=document.title,
                filename=document.filename,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                heading_path=tuple(chunk.heading_path),
                excerpt=chunk.content[:1500],
                score=0.0,
                rank=rank,
            )
            for rank, (chunk, document) in enumerate(rows, 1)
        ]

    async def list_documents(
        self,
        scope_ids: tuple[UUID, ...],
        *,
        title: str = "",
        limit: int = 20,
    ) -> list[dict[str, str]]:
        if not scope_ids:
            return []
        if not 1 <= limit <= 20:
            raise ValueError("invalid_document_limit")
        async with self._sessions() as session:
            query = (
                select(Document.id, Document.title, Document.filename)
                .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
                .where(
                    Document.active_version_id.is_not(None),
                    Document.deleted_at.is_(None),
                    KnowledgeBase.workspace_id == DEFAULT_WORKSPACE_ID,
                    KnowledgeBase.enabled.is_(True),
                    KnowledgeBase.id.in_(scope_ids),
                )
            )
            if title.strip():
                escaped = (
                    title.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                )
                query = query.where(
                    or_(
                        Document.title.ilike(f"%{escaped}%", escape="\\"),
                        Document.filename.ilike(f"%{escaped}%", escape="\\"),
                    )
                )
            rows = (
                await session.execute(query.order_by(Document.title, Document.id).limit(limit))
            ).all()
        return [
            {"document_id": str(document_id), "title": document_title, "filename": filename}
            for document_id, document_title, filename in rows
        ]
