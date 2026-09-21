from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_api.core.config import Settings
from agent_api.knowledge.application.ingestion import UploadValidationError, validate_upload
from agent_api.knowledge.domain.statuses import (
    DocumentStatus,
    IngestionJobStatus,
    VersionStatus,
)
from agent_api.knowledge.infrastructure.database.models import (
    Document,
    DocumentVersion,
    IngestionJob,
    KnowledgeBase,
    OutboxEvent,
)
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage

DEFAULT_WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000001")


class KnowledgeManagementError(RuntimeError):
    """知识管理应用层的稳定错误基类。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class KnowledgeNotFoundError(KnowledgeManagementError):
    pass


class KnowledgeConflictError(KnowledgeManagementError):
    def __init__(self, code: str, *, requires_confirmation: bool = False) -> None:
        self.requires_confirmation = requires_confirmation
        super().__init__(code)


class SqlAlchemyKnowledgeManagementService:
    """以 PostgreSQL 为事实源的知识库管理与上传事务边界。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        settings: Settings,
    ) -> None:
        self._sessions = session_factory
        self._storage = storage
        self._settings = settings

    @property
    def max_upload_bytes(self) -> int:
        return self._settings.max_upload_bytes

    async def create_knowledge_base(self, *, name: str, description: str) -> dict[str, Any]:
        knowledge_base = KnowledgeBase(
            workspace_id=DEFAULT_WORKSPACE_ID,
            name=name.strip(),
            description=description.strip(),
            enabled=True,
        )
        try:
            async with self._sessions() as session, session.begin():
                session.add(knowledge_base)
                await session.flush()
        except IntegrityError:
            raise KnowledgeConflictError("knowledge_base_name_conflict") from None
        return self._knowledge_base_response(knowledge_base)

    async def list_knowledge_bases(self) -> list[dict[str, Any]]:
        async with self._sessions() as session:
            knowledge_bases = list(
                await session.scalars(
                    select(KnowledgeBase)
                    .where(KnowledgeBase.workspace_id == DEFAULT_WORKSPACE_ID)
                    .order_by(KnowledgeBase.created_at, KnowledgeBase.id)
                )
            )
            results: list[dict[str, Any]] = []
            for knowledge_base in knowledge_bases:
                document_count = await session.scalar(
                    select(func.count())
                    .select_from(Document)
                    .where(
                        Document.knowledge_base_id == knowledge_base.id,
                        Document.deleted_at.is_(None),
                    )
                )
                processing_count = await session.scalar(
                    select(func.count())
                    .select_from(Document)
                    .where(
                        Document.knowledge_base_id == knowledge_base.id,
                        Document.status.in_(
                            [DocumentStatus.PENDING.value, DocumentStatus.PROCESSING.value]
                        ),
                        Document.deleted_at.is_(None),
                    )
                )
                results.append(
                    self._knowledge_base_response(
                        knowledge_base,
                        document_count=int(document_count or 0),
                        processing_count=int(processing_count or 0),
                    )
                )
            return results

    async def get_knowledge_base(self, knowledge_base_id: UUID) -> dict[str, Any]:
        async with self._sessions() as session:
            knowledge_base = await self._get_knowledge_base(session, knowledge_base_id)
            return await self._knowledge_base_with_counts(session, knowledge_base)

    async def update_knowledge_base(
        self,
        knowledge_base_id: UUID,
        **values: object,
    ) -> dict[str, Any]:
        try:
            async with self._sessions() as session, session.begin():
                knowledge_base = await self._get_knowledge_base(session, knowledge_base_id)
                if "name" in values:
                    knowledge_base.name = str(values["name"]).strip()
                if "description" in values:
                    knowledge_base.description = str(values["description"]).strip()
                if "enabled" in values:
                    knowledge_base.enabled = bool(values["enabled"])
                await session.flush()
                return await self._knowledge_base_with_counts(session, knowledge_base)
        except IntegrityError:
            raise KnowledgeConflictError("knowledge_base_name_conflict") from None

    async def upload_document(
        self,
        knowledge_base_id: UUID,
        filename: str,
        mime_type: str,
        content: bytes,
        *,
        on_duplicate: str = "reject",
    ) -> dict[str, Any]:
        if on_duplicate not in {"reject", "new_version"}:
            raise KnowledgeManagementError("invalid_duplicate_policy")
        try:
            validated = validate_upload(
                filename,
                mime_type,
                content,
                max_bytes=self._settings.max_upload_bytes,
            )
        except UploadValidationError as error:
            raise KnowledgeManagementError(error.code) from None

        document_id = uuid4()
        version_id = uuid4()
        job_id = uuid4()
        event_id = uuid4()
        correlation_id = uuid4()
        stored_path = self._storage.write(version_id, content, suffix=validated.suffix)
        embedding_model = self._settings.embedding_model or ""
        embedding_fingerprint = self._embedding_fingerprint()
        try:
            async with self._sessions() as session, session.begin():
                knowledge_base = await session.scalar(
                    select(KnowledgeBase).where(
                        KnowledgeBase.id == knowledge_base_id,
                        KnowledgeBase.workspace_id == DEFAULT_WORKSPACE_ID,
                    )
                )
                if knowledge_base is None:
                    raise KnowledgeNotFoundError("knowledge_base_not_found")

                # 同一知识库和哈希的并发上传串行化，避免两个事务都看不到对方。
                lock_digest = sha256(
                    knowledge_base_id.bytes + bytes.fromhex(validated.sha256)
                ).digest()
                lock_key = int.from_bytes(lock_digest[:8], signed=True)
                await session.scalar(select(func.pg_advisory_xact_lock(lock_key)))
                duplicate = await session.execute(
                    select(Document, DocumentVersion)
                    .join(DocumentVersion, DocumentVersion.document_id == Document.id)
                    .where(
                        Document.knowledge_base_id == knowledge_base_id,
                        Document.deleted_at.is_(None),
                        DocumentVersion.sha256 == validated.sha256,
                        or_(
                            Document.active_version_id == DocumentVersion.id,
                            DocumentVersion.status.not_in(
                                [VersionStatus.FAILED.value, VersionStatus.READY.value]
                            ),
                        ),
                    )
                    .order_by(DocumentVersion.version_no.desc())
                    .limit(1)
                )
                duplicate_row = duplicate.first()
                if duplicate_row is not None:
                    duplicate_document, duplicate_version = duplicate_row
                    if (
                        on_duplicate != "new_version"
                        or duplicate_document.active_version_id != duplicate_version.id
                    ):
                        raise KnowledgeConflictError("duplicate_document")
                    document = await session.get(
                        Document, duplicate_document.id, with_for_update=True
                    )
                    assert document is not None
                    max_version = await session.scalar(
                        select(func.max(DocumentVersion.version_no)).where(
                            DocumentVersion.document_id == document.id
                        )
                    )
                    version_no = int(max_version or 0) + 1
                    document.status = DocumentStatus.PENDING.value
                else:
                    document = Document(
                        id=document_id,
                        knowledge_base_id=knowledge_base_id,
                        title=validated.title,
                        filename=PathSafeName(filename).value,
                        mime_type=validated.mime_type,
                        status=DocumentStatus.PENDING.value,
                    )
                    session.add(document)
                    version_no = 1

                version = DocumentVersion(
                    id=version_id,
                    document_id=document.id,
                    version_no=version_no,
                    file_path=stored_path.name,
                    sha256=validated.sha256,
                    file_size=validated.size,
                    pipeline_version="knowledge-v1",
                    embedding_provider=self._settings.embedding_provider.value,
                    embedding_model=embedding_model,
                    embedding_dimension=self._settings.embedding_dimension,
                    embedding_fingerprint=embedding_fingerprint,
                )
                job = IngestionJob(
                    id=job_id,
                    document_version_id=version_id,
                    correlation_id=correlation_id,
                    idempotency_key=f"ingest:{version_id}",
                )
                outbox = OutboxEvent(
                    id=event_id,
                    event_key=f"ingest:{version_id}",
                    event_type="document.ingestion.requested",
                    aggregate_id=version_id,
                    payload={
                        "job_id": str(job_id),
                        "document_version_id": str(version_id),
                        "correlation_id": str(correlation_id),
                    },
                )
                session.add_all((version, job, outbox))
        except Exception:
            self._storage.delete(version_id, suffix=validated.suffix)
            raise

        return {
            "document_id": document.id,
            "document_version_id": version_id,
            "job_id": job_id,
            "status": "pending",
        }

    async def list_documents(self, knowledge_base_id: UUID) -> list[dict[str, Any]]:
        async with self._sessions() as session:
            await self._get_knowledge_base(session, knowledge_base_id)
            documents = list(
                await session.scalars(
                    select(Document)
                    .where(
                        Document.knowledge_base_id == knowledge_base_id,
                        Document.deleted_at.is_(None),
                    )
                    .order_by(Document.created_at.desc(), Document.id)
                )
            )
            return [await self._document_response(session, document) for document in documents]

    async def get_document(self, document_id: UUID) -> dict[str, Any]:
        async with self._sessions() as session:
            document = await self._get_document(session, document_id)
            return await self._document_response(session, document)

    async def request_document_action(
        self,
        document_id: UUID,
        action: str,
    ) -> dict[str, Any]:
        if action not in {"retry", "reindex"}:
            raise KnowledgeManagementError("unsupported_document_action")
        async with self._sessions() as session, session.begin():
            document = await self._get_document(session, document_id)
            await session.refresh(document, with_for_update=True)
            if document.status in {DocumentStatus.DELETING.value, DocumentStatus.DELETED.value}:
                raise KnowledgeConflictError("document_deleting")
            latest = await session.scalar(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
                .order_by(DocumentVersion.version_no.desc())
                .limit(1)
                .with_for_update()
            )
            if latest is None:
                raise KnowledgeConflictError("document_has_no_version")
            latest_job = await session.scalar(
                select(IngestionJob)
                .where(IngestionJob.document_version_id == latest.id)
                .order_by(IngestionJob.created_at.desc(), IngestionJob.id.desc())
                .limit(1)
            )
            if (
                latest_job is not None
                and latest_job.idempotency_key.startswith(f"{action}:")
                and latest_job.status
                in {
                    IngestionJobStatus.PENDING.value,
                    IngestionJobStatus.RUNNING.value,
                    IngestionJobStatus.RETRY_WAIT.value,
                }
            ):
                return {
                    "document_id": document.id,
                    "job_id": latest_job.id,
                    "status": "pending",
                }

            if action == "retry" and (
                latest.status != VersionStatus.FAILED.value
                or latest_job is None
                or latest_job.status != IngestionJobStatus.FAILED.value
            ):
                raise KnowledgeConflictError("document_not_failed")
            if action == "reindex" and document.active_version_id is None:
                raise KnowledgeConflictError("document_not_ready")

            if action == "reindex":
                version = DocumentVersion(
                    document_id=document.id,
                    version_no=latest.version_no + 1,
                    file_path=latest.file_path,
                    sha256=latest.sha256,
                    file_size=latest.file_size,
                    pipeline_version="knowledge-v1",
                    embedding_provider=self._settings.embedding_provider.value,
                    embedding_model=self._settings.embedding_model or "",
                    embedding_dimension=self._settings.embedding_dimension,
                    embedding_fingerprint=self._embedding_fingerprint(),
                )
                session.add(version)
                await session.flush()
            else:
                version = latest
                version.status = "pending"
                version.error_code = None
                version.error_reference = None

            job = IngestionJob(
                document_version_id=version.id,
                current_stage="pending",
                correlation_id=uuid4(),
                idempotency_key=f"{action}:{version.id}:{uuid4()}",
            )
            session.add(job)
            await session.flush()
            session.add(
                OutboxEvent(
                    event_key=f"{action}:{job.id}",
                    event_type="document.ingestion.requested",
                    aggregate_id=version.id,
                    payload={
                        "job_id": str(job.id),
                        "document_version_id": str(version.id),
                        "correlation_id": str(job.correlation_id),
                    },
                )
            )
            document.status = DocumentStatus.PENDING.value
            return {"document_id": document.id, "job_id": job.id, "status": "pending"}

    async def delete_document(self, document_id: UUID) -> dict[str, Any]:
        async with self._sessions() as session, session.begin():
            document = await self._get_document(session, document_id)
            await session.refresh(document, with_for_update=True)
            existing_job = await session.scalar(
                select(IngestionJob).where(IngestionJob.idempotency_key == f"delete:{document.id}")
            )
            if existing_job is not None:
                return {
                    "document_id": document.id,
                    "job_id": existing_job.id,
                    "status": "deleting",
                }
            version = await session.scalar(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document.id)
                .order_by(DocumentVersion.version_no.desc())
                .limit(1)
            )
            if version is None:
                raise KnowledgeConflictError("document_has_no_version")
            job = IngestionJob(
                document_version_id=version.id,
                current_stage="deleting",
                correlation_id=uuid4(),
                idempotency_key=f"delete:{document.id}",
            )
            session.add(job)
            await session.flush()
            session.add(
                OutboxEvent(
                    event_key=f"delete:{document.id}",
                    event_type="document.deletion.requested",
                    aggregate_id=version.id,
                    payload={"job_id": str(job.id), "document_id": str(document.id)},
                )
            )
            document.status = DocumentStatus.DELETING.value
            return {"document_id": document.id, "job_id": job.id, "status": "deleting"}

    async def stream_events(self) -> AsyncIterator[dict[str, object]]:
        """轮询 PostgreSQL 事实状态；客户端断开时生成器会被取消。"""

        cursor = (datetime.now(UTC), UUID(int=0))
        while True:
            events: list[dict[str, object]] = []
            async with self._sessions() as session:
                jobs = list(
                    await session.scalars(
                        select(IngestionJob)
                        .join(
                            DocumentVersion,
                            DocumentVersion.id == IngestionJob.document_version_id,
                        )
                        .join(Document, Document.id == DocumentVersion.document_id)
                        .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
                        .where(
                            KnowledgeBase.workspace_id == DEFAULT_WORKSPACE_ID,
                            tuple_(IngestionJob.updated_at, IngestionJob.id) > cursor,
                        )
                        .order_by(IngestionJob.updated_at, IngestionJob.id)
                    )
                )
                for job in jobs:
                    cursor = (job.updated_at, job.id)
                    version = await session.get(DocumentVersion, job.document_version_id)
                    if version is None:
                        continue
                    events.append(
                        {
                            "type": "document_progress",
                            "document_id": str(version.document_id),
                            "job_id": str(job.id),
                            "stage": job.current_stage,
                            "status": job.status,
                            "progress": job.progress,
                            "error_code": job.error_code,
                            "error_reference": job.error_reference,
                        }
                    )
            for event in events:
                yield event
            await asyncio.sleep(1)

    async def delete_knowledge_base(self, knowledge_base_id: UUID, *, confirm: bool) -> None:
        async with self._sessions() as session, session.begin():
            knowledge_base = await session.scalar(
                select(KnowledgeBase)
                .where(
                    KnowledgeBase.id == knowledge_base_id,
                    KnowledgeBase.workspace_id == DEFAULT_WORKSPACE_ID,
                )
                .with_for_update()
            )
            if knowledge_base is None:
                raise KnowledgeNotFoundError("knowledge_base_not_found")
            document_count = await session.scalar(
                select(func.count())
                .select_from(Document)
                .where(
                    Document.knowledge_base_id == knowledge_base_id,
                    Document.deleted_at.is_(None),
                )
            )
            if document_count and not confirm:
                raise KnowledgeConflictError("knowledge_base_not_empty", requires_confirmation=True)
            if not document_count:
                await session.execute(
                    delete(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
                )
                return

            existing_deletion = await session.scalar(
                select(OutboxEvent.id).where(
                    OutboxEvent.event_key == f"delete-kb:{knowledge_base_id}"
                )
            )
            if existing_deletion is not None:
                return

            knowledge_base.enabled = False
            await session.execute(
                update(Document)
                .where(Document.knowledge_base_id == knowledge_base_id)
                .values(status=DocumentStatus.DELETING.value)
            )
            session.add(
                OutboxEvent(
                    event_key=f"delete-kb:{knowledge_base_id}",
                    event_type="knowledge_base.deletion.requested",
                    aggregate_id=knowledge_base_id,
                    payload={"knowledge_base_id": str(knowledge_base_id)},
                )
            )

    @staticmethod
    def _knowledge_base_response(
        knowledge_base: KnowledgeBase,
        *,
        document_count: int = 0,
        processing_count: int = 0,
    ) -> dict[str, Any]:
        return {
            "id": knowledge_base.id,
            "name": knowledge_base.name,
            "description": knowledge_base.description,
            "enabled": knowledge_base.enabled,
            "document_count": document_count,
            "processing_count": processing_count,
        }

    def _embedding_fingerprint(self) -> str:
        return sha256(
            (
                f"{self._settings.embedding_provider.value}|"
                f"{self._settings.embedding_model or ''}|"
                f"{self._settings.embedding_dimension}"
            ).encode()
        ).hexdigest()

    async def _get_knowledge_base(
        self,
        session: AsyncSession,
        knowledge_base_id: UUID,
    ) -> KnowledgeBase:
        knowledge_base = await session.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.id == knowledge_base_id,
                KnowledgeBase.workspace_id == DEFAULT_WORKSPACE_ID,
            )
        )
        if knowledge_base is None:
            raise KnowledgeNotFoundError("knowledge_base_not_found")
        return knowledge_base

    async def _get_document(self, session: AsyncSession, document_id: UUID) -> Document:
        document = await session.scalar(
            select(Document)
            .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
            .where(
                Document.id == document_id,
                Document.deleted_at.is_(None),
                KnowledgeBase.workspace_id == DEFAULT_WORKSPACE_ID,
            )
        )
        if document is None:
            raise KnowledgeNotFoundError("document_not_found")
        return document

    async def _knowledge_base_with_counts(
        self,
        session: AsyncSession,
        knowledge_base: KnowledgeBase,
    ) -> dict[str, Any]:
        document_count = await session.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.knowledge_base_id == knowledge_base.id,
                Document.deleted_at.is_(None),
            )
        )
        processing_count = await session.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.knowledge_base_id == knowledge_base.id,
                Document.status.in_(
                    [DocumentStatus.PENDING.value, DocumentStatus.PROCESSING.value]
                ),
                Document.deleted_at.is_(None),
            )
        )
        return self._knowledge_base_response(
            knowledge_base,
            document_count=int(document_count or 0),
            processing_count=int(processing_count or 0),
        )

    async def _document_response(
        self,
        session: AsyncSession,
        document: Document,
    ) -> dict[str, Any]:
        job = await session.scalar(
            select(IngestionJob)
            .join(DocumentVersion, DocumentVersion.id == IngestionJob.document_version_id)
            .where(DocumentVersion.document_id == document.id)
            .order_by(IngestionJob.created_at.desc(), IngestionJob.id.desc())
            .limit(1)
        )
        return {
            "id": document.id,
            "knowledge_base_id": document.knowledge_base_id,
            "title": document.title,
            "filename": document.filename,
            "mime_type": document.mime_type,
            "status": document.status,
            "active_version_id": document.active_version_id,
            "job": (
                None
                if job is None
                else {
                    "id": job.id,
                    "stage": job.current_stage,
                    "status": job.status,
                    "progress": job.progress,
                    "error_code": job.error_code,
                    "error_reference": job.error_reference,
                }
            ),
        }


class PathSafeName:
    """只保留用户文件名的末段，绝不将其用于磁盘路径。"""

    def __init__(self, filename: str) -> None:
        normalized = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        self.value = normalized or "upload"
