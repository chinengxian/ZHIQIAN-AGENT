from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from pymilvus import MilvusClient  # type: ignore[import-untyped]

from agent_api.core.config import Settings
from agent_api.knowledge.application.ingestion import IngestionPipeline, ParentChildChunker
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.infrastructure.docling.parser import DoclingParser
from agent_api.knowledge.infrastructure.embedding.openai import create_embedding_adapter
from agent_api.knowledge.infrastructure.jobs.repository import SqlAlchemyIngestionRepository
from agent_api.knowledge.infrastructure.milvus.index import MilvusChunkIndex
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage
from agent_api.knowledge.worker.cleanup import KnowledgeCleanupService


async def handle_event(
    event_type: str,
    payload: Mapping[str, object],
    pipeline: IngestionPipeline,
    cleanup: CleanupHandler | None = None,
) -> None:
    """只接受显式白名单事件，队列数据不能选择任意函数。"""

    if event_type == "document.ingestion.requested":
        await pipeline.run(_payload_uuid(payload, "job_id"))
        return
    if cleanup is None:
        raise ValueError("unsupported_event_type")
    if event_type == "document.deletion.requested":
        await cleanup.delete_document(
            _payload_uuid(payload, "document_id"),
            _payload_uuid(payload, "job_id"),
        )
        return
    if event_type == "document.version.cleanup.requested":
        await cleanup.cleanup_version(_payload_uuid(payload, "document_version_id"))
        return
    if event_type == "knowledge_base.deletion.requested":
        await cleanup.delete_knowledge_base(_payload_uuid(payload, "knowledge_base_id"))
        return
    raise ValueError("unsupported_event_type")


class CleanupHandler(Protocol):
    async def delete_document(self, document_id: UUID, job_id: UUID) -> None: ...

    async def cleanup_version(self, version_id: UUID) -> None: ...

    async def delete_knowledge_base(self, knowledge_base_id: UUID) -> None: ...


def _payload_uuid(payload: Mapping[str, object], field: str) -> UUID:
    try:
        return UUID(str(payload[field]))
    except (KeyError, ValueError, TypeError):
        code = "invalid_job_id" if field == "job_id" else "invalid_event_payload"
        raise ValueError(code) from None


class KnowledgeWorkerRuntime:
    """为单次 Celery 任务装配并释放数据库、Docling、Embedding 与 Milvus。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._database: DatabaseRuntime | None = None
        self._milvus: MilvusClient | None = None
        self.pipeline: IngestionPipeline | None = None
        self.cleanup: KnowledgeCleanupService | None = None

    async def __aenter__(self) -> Self:
        settings = self._settings
        database = DatabaseRuntime(settings.database_url.get_secret_value())
        storage = LocalFileStorage(settings.storage_root)
        storage.ensure_ready()
        token = settings.milvus_token.get_secret_value() if settings.milvus_token else None
        kwargs: dict[str, str] = {"uri": settings.milvus_uri}
        if token:
            kwargs["token"] = token
        milvus = await asyncio.to_thread(MilvusClient, **kwargs)
        repository = SqlAlchemyIngestionRepository(database.session_factory, storage)
        self._database = database
        self._milvus = milvus
        index = MilvusChunkIndex(milvus, settings.milvus_collection)
        self.pipeline = IngestionPipeline(
            repository=repository,
            parser=DoclingParser(),
            chunker=ParentChildChunker(),
            embedder=create_embedding_adapter(settings),
            index=index,
        )
        self.cleanup = KnowledgeCleanupService(database.session_factory, storage, index)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._milvus is not None:
            await asyncio.to_thread(self._milvus.close)
            self._milvus = None
        if self._database is not None:
            await self._database.close()
            self._database = None
        self.pipeline = None
        self.cleanup = None
