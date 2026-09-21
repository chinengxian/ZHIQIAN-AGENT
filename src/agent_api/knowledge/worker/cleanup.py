from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
)
from agent_api.knowledge.infrastructure.milvus.index import MilvusChunkIndex
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage


class KnowledgeCleanupService:
    """执行可重复的索引、文件与数据库补偿清理。"""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        index: MilvusChunkIndex,
    ) -> None:
        self._sessions = sessions
        self._storage = storage
        self._index = index

    async def delete_document(self, document_id: UUID, job_id: UUID) -> None:
        async with self._sessions() as session:
            versions = list(
                await session.scalars(
                    select(DocumentVersion).where(DocumentVersion.document_id == document_id)
                )
            )
        for version in versions:
            await self._index.delete_version(version.id)
        for file_path in {version.file_path for version in versions}:
            self._delete_stored_file(file_path)

        async with self._sessions() as session, session.begin():
            document = await session.get(Document, document_id, with_for_update=True)
            job = await session.get(IngestionJob, job_id, with_for_update=True)
            if document is not None:
                document.status = DocumentStatus.DELETED.value
                document.deleted_at = datetime.now(UTC)
            if job is not None:
                job.status = IngestionJobStatus.SUCCEEDED.value
                job.current_stage = "deleted"
                job.progress = 100

    async def cleanup_version(self, version_id: UUID) -> None:
        # 与版本激活共用文档行锁，迟到的补偿事件不能删除活动索引。
        async with self._sessions() as session, session.begin():
            document = await session.scalar(
                select(Document)
                .join(DocumentVersion, DocumentVersion.document_id == Document.id)
                .where(DocumentVersion.id == version_id)
                .with_for_update(of=Document)
            )
            version = await session.get(DocumentVersion, version_id)
            if document is None or version is None:
                return
            if document.active_version_id == version_id:
                return
            if version.status not in {VersionStatus.READY.value, VersionStatus.FAILED.value}:
                return
            # PostgreSQL 保留旧版本与父子块作为审计事实，仅移除可重建索引。
            await self._index.delete_version(version_id)

    async def delete_knowledge_base(self, knowledge_base_id: UUID) -> None:
        async with self._sessions() as session:
            versions = list(
                await session.scalars(
                    select(DocumentVersion)
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(Document.knowledge_base_id == knowledge_base_id)
                )
            )
        for version in versions:
            await self._index.delete_version(version.id)
        for file_path in {version.file_path for version in versions}:
            self._delete_stored_file(file_path)

        async with self._sessions() as session, session.begin():
            await session.execute(
                delete(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
            )

    def _delete_stored_file(self, file_path: str) -> None:
        path = (self._storage.root / file_path).resolve()
        if not path.is_relative_to(self._storage.root):
            raise RuntimeError("stored_file_path_invalid")
        path.unlink(missing_ok=True)
