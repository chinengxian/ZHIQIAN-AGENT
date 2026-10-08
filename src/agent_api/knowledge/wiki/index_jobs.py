from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_api.knowledge.domain.events import OutboxEventType
from agent_api.knowledge.infrastructure.database.models import (
    Chunk,
    Document,
    OutboxEvent,
    WikiClaim,
    WikiClaimSource,
    WikiJob,
    WikiPage,
    WikiPageAlias,
    WikiPageVersion,
)
from agent_api.knowledge.wiki.indexing import (
    WikiIndexJobLike,
    WikiIndexProjection,
    WikiPageVersionLike,
    WikiSourceSnapshot,
)

_INDEXABLE_CLAIM_TYPES = ("summary_section", "summary_fact", "topic_fact")
_CLEANUP_PENDING_STAGES = {
    "cleanup_pending_retry",
    "cleanup_pending_superseded",
    "cleanup_pending_conflict",
}


def _retry_delay(attempt_count: int) -> int:
    """按任务尝试次数计算最多五分钟的指数退避。"""

    return int(min(300, 2 ** min(max(attempt_count, 0), 8)))


def _setup_error_reference(job_id: UUID, stage: str, error: BaseException) -> str:
    """只哈希异常类型，不把异常文本、正文或凭据写入数据库。"""

    payload = f"{job_id}:{stage}:{type(error).__module__}.{type(error).__qualname__}"
    return sha256(payload.encode("utf-8")).hexdigest()[:24]


def source_matches_chunk(source: WikiClaimSource, chunk: Chunk) -> bool:
    """来源显式记录 chunk_id 时必须与定位字段解析出的分块完全一致。"""

    return (
        (source.chunk_id is None or source.chunk_id == chunk.id)
        and source.document_version_id == chunk.document_version_id
        and source.chunk_kind == chunk.kind
        and source.chunk_ordinal == chunk.ordinal
        and sha256(chunk.content.encode("utf-8")).hexdigest() == source.content_sha256
    )


def apply_page_index_setup_failure(
    job: WikiJob,
    version: WikiPageVersion,
    *,
    stage: str,
    error: BaseException,
    now: datetime,
) -> OutboxEvent | None:
    """把 Worker 装配失败收敛为可重试或终止状态。"""

    job.attempt_count += 1
    job.status = "retry_wait" if job.attempt_count < job.max_attempts else "failed"
    job.stage = "failed"
    job.heartbeat_at = now
    job.error_code = "wiki_index_worker_setup_failed"
    job.error_reference = _setup_error_reference(job.id, stage, error)
    version.index_state = "failed"
    version.embedding_fingerprint = None
    version.indexed_at = None
    version.index_error_code = job.error_code
    if job.status == "failed":
        job.next_retry_at = None
        return None
    job.next_retry_at = now + timedelta(seconds=_retry_delay(job.attempt_count))
    return OutboxEvent(
        event_key=f"wiki-index-setup-retry:{job.id}:{job.attempt_count}",
        event_type=OutboxEventType.WIKI_PAGE_INDEX_REQUESTED,
        aggregate_id=job.id,
        payload={"job_id": str(job.id)},
        available_at=job.next_retry_at,
    )


async def record_page_index_setup_failure(
    sessions: async_sessionmaker[AsyncSession],
    job_id: UUID,
    *,
    stage: str,
    error: BaseException,
) -> None:
    """在独立 PostgreSQL 事务中锁定任务并持久化装配失败。"""

    async with sessions() as session, session.begin():
        row = (
            await session.execute(
                select(WikiJob, WikiPageVersion)
                .join(WikiPageVersion, WikiJob.page_version_id == WikiPageVersion.id)
                .where(WikiJob.id == job_id, WikiJob.job_type == "page_index")
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            return
        job, version = row
        if job.status not in {"pending", "retry_wait", "running"}:
            return
        event = apply_page_index_setup_failure(
            job,
            version,
            stage=stage,
            error=error,
            now=datetime.now(UTC),
        )
        if event is not None:
            session.add(event)


async def schedule_page_index(
    session: AsyncSession,
    *,
    knowledge_base_id: UUID,
    page_version_id: UUID,
    fingerprint: str,
) -> UUID | None:
    """在调用方事务内幂等创建索引任务与最小 Outbox 事件。"""

    key = f"wiki-index:{page_version_id}:{fingerprint}"
    job_id = uuid4()
    inserted = await session.scalar(
        pg_insert(WikiJob)
        .values(
            id=job_id,
            knowledge_base_id=knowledge_base_id,
            page_version_id=page_version_id,
            document_version_id=None,
            embedding_fingerprint=fingerprint,
            job_type="page_index",
            status="pending",
            stage="pending",
            progress=0,
            attempt_count=0,
            max_attempts=5,
            reserved_tokens=0,
            payload={},
            idempotency_key=key,
        )
        .on_conflict_do_nothing(index_elements=[WikiJob.idempotency_key])
        .returning(WikiJob.id)
    )
    if inserted is None:
        return None
    session.add(
        OutboxEvent(
            event_key=key,
            event_type=OutboxEventType.WIKI_PAGE_INDEX_REQUESTED,
            aggregate_id=inserted,
            payload={"job_id": str(inserted)},
        )
    )
    return inserted


async def retry_page_index_job(
    session: AsyncSession,
    *,
    knowledge_base_id: UUID,
    page_version_id: UUID,
    fingerprint: str,
    failed_job: WikiJob | None,
) -> UUID:
    """同指纹复用失败任务；指纹变化时创建或精确复用新幂等键任务。"""

    expected_key = f"wiki-index:{page_version_id}:{fingerprint}"
    if (
        failed_job is not None
        and failed_job.embedding_fingerprint == fingerprint
        and failed_job.idempotency_key == expected_key
    ):
        failed_job.status = "pending"
        failed_job.stage = "pending"
        failed_job.progress = 0
        failed_job.attempt_count = 0
        failed_job.next_retry_at = None
        failed_job.error_code = None
        failed_job.error_reference = None
        session.add(
            OutboxEvent(
                event_key=f"wiki-index-manual-retry:{failed_job.id}:{uuid4()}",
                event_type=OutboxEventType.WIKI_PAGE_INDEX_REQUESTED,
                aggregate_id=failed_job.id,
                payload={"job_id": str(failed_job.id)},
            )
        )
        return failed_job.id

    job_id = await schedule_page_index(
        session,
        knowledge_base_id=knowledge_base_id,
        page_version_id=page_version_id,
        fingerprint=fingerprint,
    )
    if job_id is not None:
        return job_id
    existing_id = await session.scalar(
        select(WikiJob.id).where(WikiJob.idempotency_key == expected_key)
    )
    if existing_id is None:
        raise RuntimeError("wiki_index_retry_job_missing")
    return existing_id


async def has_indexable_sources(session: AsyncSession, page_version_id: UUID) -> bool:
    """只判断版本是否至少包含一个带来源的可信受支持 Claim。"""

    value = await session.scalar(
        select(func.count(WikiClaimSource.id))
        .select_from(WikiClaim)
        .join(WikiClaimSource, WikiClaimSource.claim_id == WikiClaim.id)
        .where(
            WikiClaim.page_version_id == page_version_id,
            WikiClaim.trust_state == "verified",
            WikiClaim.claim_type.in_(_INDEXABLE_CLAIM_TYPES),
        )
    )
    return bool(value)


async def mark_and_schedule_page_index(
    session: AsyncSession,
    *,
    page: WikiPage,
    version: WikiPageVersion,
    index_enabled: bool,
    fingerprint: str,
) -> UUID | None:
    """仅把当前、已发布且具可信来源的非人工版本送入索引。"""

    eligible = (
        index_enabled
        and page.current_version_id == version.id
        and page.page_type in {"summary", "topic"}
        and version.origin != "user"
        and version.review_state == "published"
        and await has_indexable_sources(session, version.id)
    )
    if not eligible:
        version.index_state = "not_required"
        version.embedding_fingerprint = None
        version.indexed_at = None
        version.index_error_code = None
        return None
    version.index_state = "pending"
    version.embedding_fingerprint = fingerprint
    version.indexed_at = None
    version.index_error_code = None
    return await schedule_page_index(
        session,
        knowledge_base_id=page.knowledge_base_id,
        page_version_id=version.id,
        fingerprint=fingerprint,
    )


async def schedule_current_page_indexes(
    session: AsyncSession,
    *,
    knowledge_base_id: UUID,
    index_enabled: bool,
    fingerprint: str,
) -> tuple[int, int, int]:
    """扫描当前页版本并返回创建、已存在和跳过数量。"""

    rows = list(
        await session.execute(
            select(WikiPage, WikiPageVersion)
            .join(WikiPageVersion, WikiPage.current_version_id == WikiPageVersion.id)
            .where(
                WikiPage.knowledge_base_id == knowledge_base_id,
                WikiPage.deleted_at.is_(None),
                WikiPage.page_type.in_(("summary", "topic")),
            )
            .order_by(WikiPage.id)
        )
    )
    created = existing = skipped = 0
    for _page, version in rows:
        eligible = (
            index_enabled
            and version.origin != "user"
            and version.review_state == "published"
            and await has_indexable_sources(session, version.id)
        )
        if not eligible:
            version.index_state = "not_required"
            version.embedding_fingerprint = None
            skipped += 1
            continue
        if version.embedding_fingerprint == fingerprint and version.index_state in {
            "pending",
            "indexing",
            "ready",
        }:
            # 重复回填只报告已在执行或完成的同指纹任务，不能把 ready 降回 pending。
            existing += 1
            continue
        job_id = await schedule_page_index(
            session,
            knowledge_base_id=knowledge_base_id,
            page_version_id=version.id,
            fingerprint=fingerprint,
        )
        if job_id is None:
            expected_key = f"wiki-index:{version.id}:{fingerprint}"
            existing_job = await session.scalar(
                select(WikiJob).where(WikiJob.idempotency_key == expected_key)
            )
            if existing_job is not None and existing_job.status == "failed":
                await retry_page_index_job(
                    session,
                    knowledge_base_id=knowledge_base_id,
                    page_version_id=version.id,
                    fingerprint=fingerprint,
                    failed_job=existing_job,
                )
                version.index_state = "pending"
                version.embedding_fingerprint = fingerprint
                version.index_error_code = None
            elif existing_job is not None and existing_job.status in {
                "pending",
                "retry_wait",
            }:
                version.index_state = "pending"
                version.embedding_fingerprint = fingerprint
                version.index_error_code = None
            existing += 1
        else:
            version.index_state = "pending"
            version.embedding_fingerprint = fingerprint
            version.index_error_code = None
            created += 1
    return created, existing, skipped


class SqlAlchemyWikiIndexRepository:
    """Task4 状态机的 PostgreSQL 仓储；正文只在内存投影中短暂存在。"""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        embedding_dimension: int,
    ) -> None:
        if embedding_dimension <= 0:
            raise ValueError("wiki_embedding_dimension_invalid")
        self._sessions = sessions
        self.embedding_dimension = embedding_dimension
        self._locked_sessions: dict[UUID, AsyncSession] = {}

    @staticmethod
    def retry_delay(attempt_count: int) -> int:
        """按尝试次数计算最多五分钟的指数退避。"""

        return _retry_delay(attempt_count)

    @staticmethod
    def prepare_stale_recovery(job: WikiJob, version: WikiPageVersion) -> None:
        """恢复任务租约但保留 indexing，供 Task4 的 recovered_start 继续执行。"""

        now = datetime.now(UTC)
        job.heartbeat_at = now
        if job.attempt_count < job.max_attempts:
            job.status = "retry_wait"
            job.next_retry_at = now
            if version.index_state != "indexing":
                version.index_state = "failed"
            return
        job.status = "failed"
        job.stage = "failed"
        job.next_retry_at = None
        job.error_code = "wiki_index_heartbeat_timeout"
        job.error_reference = _setup_error_reference(
            job.id,
            "heartbeat_timeout",
            TimeoutError(),
        )
        version.index_state = "failed"
        version.embedding_fingerprint = None
        version.indexed_at = None
        version.index_error_code = job.error_code

    async def lock_job_and_version(
        self, job_id: UUID
    ) -> tuple[WikiJob, WikiPage, WikiPageVersion] | None:
        session = self._sessions()
        await session.begin()
        try:
            row = (
                await session.execute(
                    select(WikiJob, WikiPage, WikiPageVersion)
                    .join(WikiPageVersion, WikiJob.page_version_id == WikiPageVersion.id)
                    .join(WikiPage, WikiPageVersion.page_id == WikiPage.id)
                    .where(WikiJob.id == job_id)
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                await session.rollback()
                await session.close()
                return None
            job, page, version = row
            executable = (
                job.job_type == "page_index"
                and job.page_version_id == version.id
                and (
                    (
                        job.status in {"pending", "retry_wait"}
                        and version.index_state in {"pending", "failed"}
                    )
                    or (job.status == "retry_wait" and version.index_state == "indexing")
                    or (
                        job.stage in _CLEANUP_PENDING_STAGES
                        and job.status in {"retry_wait", "failed"}
                        and version.index_state == "failed"
                    )
                )
            )
            if not executable:
                # Task4 对不可执行任务会直接返回；这里必须先释放事务与行锁。
                await session.rollback()
                await session.close()
                return None
            self._locked_sessions[job_id] = session
            return job, page, version
        except Exception:
            await session.rollback()
            await session.close()
            raise

    async def save_state(self, job: WikiIndexJobLike, version: WikiPageVersionLike) -> None:
        orm_job = cast(WikiJob, job)
        orm_version = cast(WikiPageVersion, version)
        session = self._locked_sessions.pop(job.id, None)
        owns_session = session is None
        if session is None:
            session = self._sessions()
            await session.begin()
            orm_job = await session.merge(orm_job)
            orm_version = await session.merge(orm_version)
        try:
            await self._schedule_retry_if_needed(session, orm_job)
            if orm_version.index_state == "ready":
                await self._schedule_old_version_cleanup(session, orm_version)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            # 持锁 Session 与临时 Session 都在每个状态转换后释放。
            if owns_session or session is not None:
                await session.close()

    async def _schedule_retry_if_needed(self, session: AsyncSession, job: WikiJob) -> None:
        if job.status != "retry_wait":
            return
        delay = self.retry_delay(job.attempt_count)
        job.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
        key = f"wiki-index-retry:{job.id}:{job.attempt_count}"
        await session.execute(
            pg_insert(OutboxEvent)
            .values(
                id=uuid4(),
                event_key=key,
                event_type=OutboxEventType.WIKI_PAGE_INDEX_REQUESTED,
                aggregate_id=job.id,
                payload={"job_id": str(job.id)},
                available_at=job.next_retry_at,
            )
            .on_conflict_do_nothing(index_elements=[OutboxEvent.event_key])
        )

    async def _schedule_old_version_cleanup(
        self, session: AsyncSession, version: WikiPageVersion
    ) -> None:
        old_ids = list(
            await session.scalars(
                select(WikiPageVersion.id).where(
                    WikiPageVersion.page_id == version.page_id,
                    WikiPageVersion.id != version.id,
                    WikiPageVersion.index_state.in_(("ready", "failed")),
                )
            )
        )
        for old_id in old_ids:
            await session.execute(
                pg_insert(OutboxEvent)
                .values(
                    id=uuid4(),
                    event_key=f"wiki-index-delete:{old_id}",
                    event_type=OutboxEventType.WIKI_PAGE_INDEX_DELETE_REQUESTED,
                    aggregate_id=old_id,
                    payload={"page_version_id": str(old_id)},
                )
                .on_conflict_do_nothing(index_elements=[OutboxEvent.event_key])
            )

    async def touch_heartbeat(self, job_id: UUID, at: datetime) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(WikiJob)
                .where(WikiJob.id == job_id, WikiJob.status == "running")
                .values(heartbeat_at=at)
            )

    async def load_aliases_claims_and_sources(
        self, *, page_id: UUID, page_version_id: UUID
    ) -> WikiIndexProjection:
        async with self._sessions() as session:
            return await self._load_projection(session, page_id, page_version_id)

    async def relock_and_reload_projection(
        self, *, job_id: UUID, page_version_id: UUID
    ) -> tuple[WikiJob, WikiPage, WikiPageVersion, WikiIndexProjection]:
        session = self._sessions()
        await session.begin()
        try:
            row = (
                await session.execute(
                    select(WikiJob, WikiPage, WikiPageVersion)
                    .join(WikiPageVersion, WikiJob.page_version_id == WikiPageVersion.id)
                    .join(WikiPage, WikiPageVersion.page_id == WikiPage.id)
                    .where(WikiJob.id == job_id, WikiPageVersion.id == page_version_id)
                    .with_for_update()
                )
            ).one()
            job, page, version = row
            projection = await self._load_projection(session, page.id, version.id)
            self._locked_sessions[job_id] = session
            return job, page, version, projection
        except Exception:
            await session.rollback()
            await session.close()
            raise

    async def _load_projection(
        self, session: AsyncSession, page_id: UUID, page_version_id: UUID
    ) -> WikiIndexProjection:
        aliases = tuple(
            await session.scalars(
                select(WikiPageAlias.alias)
                .where(WikiPageAlias.page_id == page_id)
                .order_by(WikiPageAlias.normalized_alias, WikiPageAlias.id)
            )
        )
        claims = tuple(
            await session.scalars(
                select(WikiClaim)
                .where(
                    WikiClaim.page_version_id == page_version_id,
                    WikiClaim.trust_state == "verified",
                    WikiClaim.claim_type.in_(_INDEXABLE_CLAIM_TYPES),
                )
                .order_by(WikiClaim.position, WikiClaim.id)
            )
        )
        claim_ids = tuple(claim.id for claim in claims)
        if not claim_ids:
            return WikiIndexProjection(aliases, claims, {}, {}, ())
        rows = list(
            await session.execute(
                select(WikiClaimSource, Chunk)
                .join(
                    Document,
                    and_(
                        Document.id == WikiClaimSource.document_id,
                        Document.active_version_id == WikiClaimSource.document_version_id,
                        Document.status == "ready",
                        Document.deleted_at.is_(None),
                    ),
                )
                .join(
                    Chunk,
                    and_(
                        Chunk.document_version_id == WikiClaimSource.document_version_id,
                        Chunk.kind == WikiClaimSource.chunk_kind,
                        Chunk.ordinal == WikiClaimSource.chunk_ordinal,
                        or_(
                            WikiClaimSource.chunk_id.is_(None),
                            Chunk.id == WikiClaimSource.chunk_id,
                        ),
                    ),
                )
                .where(WikiClaimSource.claim_id.in_(claim_ids))
                .order_by(WikiClaimSource.claim_id, WikiClaimSource.id)
            )
        )
        paths: dict[UUID, list[str]] = defaultdict(list)
        snapshots: list[WikiSourceSnapshot] = []
        for source, chunk in rows:
            if not source_matches_chunk(source, chunk):
                continue
            path = " / ".join(
                str(part).strip() for part in source.heading_path if str(part).strip()
            )
            if path:
                paths[source.claim_id].append(path)
            snapshots.append(
                WikiSourceSnapshot(
                    source_id=source.id,
                    claim_id=source.claim_id,
                    document_id=source.document_id,
                    document_version_id=source.document_version_id,
                    chunk_id=source.chunk_id,
                    chunk_kind=source.chunk_kind,
                    chunk_ordinal=source.chunk_ordinal,
                    content_sha256=source.content_sha256,
                    heading_path=tuple(source.heading_path),
                    page_start=source.page_start,
                    page_end=source.page_end,
                )
            )
        valid_ids = {source.claim_id for source in snapshots}
        valid_claims = tuple(claim for claim in claims if claim.id in valid_ids)
        version = await session.get(WikiPageVersion, page_version_id)
        raw_sections = version.generation_metadata.get("summary_sections", []) if version else []
        title_by_key = (
            {
                item["claim_key"]: item["title"]
                for item in raw_sections
                if isinstance(item, dict)
                and isinstance(item.get("claim_key"), str)
                and isinstance(item.get("title"), str)
            }
            if isinstance(raw_sections, list)
            else {}
        )
        section_titles = {
            claim.id: title_by_key[claim.claim_key]
            for claim in valid_claims
            if claim.claim_key in title_by_key
        }
        return WikiIndexProjection(
            aliases=aliases,
            claims=valid_claims,
            source_paths={key: tuple(sorted(set(values))) for key, values in paths.items()},
            section_titles=section_titles,
            sources=tuple(snapshots),
        )


async def recover_stale_page_index_jobs(
    sessions: async_sessionmaker[AsyncSession], *, limit: int = 100
) -> int:
    """恢复超过五分钟未续租的索引任务，并保留版本 indexing 语义。"""

    stale_before = datetime.now(UTC) - timedelta(minutes=5)
    async with sessions() as session, session.begin():
        rows = list(
            await session.execute(
                select(WikiJob, WikiPageVersion)
                .join(WikiPageVersion, WikiJob.page_version_id == WikiPageVersion.id)
                .where(
                    WikiJob.job_type == "page_index",
                    WikiJob.status == "running",
                    WikiJob.heartbeat_at < stale_before,
                )
                .order_by(WikiJob.heartbeat_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        )
        for job, version in rows:
            SqlAlchemyWikiIndexRepository.prepare_stale_recovery(job, version)
            if job.status == "retry_wait":
                session.add(
                    OutboxEvent(
                        event_key=f"wiki-index-recover:{job.id}:{job.attempt_count}",
                        event_type=OutboxEventType.WIKI_PAGE_INDEX_REQUESTED,
                        aggregate_id=job.id,
                        payload={"job_id": str(job.id)},
                    )
                )
        return len(rows)


async def try_backfill_lock(session: AsyncSession, knowledge_base_id: UUID) -> bool:
    """使用事务级 advisory lock 串行同一知识库的回填。"""

    return bool(
        await session.scalar(
            text(
                "SELECT pg_try_advisory_xact_lock("
                "hashtextextended('wiki-index:' || CAST(:knowledge_base_id AS text), 0))"
            ),
            {"knowledge_base_id": str(knowledge_base_id)},
        )
    )
