from __future__ import annotations

from hashlib import sha256
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_api.knowledge.application.management import DEFAULT_WORKSPACE_ID
from agent_api.knowledge.domain.events import OutboxEventType
from agent_api.knowledge.infrastructure.database.models import (
    Chunk,
    Document,
    KnowledgeBase,
    OutboxEvent,
    WikiClaim,
    WikiClaimSource,
    WikiConfig,
    WikiJob,
    WikiPage,
    WikiPageVersion,
    WikiTokenUsage,
)
from agent_api.knowledge.wiki.jobs import schedule_document_wiki
from agent_api.knowledge.wiki.processor import WikiProcessor


class WikiError(RuntimeError):
    """对外保持稳定错误码的 Wiki 业务错误。"""

    def __init__(self, code: str, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class WikiService:
    """负责 Wiki 配置、页面版本与可信来源的事务边界。"""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def _base(self, session: AsyncSession, knowledge_base_id: UUID) -> KnowledgeBase:
        base = await session.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.workspace_id == DEFAULT_WORKSPACE_ID,
            )
        )
        if base is None:
            raise WikiError("knowledge_base_not_found", 404)
        return base

    async def get_config(self, knowledge_base_id: UUID) -> dict[str, Any]:
        async with self._sessions() as session:
            await self._base(session, knowledge_base_id)
            config = await session.get(WikiConfig, knowledge_base_id)
            return self._config_response(config, knowledge_base_id)

    async def update_config(
        self,
        knowledge_base_id: UUID,
        *,
        enabled: bool | None,
        token_limit: int | None,
    ) -> dict[str, Any]:
        async with self._sessions() as session, session.begin():
            base = await self._base(session, knowledge_base_id)
            await session.refresh(base, with_for_update=True)
            config = await session.get(WikiConfig, knowledge_base_id, with_for_update=True)
            if config is None:
                config = WikiConfig(knowledge_base_id=knowledge_base_id)
                session.add(config)
                await session.flush()
            was_enabled = config.enabled
            previous_limit = config.token_limit
            if token_limit is not None:
                if token_limit < config.tokens_charged + config.tokens_reserved:
                    raise WikiError("token_limit_below_usage", 409)
                config.token_limit = token_limit
            if enabled is True and config.token_limit <= 0:
                raise WikiError("token_limit_required", 422)
            if enabled is not None:
                config.enabled = enabled
            if config.enabled and not was_enabled:
                versions = list(
                    await session.scalars(
                        select(Document.active_version_id).where(
                            Document.knowledge_base_id == knowledge_base_id,
                            Document.status == "ready",
                            Document.deleted_at.is_(None),
                            Document.active_version_id.is_not(None),
                        )
                    )
                )
                for version_id in versions:
                    if version_id is not None:
                        await schedule_document_wiki(session, knowledge_base_id, version_id)
            if config.enabled and (not was_enabled or config.token_limit > previous_limit):
                paused_jobs = list(
                    await session.scalars(
                        select(WikiJob).where(
                            WikiJob.knowledge_base_id == knowledge_base_id,
                            WikiJob.status.in_(["paused_budget", "paused_disabled"]),
                        )
                    )
                )
                for job in paused_jobs:
                    job.status = "pending"
                    job.stage = "pending"
                    session.add(
                        OutboxEvent(
                            event_key=f"wiki-resume:{job.id}:{uuid4()}",
                            event_type=OutboxEventType.WIKI_DOCUMENT_GENERATE_REQUESTED,
                            aggregate_id=job.id,
                            payload={"job_id": str(job.id)},
                        )
                    )
            return self._config_response(config, knowledge_base_id)

    @staticmethod
    def _config_response(config: WikiConfig | None, knowledge_base_id: UUID) -> dict[str, Any]:
        return {
            "knowledge_base_id": knowledge_base_id,
            "enabled": bool(config.enabled) if config else False,
            "token_limit": int(config.token_limit) if config else 0,
            "tokens_reserved": int(config.tokens_reserved) if config else 0,
            "tokens_charged": int(config.tokens_charged) if config else 0,
        }

    async def _page(
        self, session: AsyncSession, knowledge_base_id: UUID, page_id: UUID, *, lock: bool = False
    ) -> WikiPage:
        query = select(WikiPage).where(
            WikiPage.id == page_id,
            WikiPage.knowledge_base_id == knowledge_base_id,
            WikiPage.deleted_at.is_(None),
        )
        if lock:
            query = query.with_for_update()
        page = await session.scalar(query)
        if page is None:
            raise WikiError("wiki_page_not_found", 404)
        return page

    async def list_pages(
        self,
        knowledge_base_id: UUID,
        *,
        page_type: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        async with self._sessions() as session:
            await self._base(session, knowledge_base_id)
            query = select(WikiPage).where(
                WikiPage.knowledge_base_id == knowledge_base_id,
                WikiPage.deleted_at.is_(None),
                WikiPage.current_version_id.is_not(None),
            )
            if page_type is not None:
                query = query.where(WikiPage.page_type == page_type)
            if cursor is not None:
                query = query.where(WikiPage.slug > cursor)
            pages = list(await session.scalars(query.order_by(WikiPage.slug).limit(limit)))
            return [
                {
                    "id": page.id,
                    "knowledge_base_id": page.knowledge_base_id,
                    "slug": page.slug,
                    "page_type": page.page_type,
                    "title": page.title,
                    "current_version_id": page.current_version_id,
                }
                for page in pages
            ]

    async def get_page(self, knowledge_base_id: UUID, page_id: UUID) -> dict[str, Any]:
        async with self._sessions() as session:
            await self._base(session, knowledge_base_id)
            page = await self._page(session, knowledge_base_id, page_id)
            if page.current_version_id is None:
                raise WikiError("wiki_page_not_published", 404)
            version = await session.get(WikiPageVersion, page.current_version_id)
            if version is None:
                raise WikiError("wiki_page_version_not_found", 404)
            claims = await self._claims_response(session, version.id)
            return self._page_response(page, version, claims)

    @staticmethod
    def _page_response(
        page: WikiPage, version: WikiPageVersion, claims: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "id": page.id,
            "knowledge_base_id": page.knowledge_base_id,
            "slug": page.slug,
            "page_type": page.page_type,
            "title": page.title,
            "current_version_id": version.id,
            "version_no": version.version_no,
            "content": version.content,
            "summary": version.summary,
            "origin": version.origin,
            "claims": claims,
        }

    async def _claims_response(
        self, session: AsyncSession, page_version_id: UUID
    ) -> list[dict[str, Any]]:
        claims = list(
            await session.scalars(
                select(WikiClaim)
                .where(WikiClaim.page_version_id == page_version_id)
                .order_by(WikiClaim.claim_key)
            )
        )
        output: list[dict[str, Any]] = []
        for claim in claims:
            sources = list(
                await session.scalars(
                    select(WikiClaimSource).where(WikiClaimSource.claim_id == claim.id)
                )
            )
            rendered_sources = []
            for source in sources:
                valid = await self._source_valid(session, source)
                rendered_sources.append(
                    {
                        "document_id": source.document_id,
                        "document_version_id": source.document_version_id,
                        "chunk_id": source.chunk_id,
                        "document_title": source.document_title,
                        "heading_path": source.heading_path,
                        "page_start": source.page_start,
                        "page_end": source.page_end,
                        "valid": valid,
                    }
                )
            trusted = (
                claim.trust_state == "verified"
                and bool(rendered_sources)
                and all(item["valid"] for item in rendered_sources)
            )
            output.append(
                {
                    "id": claim.id,
                    "claim_key": claim.claim_key,
                    "text": claim.text,
                    "trust_state": "verified" if trusted else "needs_review",
                    "reason": claim.reason if trusted else claim.reason or "source_not_current",
                    "sources": rendered_sources,
                }
            )
        return output

    @staticmethod
    async def _source_valid(session: AsyncSession, source: WikiClaimSource) -> bool:
        document = await session.get(Document, source.document_id)
        if (
            document is None
            or document.deleted_at is not None
            or document.status != "ready"
            or document.active_version_id != source.document_version_id
        ):
            return False
        chunk = await session.scalar(
            select(Chunk).where(
                Chunk.document_version_id == source.document_version_id,
                Chunk.kind == source.chunk_kind,
                Chunk.ordinal == source.chunk_ordinal,
            )
        )
        return (
            chunk is not None
            and sha256(chunk.content.encode()).hexdigest() == source.content_sha256
        )

    async def list_versions(self, knowledge_base_id: UUID, page_id: UUID) -> list[dict[str, Any]]:
        async with self._sessions() as session:
            await self._base(session, knowledge_base_id)
            await self._page(session, knowledge_base_id, page_id)
            versions = list(
                await session.scalars(
                    select(WikiPageVersion)
                    .where(WikiPageVersion.page_id == page_id)
                    .order_by(WikiPageVersion.version_no.desc())
                )
            )
            return [self._version_response(item) for item in versions]

    @staticmethod
    def _version_response(version: WikiPageVersion) -> dict[str, Any]:
        return {
            "id": version.id,
            "page_id": version.page_id,
            "version_no": version.version_no,
            "title": version.title,
            "content": version.content,
            "summary": version.summary,
            "origin": version.origin,
            "review_state": version.review_state,
            "base_published_version_id": version.base_published_version_id,
            "created_at": version.created_at,
        }

    async def edit_page(
        self,
        knowledge_base_id: UUID,
        page_id: UUID,
        *,
        base_version: UUID,
        title: str,
        content: str,
    ) -> dict[str, Any]:
        async with self._sessions() as session, session.begin():
            await self._base(session, knowledge_base_id)
            page = await self._page(session, knowledge_base_id, page_id, lock=True)
            if page.current_version_id != base_version:
                raise WikiError("wiki_version_conflict", 409)
            current = await session.get(WikiPageVersion, base_version)
            if current is None:
                raise WikiError("wiki_page_version_not_found", 404)
            version = WikiPageVersion(
                page_id=page.id,
                version_no=await self._next_version_no(session, page.id),
                title=title,
                content=content,
                summary=current.summary,
                generation_metadata=dict(current.generation_metadata),
                origin="user",
                review_state="published",
                base_published_version_id=current.id,
            )
            session.add(version)
            await session.flush()
            session.add(
                WikiClaim(
                    page_version_id=version.id,
                    claim_key="manual-content",
                    text=content,
                    trust_state="needs_review",
                    reason="manual_edit_without_source",
                )
            )
            await session.flush()
            page.title = title
            page.current_version_id = version.id
            await WikiProcessor._rebuild_topic_pages(session, knowledge_base_id)
            await WikiProcessor._rebuild_index(session, knowledge_base_id)
            return self._page_response(
                page, version, await self._claims_response(session, version.id)
            )

    async def revert_page(
        self,
        knowledge_base_id: UUID,
        page_id: UUID,
        *,
        base_version: UUID,
        target_version: UUID,
    ) -> dict[str, Any]:
        async with self._sessions() as session, session.begin():
            await self._base(session, knowledge_base_id)
            page = await self._page(session, knowledge_base_id, page_id, lock=True)
            if page.current_version_id != base_version:
                raise WikiError("wiki_version_conflict", 409)
            target = await session.scalar(
                select(WikiPageVersion).where(
                    WikiPageVersion.id == target_version,
                    WikiPageVersion.page_id == page.id,
                )
            )
            if target is None:
                raise WikiError("wiki_page_version_not_found", 404)
            if target.id == base_version:
                raise WikiError("wiki_revert_current_version", 400)
            version = WikiPageVersion(
                page_id=page.id,
                version_no=await self._next_version_no(session, page.id),
                title=target.title,
                content=target.content,
                summary=target.summary,
                generation_metadata=dict(target.generation_metadata),
                origin="revert",
                review_state="published",
                base_published_version_id=base_version,
            )
            session.add(version)
            await session.flush()
            await self._copy_claims(session, target.id, version.id)
            page.title = target.title
            page.current_version_id = version.id
            await WikiProcessor._rebuild_topic_pages(session, knowledge_base_id)
            await WikiProcessor._rebuild_index(session, knowledge_base_id)
            return self._page_response(
                page, version, await self._claims_response(session, version.id)
            )

    async def review_page(
        self,
        knowledge_base_id: UUID,
        page_id: UUID,
        *,
        candidate_version_id: UUID,
        base_version: UUID,
        decision: Literal["publish", "reject"],
    ) -> dict[str, Any]:
        async with self._sessions() as session, session.begin():
            await self._base(session, knowledge_base_id)
            page = await self._page(session, knowledge_base_id, page_id, lock=True)
            candidate = await session.scalar(
                select(WikiPageVersion).where(
                    WikiPageVersion.id == candidate_version_id,
                    WikiPageVersion.page_id == page.id,
                )
            )
            if candidate is None:
                raise WikiError("wiki_page_version_not_found", 404)
            final_state = "published" if decision == "publish" else "rejected"
            if candidate.review_state == final_state:
                return self._version_response(candidate)
            if candidate.review_state != "pending_review":
                raise WikiError("wiki_candidate_not_pending", 409)
            if (
                page.current_version_id != base_version
                or candidate.base_published_version_id != base_version
            ):
                raise WikiError("wiki_version_conflict", 409)
            candidate.review_state = final_state
            if decision == "publish":
                page.title = candidate.title
                page.current_version_id = candidate.id
                await WikiProcessor._rebuild_topic_pages(session, knowledge_base_id)
                await WikiProcessor._rebuild_index(session, knowledge_base_id)
            return self._version_response(candidate)

    @staticmethod
    async def _next_version_no(session: AsyncSession, page_id: UUID) -> int:
        highest = await session.scalar(
            select(func.max(WikiPageVersion.version_no)).where(WikiPageVersion.page_id == page_id)
        )
        return int(highest or 0) + 1

    @staticmethod
    async def _copy_claims(
        session: AsyncSession, source_version_id: UUID, new_version_id: UUID
    ) -> None:
        source_claims = list(
            await session.scalars(
                select(WikiClaim).where(WikiClaim.page_version_id == source_version_id)
            )
        )
        for source_claim in source_claims:
            claim = WikiClaim(
                page_version_id=new_version_id,
                claim_key=source_claim.claim_key,
                text=source_claim.text,
                trust_state=source_claim.trust_state,
                reason=source_claim.reason,
            )
            session.add(claim)
            await session.flush()
            sources = list(
                await session.scalars(
                    select(WikiClaimSource).where(WikiClaimSource.claim_id == source_claim.id)
                )
            )
            for item in sources:
                session.add(
                    WikiClaimSource(
                        claim_id=claim.id,
                        document_id=item.document_id,
                        document_version_id=item.document_version_id,
                        chunk_id=item.chunk_id,
                        chunk_kind=item.chunk_kind,
                        chunk_ordinal=item.chunk_ordinal,
                        content_sha256=item.content_sha256,
                        document_title=item.document_title,
                        heading_path=list(item.heading_path),
                        page_start=item.page_start,
                        page_end=item.page_end,
                    )
                )
        await session.flush()

    async def list_jobs(self, knowledge_base_id: UUID, *, limit: int = 50) -> list[dict[str, Any]]:
        async with self._sessions() as session:
            await self._base(session, knowledge_base_id)
            jobs = list(
                await session.scalars(
                    select(WikiJob)
                    .where(WikiJob.knowledge_base_id == knowledge_base_id)
                    .order_by(WikiJob.created_at.desc())
                    .limit(limit)
                )
            )
            output = []
            for job in jobs:
                usage = await session.scalar(
                    select(WikiTokenUsage).where(WikiTokenUsage.job_id == job.id)
                )
                output.append({
                    "id": job.id,
                    "job_type": job.job_type,
                    "status": job.status,
                    "stage": job.stage,
                    "progress": job.progress,
                    "error_code": job.error_code,
                    "estimated_tokens": int(job.payload.get("estimated_tokens", 0)),
                    "actual_tokens": usage.measured_tokens if usage else None,
                    "charged_tokens": usage.charged_tokens if usage else 0,
                    "created_at": job.created_at,
                })
            return output

    async def search_pages(
        self, knowledge_base_id: UUID, query: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        async with self._sessions() as session:
            await self._base(session, knowledge_base_id)
            pattern = f"%{query.strip()}%"
            pages = list(
                await session.scalars(
                    select(WikiPage)
                    .join(WikiPageVersion, WikiPage.current_version_id == WikiPageVersion.id)
                    .where(
                        WikiPage.knowledge_base_id == knowledge_base_id,
                        WikiPage.deleted_at.is_(None),
                        or_(
                            WikiPage.title.ilike(pattern),
                            WikiPage.slug.ilike(pattern),
                            WikiPageVersion.content.ilike(pattern),
                        ),
                    )
                    .order_by(WikiPage.title, WikiPage.id)
                    .limit(limit)
                )
            )
            return [
                {"id": page.id, "slug": page.slug, "title": page.title, "page_type": page.page_type}
                for page in pages
            ]

    async def agent_pages(
        self, knowledge_base_id: UUID, query: str, *, limit: int = 5
    ) -> list[dict[str, Any]]:
        """只向 Agent 提供启用状态下、来源仍有效的结论。"""

        async with self._sessions() as session:
            base = await self._base(session, knowledge_base_id)
            config = await session.get(WikiConfig, knowledge_base_id)
            if not base.enabled or config is None or not config.enabled:
                return []
            pattern = f"%{query.strip()}%"
            pages = list(
                await session.scalars(
                    select(WikiPage)
                    .join(WikiPageVersion, WikiPage.current_version_id == WikiPageVersion.id)
                    .where(
                        WikiPage.knowledge_base_id == knowledge_base_id,
                        WikiPage.deleted_at.is_(None),
                        WikiPage.page_type != "index",
                        or_(
                            WikiPage.title.ilike(pattern),
                            WikiPageVersion.content.ilike(pattern),
                        ),
                    )
                    .order_by(WikiPage.title)
                    .limit(limit)
                )
            )
            output: list[dict[str, Any]] = []
            for page in pages:
                if page.current_version_id is None:
                    continue
                claims = await self._claims_response(session, page.current_version_id)
                trusted = [claim for claim in claims if claim["trust_state"] == "verified"]
                if trusted:
                    output.append(
                        {"page_id": page.id, "title": page.title, "claims": trusted[:8]}
                    )
            return output

    async def agent_read_page(
        self, knowledge_base_id: UUID, page_id: UUID
    ) -> dict[str, Any] | None:
        """读取页面的可信结论；人工正文和失效引用不能进入工具输出。"""

        async with self._sessions() as session:
            base = await self._base(session, knowledge_base_id)
            config = await session.get(WikiConfig, knowledge_base_id)
            if not base.enabled or config is None or not config.enabled:
                return None
            page = await self._page(session, knowledge_base_id, page_id)
            if page.current_version_id is None:
                return None
            claims = await self._claims_response(session, page.current_version_id)
            return {
                "page_id": page.id,
                "title": page.title,
                "claims": [claim for claim in claims if claim["trust_state"] == "verified"][:8],
            }
