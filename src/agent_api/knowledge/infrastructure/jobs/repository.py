from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import String, and_, cast, delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_api.knowledge.application.ingestion import (
    ChunkDraft,
    IngestionAlreadyClaimed,
    IngestionContext,
    PersistedChunk,
)
from agent_api.knowledge.domain.events import OutboxEventType
from agent_api.knowledge.domain.statuses import (
    ChunkKind,
    DocumentStatus,
    IngestionJobStatus,
    OutboxStatus,
    VersionStatus,
)
from agent_api.knowledge.infrastructure.database.models import (
    Chunk,
    Document,
    DocumentVersion,
    IngestionJob,
    OutboxEvent,
)
from agent_api.knowledge.infrastructure.observability.events import record_event
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage
from agent_api.knowledge.wiki.jobs import invalidate_document_claims, schedule_document_wiki


class SqlAlchemyIngestionRepository:
    """为 Worker 提供短事务、幂等 chunk 重写和活动版本切换。"""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
    ) -> None:
        self._sessions = sessions
        self._storage = storage

    async def load_context(self, job_id: UUID) -> IngestionContext:
        async with self._sessions() as session, session.begin():
            job = await session.get(IngestionJob, job_id, with_for_update=True)
            if job is None:
                raise IngestionAlreadyClaimed()
            now = datetime.now(UTC)
            if job.status not in {
                IngestionJobStatus.PENDING.value,
                IngestionJobStatus.RETRY_WAIT.value,
            } or (
                job.status == IngestionJobStatus.RETRY_WAIT.value
                and job.next_retry_at is not None
                and job.next_retry_at > now
            ):
                raise IngestionAlreadyClaimed()
            version = await session.get(DocumentVersion, job.document_version_id)
            if version is None:
                raise IngestionAlreadyClaimed()
            document = await session.get(Document, version.document_id)
            if document is None:
                raise IngestionAlreadyClaimed()
            if document.status in {DocumentStatus.DELETING.value, DocumentStatus.DELETED.value}:
                raise IngestionAlreadyClaimed()
            file_path = (self._storage.root / version.file_path).resolve()
            if not file_path.is_relative_to(self._storage.root) or not file_path.is_file():
                raise RuntimeError("stored_file_unavailable")
            job.status = IngestionJobStatus.RUNNING.value
            job.heartbeat_at = now
            return IngestionContext(
                job_id=job.id,
                document_id=document.id,
                document_version_id=version.id,
                knowledge_base_id=document.knowledge_base_id,
                file_path=file_path,
                previous_active_version_id=document.active_version_id,
            )

    async def update_stage(self, job_id: UUID, stage: str, progress: int) -> None:
        now = datetime.now(UTC)
        async with self._sessions() as session, session.begin():
            job = await session.get(IngestionJob, job_id, with_for_update=True)
            if job is None:
                raise RuntimeError("ingestion_job_not_found")
            version = await session.get(DocumentVersion, job.document_version_id)
            if version is None:
                raise RuntimeError("document_version_not_found")
            document = await session.get(Document, version.document_id, with_for_update=True)
            if document is None:
                raise RuntimeError("document_not_found")
            if document.status in {DocumentStatus.DELETING.value, DocumentStatus.DELETED.value}:
                raise RuntimeError("ingestion_document_deleting")
            job.status = IngestionJobStatus.RUNNING.value
            job.current_stage = stage
            job.progress = progress
            job.heartbeat_at = now
            document.status = (
                DocumentStatus.READY.value if stage == "ready" else DocumentStatus.PROCESSING.value
            )
            if stage in {item.value for item in VersionStatus}:
                version.status = stage

    async def heartbeat(self, job_id: UUID) -> None:
        async with self._sessions() as session, session.begin():
            job = await session.get(IngestionJob, job_id, with_for_update=True)
            if job is not None and job.status == IngestionJobStatus.RUNNING.value:
                job.heartbeat_at = datetime.now(UTC)

    async def replace_chunks(
        self,
        version_id: UUID,
        drafts: list[ChunkDraft],
    ) -> list[PersistedChunk]:
        async with self._sessions() as session, session.begin():
            await session.execute(delete(Chunk).where(Chunk.document_version_id == version_id))
            ids_by_ordinal = {draft.ordinal: uuid4() for draft in drafts}
            persisted: list[PersistedChunk] = []
            # 先写父块，确保同表外键在子块 flush 时已经存在。
            for kind in (ChunkKind.PARENT, ChunkKind.CHILD):
                for draft in drafts:
                    if draft.kind is not kind:
                        continue
                    parent_id = (
                        ids_by_ordinal[draft.parent_ordinal]
                        if draft.parent_ordinal is not None
                        else None
                    )
                    chunk_id = ids_by_ordinal[draft.ordinal]
                    session.add(
                        Chunk(
                            id=chunk_id,
                            document_version_id=version_id,
                            parent_chunk_id=parent_id,
                            ordinal=draft.ordinal,
                            kind=draft.kind.value,
                            content=draft.content,
                            embedding_text=draft.embedding_text,
                            heading_path=list(draft.heading_path),
                            page_start=draft.page_start,
                            page_end=draft.page_end,
                            char_start=draft.char_start,
                            char_end=draft.char_end,
                            token_count=max(1, (len(draft.content) + 3) // 4),
                        )
                    )
                    persisted.append(PersistedChunk(chunk_id, draft, parent_id))
                await session.flush()
            persisted.sort(key=lambda item: item.draft.ordinal)
            return persisted

    async def activate(
        self,
        document_id: UUID,
        version_id: UUID,
        previous_version_id: UUID | None,
    ) -> None:
        async with self._sessions() as session, session.begin():
            document = await session.scalar(
                select(Document).where(Document.id == document_id).with_for_update()
            )
            version = await session.get(DocumentVersion, version_id, with_for_update=True)
            if document is None or version is None:
                raise RuntimeError("document_version_not_found")
            if document.status in {DocumentStatus.DELETING.value, DocumentStatus.DELETED.value}:
                raise RuntimeError("ingestion_document_deleting")
            if document.active_version_id != previous_version_id:
                raise RuntimeError("active_version_changed")
            document.active_version_id = version_id
            document.status = DocumentStatus.READY.value
            version.status = VersionStatus.READY.value
            version.ready_at = datetime.now(UTC)
            if previous_version_id is not None and previous_version_id != version_id:
                await invalidate_document_claims(session, document_id)
            await schedule_document_wiki(session, document.knowledge_base_id, version_id)
            if previous_version_id is not None and previous_version_id != version_id:
                session.add(
                    OutboxEvent(
                        event_key=f"cleanup-version:{previous_version_id}",
                        event_type=OutboxEventType.DOCUMENT_VERSION_CLEANUP_REQUESTED,
                        aggregate_id=previous_version_id,
                        payload={"document_version_id": str(previous_version_id)},
                    )
                )

    async def mark_failed(self, version_id: UUID, code: str) -> None:
        async with self._sessions() as session, session.begin():
            version = await session.get(DocumentVersion, version_id, with_for_update=True)
            if version is None:
                return
            version.status = VersionStatus.FAILED.value
            version.error_code = code
            version.error_reference = f"ingestion-{uuid4()}"
            document = await session.get(Document, version.document_id, with_for_update=True)
            if document is not None and document.status not in {
                DocumentStatus.DELETING.value,
                DocumentStatus.DELETED.value,
            }:
                document.status = (
                    DocumentStatus.READY.value
                    if document.active_version_id is not None
                    else DocumentStatus.FAILED.value
                )

    async def complete_job(self, job_id: UUID) -> None:
        async with self._sessions() as session, session.begin():
            job = await session.get(IngestionJob, job_id, with_for_update=True)
            if job is None:
                return
            job.status = IngestionJobStatus.SUCCEEDED.value
            job.current_stage = "ready"
            job.progress = 100
            job.heartbeat_at = datetime.now(UTC)

    async def fail_job(self, job_id: UUID, code: str) -> None:
        async with self._sessions() as session, session.begin():
            job = await session.get(IngestionJob, job_id, with_for_update=True)
            if job is None:
                return
            job.status = IngestionJobStatus.FAILED.value
            job.error_code = code
            job.error_reference = f"ingestion-{uuid4()}"
            job.heartbeat_at = datetime.now(UTC)

    async def schedule_failed_version_cleanup(self, version_id: UUID) -> None:
        # 留出自动重试窗口，避免补偿任务删除重试期间刚写入的向量。
        async with self._sessions() as session, session.begin():
            statement = (
                pg_insert(OutboxEvent)
                .values(
                    id=uuid4(),
                    event_key=f"failed-version:{version_id}",
                    event_type=OutboxEventType.DOCUMENT_VERSION_CLEANUP_REQUESTED,
                    aggregate_id=version_id,
                    payload={"document_version_id": str(version_id)},
                    status=OutboxStatus.PENDING.value,
                    available_at=datetime.now(UTC) + timedelta(minutes=5),
                )
                .on_conflict_do_nothing(index_elements=[OutboxEvent.event_key])
            )
            await session.execute(statement)

    async def schedule_retry(self, job_id: UUID, *, delay_seconds: int) -> bool:
        """失败任务与延迟 outbox 同事务持久化，重复消费不会重复排队。"""

        async with self._sessions() as session, session.begin():
            job = await session.get(IngestionJob, job_id, with_for_update=True)
            if job is None:
                raise RuntimeError("ingestion_job_not_found")
            if job.status in {
                IngestionJobStatus.SUCCEEDED.value,
                IngestionJobStatus.RETRY_WAIT.value,
            }:
                return False
            if job.attempt_count >= job.max_attempts:
                job.status = IngestionJobStatus.FAILED.value
                job.error_code = "retry_exhausted"
                return False

            job.attempt_count += 1
            job.status = IngestionJobStatus.RETRY_WAIT.value
            job.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
            job.heartbeat_at = None
            session.add(
                OutboxEvent(
                    event_key=f"retry-job:{job.id}:{job.attempt_count}",
                    event_type=OutboxEventType.DOCUMENT_INGESTION_REQUESTED,
                    aggregate_id=job.document_version_id,
                    payload={
                        "job_id": str(job.id),
                        "document_version_id": str(job.document_version_id),
                        "correlation_id": str(job.correlation_id),
                    },
                    available_at=job.next_retry_at,
                )
            )
            return True

    async def recover_stale_jobs(self, *, stale_before: datetime, limit: int) -> list[UUID]:
        """恢复心跳超时或已发布却未消费的任务，并原子写入新投递。"""

        recovered: list[UUID] = []
        outcomes: list[tuple[str, UUID, str | None]] = []
        published_event = (
            select(OutboxEvent.id)
            .where(
                OutboxEvent.event_type == OutboxEventType.DOCUMENT_INGESTION_REQUESTED,
                OutboxEvent.status == OutboxStatus.PUBLISHED.value,
                OutboxEvent.payload["job_id"].astext == cast(IngestionJob.id, String),
            )
            .exists()
        )
        async with self._sessions() as session, session.begin():
            jobs = list(
                await session.scalars(
                    select(IngestionJob)
                    .join(
                        DocumentVersion,
                        DocumentVersion.id == IngestionJob.document_version_id,
                    )
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(
                        or_(
                            IngestionJob.status == IngestionJobStatus.RUNNING.value,
                            and_(
                                IngestionJob.status == IngestionJobStatus.FAILED.value,
                                IngestionJob.error_code.not_in(
                                    ["document_parse_failed", "retry_exhausted"]
                                ),
                            ),
                            and_(
                                IngestionJob.status.in_(
                                    [
                                        IngestionJobStatus.PENDING.value,
                                        IngestionJobStatus.RETRY_WAIT.value,
                                    ]
                                ),
                                IngestionJob.updated_at < stale_before,
                                or_(
                                    IngestionJob.next_retry_at.is_(None),
                                    IngestionJob.next_retry_at < stale_before,
                                ),
                                published_event,
                            ),
                        ),
                        or_(
                            IngestionJob.heartbeat_at < stale_before,
                            and_(
                                IngestionJob.heartbeat_at.is_(None),
                                IngestionJob.updated_at < stale_before,
                                published_event,
                            ),
                        ),
                        Document.status.not_in(
                            [DocumentStatus.DELETING.value, DocumentStatus.DELETED.value]
                        ),
                    )
                    .order_by(IngestionJob.heartbeat_at, IngestionJob.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for job in jobs:
                version = await session.get(DocumentVersion, job.document_version_id)
                document = await session.get(Document, version.document_id) if version else None
                if version is None or document is None:
                    continue
                if (
                    document.active_version_id == version.id
                    and version.status == VersionStatus.READY.value
                ):
                    job.status = IngestionJobStatus.SUCCEEDED.value
                    job.current_stage = "ready"
                    job.progress = 100
                    outcomes.append(("job_already_activated", job.id, None))
                    continue
                job.attempt_count += 1
                if job.attempt_count >= job.max_attempts:
                    job.status = IngestionJobStatus.FAILED.value
                    job.error_code = "retry_exhausted"
                    job.error_reference = f"ingestion-{uuid4()}"
                    version.status = VersionStatus.FAILED.value
                    version.error_code = "retry_exhausted"
                    version.error_reference = job.error_reference
                    document.status = (
                        DocumentStatus.READY.value
                        if document.active_version_id is not None
                        else DocumentStatus.FAILED.value
                    )
                    outcomes.append(("job_retry_exhausted", job.id, job.error_reference))
                    continue
                job.status = IngestionJobStatus.RETRY_WAIT.value
                job.next_retry_at = datetime.now(UTC)
                job.heartbeat_at = None
                session.add(
                    OutboxEvent(
                        event_key=f"recover:{job.id}:{job.attempt_count}",
                        event_type=OutboxEventType.DOCUMENT_INGESTION_REQUESTED,
                        aggregate_id=job.document_version_id,
                        payload={
                            "job_id": str(job.id),
                            "document_version_id": str(job.document_version_id),
                            "correlation_id": str(job.correlation_id),
                        },
                    )
                )
                recovered.append(job.id)
                outcomes.append(("job_requeued", job.id, None))
        for outcome, job_id, error_reference in outcomes:
            if outcome == "job_retry_exhausted":
                record_event("job_retry_exhausted", job_id, error_reference=error_reference)
            elif outcome == "job_already_activated":
                record_event("job_already_activated", job_id)
            else:
                record_event("job_requeued", job_id)
        return recovered
