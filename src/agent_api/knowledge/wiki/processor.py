from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_api.knowledge.domain.events import OutboxEventType
from agent_api.knowledge.infrastructure.database.models import (
    Chunk,
    Document,
    OutboxEvent,
    WikiClaim,
    WikiClaimSource,
    WikiConfig,
    WikiJob,
    WikiPage,
    WikiPageVersion,
    WikiTokenUsage,
)
from agent_api.knowledge.wiki.generator import WikiGeneration, WikiGenerator


class WikiProcessor:
    """把活动文档版本生成可恢复的 Wiki 页面。"""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], generator: WikiGenerator
    ) -> None:
        self._sessions = sessions
        self._generator = generator

    @staticmethod
    async def recover_stale(
        sessions: async_sessionmaker[AsyncSession], *, limit: int = 100
    ) -> int:
        """重新投递心跳超时的任务，保留生成检查点和已计费记录。"""

        # 生成可能包含多轮摘要调用，超时阈值需大于 Worker 的 30 分钟硬限。
        stale_before = datetime.now(UTC) - timedelta(minutes=35)
        async with sessions() as session, session.begin():
            jobs = list(
                await session.scalars(
                    select(WikiJob)
                    .where(
                        WikiJob.status == "running",
                        WikiJob.heartbeat_at < stale_before,
                    )
                    .order_by(WikiJob.heartbeat_at)
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
            )
            for job in jobs:
                session.add(
                    OutboxEvent(
                        event_key=f"wiki-recover:{job.id}:{uuid4()}",
                        event_type=OutboxEventType.WIKI_DOCUMENT_GENERATE_REQUESTED,
                        aggregate_id=job.id,
                        payload={"job_id": str(job.id)},
                    )
                )
            return len(jobs)

    async def run(self, job_id: UUID) -> None:
        context = await self._claim(job_id)
        if context is None:
            return
        title, chunks, reserved, checkpoint = context
        if checkpoint is None:
            try:
                generated = await self._generator.generate(
                    title, [chunk.content for chunk in chunks]
                )
            except Exception:
                await self._fail(job_id)
                return
            await self._settle(job_id, generated, reserved)
        await self._publish(job_id)

    async def _claim(
        self, job_id: UUID
    ) -> tuple[str, list[Chunk], int, dict[str, object] | None] | None:
        async with self._sessions() as session, session.begin():
            job = await session.get(WikiJob, job_id, with_for_update=True)
            if job is None or job.status not in {"pending", "retry_wait", "running"}:
                return None
            now = datetime.now(UTC)
            if (
                job.status == "retry_wait"
                and job.next_retry_at is not None
                and job.next_retry_at > now
            ):
                return None
            if (
                job.status == "running"
                and job.heartbeat_at is not None
                and job.heartbeat_at > now - timedelta(minutes=35)
            ):
                return None
            config = await session.get(WikiConfig, job.knowledge_base_id, with_for_update=True)
            if config is None or not config.enabled:
                job.status = "paused_disabled"
                job.stage = "paused"
                return None
            if job.document_version_id is None:
                job.status = "failed"
                job.error_code = "wiki_missing_source_version"
                return None
            document = await session.scalar(
                select(Document).where(
                    Document.active_version_id == job.document_version_id,
                    Document.knowledge_base_id == job.knowledge_base_id,
                    Document.deleted_at.is_(None),
                    Document.status == "ready",
                )
            )
            if document is None:
                job.status = "superseded"
                job.stage = "source_changed"
                return None
            chunks = list(
                await session.scalars(
                    select(Chunk)
                    .where(
                        Chunk.document_version_id == job.document_version_id,
                        Chunk.kind == "child",
                    )
                    .order_by(Chunk.ordinal)
                )
            )
            if not chunks:
                job.status = "failed"
                job.error_code = "wiki_no_source_chunks"
                return None
            checkpoint = job.payload.get("generation")
            if isinstance(checkpoint, dict):
                job.status = "running"
                job.stage = "publishing"
                job.heartbeat_at = datetime.now(UTC)
                return document.title, chunks, 0, checkpoint
            if job.reserved_tokens:
                config.tokens_reserved = max(0, config.tokens_reserved - job.reserved_tokens)
                job.reserved_tokens = 0
            batches = (len(chunks) + 11) // 12
            calls = batches
            while batches > 1:
                batches = (batches + 11) // 12
                calls += batches
            # 中文输入按字符数保守预留，并为每次模型输出和汇总留出余量。
            estimate = sum(min(len(chunk.content), 1600) for chunk in chunks)
            estimate += 1500 * calls
            job.payload = {**job.payload, "estimated_tokens": estimate}
            if config.tokens_charged + config.tokens_reserved + estimate > config.token_limit:
                job.status = "paused_budget"
                job.stage = "paused"
                job.reserved_tokens = 0
                return None
            config.tokens_reserved += estimate
            job.reserved_tokens = estimate
            job.status = "running"
            job.stage = "generating"
            job.attempt_count += 1
            job.heartbeat_at = datetime.now(UTC)
            return document.title, chunks, estimate, None

    async def _settle(self, job_id: UUID, result: WikiGeneration, reserved: int) -> None:
        async with self._sessions() as session, session.begin():
            job = await session.get(WikiJob, job_id, with_for_update=True)
            if job is None:
                return
            config = await session.get(WikiConfig, job.knowledge_base_id, with_for_update=True)
            if config is None:
                return
            charged = result.measured_tokens if result.measured_tokens is not None else reserved
            call_key = f"wiki-generate:{job.id}"
            existing = await session.scalar(
                select(WikiTokenUsage).where(WikiTokenUsage.call_key == call_key)
            )
            if existing is None:
                session.add(
                    WikiTokenUsage(
                        knowledge_base_id=job.knowledge_base_id,
                        job_id=job.id,
                        call_key=call_key,
                        model=self._generator.model_name,
                        reserved_tokens=reserved,
                        measured_tokens=result.measured_tokens,
                        charged_tokens=charged,
                    )
                )
                config.tokens_charged += charged
            config.tokens_reserved = max(0, config.tokens_reserved - job.reserved_tokens)
            job.reserved_tokens = 0
            job.payload = {
                **job.payload,
                "generation": {"summary": result.summary, "topics": list(result.topics)},
            }
            job.stage = "publishing"
            job.heartbeat_at = datetime.now(UTC)

    async def _fail(self, job_id: UUID) -> None:
        async with self._sessions() as session, session.begin():
            job = await session.get(WikiJob, job_id, with_for_update=True)
            if job is None:
                return
            config = await session.get(WikiConfig, job.knowledge_base_id, with_for_update=True)
            if config is not None:
                config.tokens_reserved = max(0, config.tokens_reserved - job.reserved_tokens)
            job.reserved_tokens = 0
            job.error_code = "wiki_generation_failed"
            if job.attempt_count >= job.max_attempts:
                job.status = "failed"
                job.stage = "failed"
                return
            job.status = "retry_wait"
            job.stage = "retry_wait"
            delay = min(300, 2**job.attempt_count)
            job.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
            session.add(
                OutboxEvent(
                    event_key=f"wiki-retry:{job.id}:{job.attempt_count}",
                    event_type=OutboxEventType.WIKI_DOCUMENT_GENERATE_REQUESTED,
                    aggregate_id=job.id,
                    payload={"job_id": str(job.id)},
                    available_at=job.next_retry_at,
                )
            )

    async def _publish(self, job_id: UUID) -> None:
        async with self._sessions() as session, session.begin():
            job = await session.get(WikiJob, job_id, with_for_update=True)
            if job is None or job.status != "running" or job.document_version_id is None:
                return
            config = await session.get(WikiConfig, job.knowledge_base_id, with_for_update=True)
            if config is None or not config.enabled:
                job.status = "paused_disabled"
                job.stage = "paused"
                return
            document = await session.scalar(
                select(Document).where(
                    Document.active_version_id == job.document_version_id,
                    Document.knowledge_base_id == job.knowledge_base_id,
                    Document.status == "ready",
                    Document.deleted_at.is_(None),
                )
            )
            if document is None:
                job.status = "superseded"
                job.stage = "source_changed"
                return
            data = job.payload.get("generation")
            if not isinstance(data, dict):
                job.status = "failed"
                job.error_code = "wiki_generation_checkpoint_missing"
                return
            summary = str(data.get("summary", ""))
            topics = [str(item) for item in data.get("topics", []) if isinstance(item, str)]
            chunks = list(
                await session.scalars(
                    select(Chunk)
                    .where(
                        Chunk.document_version_id == job.document_version_id, Chunk.kind == "child"
                    )
                    .order_by(Chunk.ordinal)
                )
            )
            body = f"# {document.title}\n\n{summary}\n\n## 原文依据\n"
            for chunk in chunks:
                body += f"\n- {chunk.content[:350].strip()} [^chunk-{chunk.ordinal}]"
            _, version, created = await self._upsert_page(
                session,
                job.knowledge_base_id,
                slug=f"summary/{document.id}",
                page_type="summary",
                title=document.title,
                content=body,
                summary=summary,
                metadata={
                    "document_id": str(document.id),
                    "document_version_id": str(job.document_version_id),
                    "topics": topics,
                },
            )
            for chunk in chunks if created else []:
                claim = WikiClaim(
                    page_version_id=version.id,
                    claim_key=f"chunk-{chunk.ordinal}",
                    text=chunk.content[:350].strip(),
                    trust_state="verified",
                )
                session.add(claim)
                await session.flush()
                session.add(
                    WikiClaimSource(
                        claim_id=claim.id,
                        document_id=document.id,
                        document_version_id=job.document_version_id,
                        chunk_id=chunk.id,
                        chunk_kind=chunk.kind,
                        chunk_ordinal=chunk.ordinal,
                        content_sha256=sha256(chunk.content.encode()).hexdigest(),
                        document_title=document.title,
                        heading_path=list(chunk.heading_path),
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                    )
                )
            await self._rebuild_topic_pages(session, job.knowledge_base_id)
            await self._rebuild_index(session, job.knowledge_base_id)
            job.status = "succeeded"
            job.stage = "ready"
            job.progress = 100
            job.heartbeat_at = datetime.now(UTC)

    @staticmethod
    async def _upsert_page(
        session: AsyncSession,
        knowledge_base_id: UUID,
        *,
        slug: str,
        page_type: str,
        title: str,
        content: str,
        summary: str,
        metadata: dict[str, object],
    ) -> tuple[WikiPage, WikiPageVersion, bool]:
        page = await session.scalar(
            select(WikiPage).where(
                WikiPage.knowledge_base_id == knowledge_base_id,
                WikiPage.slug == slug,
            )
        )
        if page is None:
            page = WikiPage(
                knowledge_base_id=knowledge_base_id,
                slug=slug,
                page_type=page_type,
                title=title,
            )
            session.add(page)
            await session.flush()
        current = (
            await session.get(WikiPageVersion, page.current_version_id)
            if page.current_version_id
            else None
        )
        if (
            current is not None
            and current.content == content
            and current.generation_metadata == metadata
        ):
            return page, current, False
        highest = await session.scalar(
            select(WikiPageVersion.version_no)
            .where(WikiPageVersion.page_id == page.id)
            .order_by(WikiPageVersion.version_no.desc())
            .limit(1)
        )
        pending = current is not None and current.origin in {"user", "revert"}
        if pending:
            old_candidates = list(
                await session.scalars(
                    select(WikiPageVersion).where(
                        WikiPageVersion.page_id == page.id,
                        WikiPageVersion.review_state == "pending_review",
                    )
                )
            )
            for candidate in old_candidates:
                candidate.review_state = "superseded"
        version = WikiPageVersion(
            page_id=page.id,
            version_no=int(highest or 0) + 1,
            title=title,
            content=content,
            summary=summary,
            generation_metadata=metadata,
            origin="pipeline",
            review_state="pending_review" if pending else "published",
            base_published_version_id=page.current_version_id,
        )
        session.add(version)
        await session.flush()
        if not pending:
            page.title = title
            page.current_version_id = version.id
        return page, version, True

    @staticmethod
    async def _rebuild_topic_pages(session: AsyncSession, knowledge_base_id: UUID) -> None:
        summaries = list(
            await session.execute(
                select(WikiPage, WikiPageVersion)
                .join(WikiPageVersion, WikiPage.current_version_id == WikiPageVersion.id)
                .where(
                    WikiPage.knowledge_base_id == knowledge_base_id, WikiPage.page_type == "summary"
                )
            )
        )
        by_topic: dict[str, list[tuple[WikiPage, WikiPageVersion]]] = {}
        for page, version in summaries:
            for topic in version.generation_metadata.get("topics", []):
                if isinstance(topic, str) and topic.strip():
                    by_topic.setdefault(topic.strip().casefold(), []).append((page, version))
        for key, items in by_topic.items():
            if len(items) < 2:
                continue
            title = key[:80]
            slug = f"topic/{sha256(key.encode()).hexdigest()[:16]}"
            lines = [f"# {title}", "", "来自多篇文档的相关内容："]
            for page, version in items:
                lines.append(
                    f"\n## {page.title}\n\n{version.summary}\n\n参见 [[{page.slug}|{page.title}]]"
                )
            _, topic_version, created = await WikiProcessor._upsert_page(
                session,
                knowledge_base_id,
                slug=slug,
                page_type="topic",
                title=title,
                content="\n".join(lines),
                summary=f"{len(items)} 篇文档涉及 {title}",
                metadata={
                    "summary_page_ids": [str(item[0].id) for item in items],
                    "summary_version_ids": [str(item[1].id) for item in items],
                },
            )
            if not created:
                continue
            for source_page, source_version in items:
                claims = list(
                    await session.scalars(
                        select(WikiClaim).where(WikiClaim.page_version_id == source_version.id)
                    )
                )
                for source_claim in claims:
                    claim = WikiClaim(
                        page_version_id=topic_version.id,
                        claim_key=f"{source_page.id}:{source_claim.claim_key}",
                        text=source_claim.text,
                        trust_state=source_claim.trust_state,
                        reason=source_claim.reason,
                    )
                    session.add(claim)
                    await session.flush()
                    sources = list(
                        await session.scalars(
                            select(WikiClaimSource).where(
                                WikiClaimSource.claim_id == source_claim.id
                            )
                        )
                    )
                    for source in sources:
                        session.add(
                            WikiClaimSource(
                                claim_id=claim.id,
                                document_id=source.document_id,
                                document_version_id=source.document_version_id,
                                chunk_id=source.chunk_id,
                                chunk_kind=source.chunk_kind,
                                chunk_ordinal=source.chunk_ordinal,
                                content_sha256=source.content_sha256,
                                document_title=source.document_title,
                                heading_path=list(source.heading_path),
                                page_start=source.page_start,
                                page_end=source.page_end,
                            )
                        )

    @staticmethod
    async def _rebuild_index(session: AsyncSession, knowledge_base_id: UUID) -> None:
        pages = list(
            await session.scalars(
                select(WikiPage)
                .where(
                    WikiPage.knowledge_base_id == knowledge_base_id,
                    WikiPage.page_type.in_(["summary", "topic"]),
                    WikiPage.current_version_id.is_not(None),
                )
                .order_by(WikiPage.page_type, WikiPage.title)
            )
        )
        lines = ["# Wiki 目录", "", "## 主题"]
        lines.extend(
            f"- [[{page.slug}|{page.title}]]" for page in pages if page.page_type == "topic"
        )
        lines.append("\n## 文档摘要")
        lines.extend(
            f"- [[{page.slug}|{page.title}]]" for page in pages if page.page_type == "summary"
        )
        await WikiProcessor._upsert_page(
            session,
            knowledge_base_id,
            slug="index/home",
            page_type="index",
            title="Wiki 目录",
            content="\n".join(lines),
            summary="知识库 Wiki 目录",
            metadata={},
        )
