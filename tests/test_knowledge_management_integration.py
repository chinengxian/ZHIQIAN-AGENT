import asyncio
import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import AnyHttpUrl, SecretStr
from pymilvus import MilvusClient
from redis.asyncio import Redis
from sqlalchemy import delete, func, select

from agent_api.core.config import ModelProvider, Settings
from agent_api.knowledge.application.ingestion import (
    IngestionPipeline,
    IngestionPipelineError,
    ParentChildChunker,
    ParsedSection,
)
from agent_api.knowledge.application.management import (
    KnowledgeConflictError,
    SqlAlchemyKnowledgeManagementService,
)
from agent_api.knowledge.infrastructure.database.models import (
    Chunk,
    Document,
    DocumentVersion,
    IngestionJob,
    KnowledgeBase,
    OutboxEvent,
)
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.infrastructure.jobs.outbox import (
    CeleryEventPublisher,
    OutboxDispatcher,
    PendingEvent,
    SqlAlchemyOutboxRepository,
)
from agent_api.knowledge.infrastructure.jobs.repository import SqlAlchemyIngestionRepository
from agent_api.knowledge.infrastructure.milvus.index import IndexedChild, MilvusChunkIndex
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage
from agent_api.knowledge.worker.cleanup import KnowledgeCleanupService

pytestmark = [
    pytest.mark.knowledge_integration,
    pytest.mark.skipif(
        os.getenv("RUN_KNOWLEDGE_INTEGRATION") != "1",
        reason="set RUN_KNOWLEDGE_INTEGRATION=1 to use local knowledge containers",
    ),
]

DATABASE_URL = "postgresql+asyncpg://agent:agent@127.0.0.1:5432/agent"


async def cleanup_knowledge_base(runtime: DatabaseRuntime, knowledge_base_id: UUID) -> None:
    """清理集成测试产生的无外键 outbox 事件和级联业务数据。"""

    async with runtime.session_factory() as session, session.begin():
        version_ids = list(
            await session.scalars(
                select(DocumentVersion.id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(Document.knowledge_base_id == knowledge_base_id)
            )
        )
        aggregate_ids = [*version_ids, knowledge_base_id]
        if aggregate_ids:
            await session.execute(
                delete(OutboxEvent).where(OutboxEvent.aggregate_id.in_(aggregate_ids))
            )
        await session.execute(delete(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id))


def knowledge_settings(storage_root: Path) -> Settings:
    return Settings(
        model_provider=ModelProvider.OPENAI,
        openai_base_url=AnyHttpUrl("https://example.test/v1"),
        openai_api_key=SecretStr("chat-secret"),
        openai_model="chat-model",
        knowledge_enabled=True,
        database_url=SecretStr(DATABASE_URL),
        storage_root=storage_root,
        embedding_base_url=AnyHttpUrl("https://embedding.example.test/v1"),
        embedding_api_key=SecretStr("embedding-secret"),
        embedding_model="embedding-model",
        embedding_dimension=8,
        _env_file=None,  # type: ignore[call-arg]
    )


async def test_knowledge_base_edit_toggle_and_confirmed_cleanup(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "kb-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(
            name="知识库编辑集成", description="原描述"
        )
        knowledge_base_id = created["id"]
        updated = await management.update_knowledge_base(
            knowledge_base_id, name="知识库编辑后", description="新描述", enabled=False
        )
        assert updated["name"] == "知识库编辑后"
        assert updated["description"] == "新描述"
        assert updated["enabled"] is False
        assert (await management.get_knowledge_base(knowledge_base_id))["enabled"] is False

        accepted = await management.upload_document(
            knowledge_base_id, "cleanup.txt", "text/plain", "待清理正文".encode()
        )
        listed = await management.list_knowledge_bases()
        current = next(item for item in listed if item["id"] == knowledge_base_id)
        assert current["document_count"] == 1
        assert current["processing_count"] == 1
        with pytest.raises(KnowledgeConflictError) as conflict:
            await management.delete_knowledge_base(knowledge_base_id, confirm=False)
        assert conflict.value.requires_confirmation is True

        await management.delete_knowledge_base(knowledge_base_id, confirm=True)
        async with runtime.session_factory() as session:
            document = await session.get(Document, accepted["document_id"])
            event = await session.scalar(
                select(OutboxEvent).where(OutboxEvent.event_key == f"delete-kb:{knowledge_base_id}")
            )
        assert document is not None and document.status == "deleting"
        assert event is not None and event.status == "pending"
        client = MilvusClient(uri="http://127.0.0.1:19530")
        try:
            cleanup = KnowledgeCleanupService(
                runtime.session_factory,
                storage,
                MilvusChunkIndex(client, "knowledge_chunks"),
            )
            await cleanup.delete_knowledge_base(knowledge_base_id)
            await cleanup.delete_knowledge_base(knowledge_base_id)
        finally:
            client.close()
        async with runtime.session_factory() as session:
            assert await session.get(KnowledgeBase, knowledge_base_id) is None
        assert list(settings.storage_root.glob("*.txt")) == []
        async with runtime.session_factory() as session, session.begin():
            await session.execute(
                delete(OutboxEvent).where(OutboxEvent.event_key == f"delete-kb:{knowledge_base_id}")
            )
        knowledge_base_id = None
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_create_list_and_upload_commit_business_rows_with_outbox(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    service = SqlAlchemyKnowledgeManagementService(
        runtime.session_factory,
        LocalFileStorage(settings.storage_root),
        settings,
    )
    knowledge_base_id: UUID | None = None
    try:
        created = await service.create_knowledge_base(name="集成资料", description="真实数据库")
        knowledge_base_id = created["id"]

        listed = await service.list_knowledge_bases()
        accepted = await service.upload_document(
            knowledge_base_id,
            "guide.md",
            "text/markdown",
            "# 标题\n正文".encode(),
        )

        assert any(item["id"] == knowledge_base_id for item in listed)
        assert accepted["status"] == "pending"
        async with runtime.session_factory() as session:
            document = await session.scalar(
                select(Document).where(Document.id == accepted["document_id"])
            )
            version = await session.scalar(
                select(DocumentVersion).where(DocumentVersion.document_id == document.id)
            )
            job_count = await session.scalar(
                select(func.count())
                .select_from(IngestionJob)
                .where(IngestionJob.document_version_id == version.id)
            )
            event_count = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.aggregate_id == version.id)
            )

        assert document is not None
        assert version is not None
        assert job_count == 1
        assert event_count == 1
        assert (settings.storage_root / f"{version.id}.md").read_bytes() == "# 标题\n正文".encode()
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_duplicate_upload_requires_explicit_new_version(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "duplicate-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="去重集成", description="")
        knowledge_base_id = created["id"]
        content = "相同文档正文".encode()
        first = await management.upload_document(
            knowledge_base_id, "duplicate.txt", "text/plain", content
        )
        pipeline = IngestionPipeline(
            repository=SqlAlchemyIngestionRepository(runtime.session_factory, storage),
            parser=FixedParser(),
            chunker=ParentChildChunker(),
            embedder=FixedEmbedder(),
            index=MemoryIndex(),
        )
        await pipeline.run(first["job_id"])

        with pytest.raises(KnowledgeConflictError, match="duplicate_document"):
            await management.upload_document(
                knowledge_base_id, "duplicate.txt", "text/plain", content
            )
        second = await management.upload_document(
            knowledge_base_id,
            "duplicate.txt",
            "text/plain",
            content,
            on_duplicate="new_version",
        )

        assert second["document_id"] == first["document_id"]
        assert second["document_version_id"] != first["document_version_id"]
        async with runtime.session_factory() as session:
            versions = list(
                await session.scalars(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == first["document_id"])
                    .order_by(DocumentVersion.version_no)
                )
            )
        assert [version.version_no for version in versions] == [1, 2]
        assert len(list(settings.storage_root.glob("*.txt"))) == 2
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_concurrent_identical_uploads_create_one_pending_document(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "concurrent-duplicates")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="并发去重集成", description="")
        knowledge_base_id = created["id"]
        results = await asyncio.gather(
            management.upload_document(
                knowledge_base_id, "same.txt", "text/plain", "并发正文".encode()
            ),
            management.upload_document(
                knowledge_base_id, "same.txt", "text/plain", "并发正文".encode()
            ),
            return_exceptions=True,
        )
        accepted = [result for result in results if isinstance(result, dict)]
        rejected = [result for result in results if isinstance(result, KnowledgeConflictError)]
        assert len(accepted) == 1
        assert len(rejected) == 1 and rejected[0].code == "duplicate_document"
        assert len(await management.list_documents(knowledge_base_id)) == 1
        assert len(list(settings.storage_root.glob("*.txt"))) == 1
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_reindex_records_current_embedding_fingerprint(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "fingerprint-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="指纹重建集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "fingerprint.txt", "text/plain", "原始正文".encode()
        )
        await IngestionPipeline(
            repository=SqlAlchemyIngestionRepository(runtime.session_factory, storage),
            parser=FixedParser(),
            chunker=ParentChildChunker(),
            embedder=FixedEmbedder(),
            index=MemoryIndex(),
        ).run(accepted["job_id"])
        changed_settings = settings.model_copy(update={"embedding_model": "embedding-model-next"})
        changed_management = SqlAlchemyKnowledgeManagementService(
            runtime.session_factory, storage, changed_settings
        )
        await changed_management.request_document_action(accepted["document_id"], "reindex")
        async with runtime.session_factory() as session:
            versions = list(
                await session.scalars(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == accepted["document_id"])
                    .order_by(DocumentVersion.version_no)
                )
            )
        assert len(versions) == 2
        assert versions[1].embedding_model == "embedding-model-next"
        assert versions[1].embedding_fingerprint != versions[0].embedding_fingerprint
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_retry_and_reindex_requests_reuse_inflight_job(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "action-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="操作幂等集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "action.txt", "text/plain", "操作正文".encode()
        )
        with pytest.raises(KnowledgeConflictError, match="document_not_failed"):
            await management.request_document_action(accepted["document_id"], "retry")
        async with runtime.session_factory() as session, session.begin():
            job = await session.get(IngestionJob, accepted["job_id"])
            version = await session.get(DocumentVersion, accepted["document_version_id"])
            document = await session.get(Document, accepted["document_id"])
            assert job is not None and version is not None and document is not None
            job.status = "failed"
            version.status = "failed"
            document.status = "failed"

        retry_once = await management.request_document_action(accepted["document_id"], "retry")
        retry_twice = await management.request_document_action(accepted["document_id"], "retry")
        assert retry_once == retry_twice
        async with runtime.session_factory() as session:
            retry_events = list(
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_key == f"retry:{retry_once['job_id']}"
                    )
                )
            )
        assert len(retry_events) == 1

        await IngestionPipeline(
            repository=SqlAlchemyIngestionRepository(runtime.session_factory, storage),
            parser=FixedParser(),
            chunker=ParentChildChunker(),
            embedder=FixedEmbedder(),
            index=MemoryIndex(),
        ).run(retry_once["job_id"])
        reindex_once = await management.request_document_action(accepted["document_id"], "reindex")
        reindex_twice = await management.request_document_action(accepted["document_id"], "reindex")
        assert reindex_once == reindex_twice
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


class FixedParser:
    def parse(self, path: Path) -> list[ParsedSection]:
        assert path.exists()
        return [ParsedSection("数据库入库正文", ("集成",), page_start=1, page_end=1)]


class FixedEmbedder:
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 8 for _ in texts]


class FlakyEmbedder:
    def __init__(self) -> None:
        self.attempts = 0

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.attempts += 1
        if self.attempts == 1:
            raise ConnectionError("https://secret@embedding.test")
        return [[0.1] * 8 for _ in texts]


class MemoryIndex:
    def __init__(self) -> None:
        self.children: list[IndexedChild] = []

    async def replace_version(self, version_id: UUID, children: list[IndexedChild]) -> None:
        self.children = children

    async def count_version(self, version_id: UUID) -> int:
        return len(self.children)


class UnavailableIndex:
    async def replace_version(self, version_id: UUID, children: list[IndexedChild]) -> None:
        raise ConnectionError("milvus://secret@127.0.0.1")

    async def count_version(self, version_id: UUID) -> int:
        raise ConnectionError("milvus://secret@127.0.0.1")

    async def delete_version(self, version_id: UUID) -> None:
        raise ConnectionError("milvus://secret@127.0.0.1")


async def test_ingestion_repository_persists_chunks_and_atomically_activates_version(
    tmp_path: Path,
) -> None:
    settings = knowledge_settings(tmp_path / "pipeline-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="管线集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id,
            "pipeline.md",
            "text/markdown",
            "# 原始正文".encode(),
        )
        repository = SqlAlchemyIngestionRepository(runtime.session_factory, storage)
        index = MemoryIndex()
        pipeline = IngestionPipeline(
            repository=repository,
            parser=FixedParser(),
            chunker=ParentChildChunker(max_child_characters=100),
            embedder=FixedEmbedder(),
            index=index,
        )

        await pipeline.run(accepted["job_id"])

        async with runtime.session_factory() as session:
            document = await session.get(Document, accepted["document_id"])
            job = await session.get(IngestionJob, accepted["job_id"])
            chunks = list(
                await session.scalars(
                    select(Chunk)
                    .join(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
                    .where(DocumentVersion.document_id == accepted["document_id"])
                    .order_by(Chunk.ordinal)
                )
            )
        assert document is not None and document.active_version_id is not None
        assert document.status == "ready"
        assert job is not None and job.status == "succeeded" and job.progress == 100
        assert [chunk.kind for chunk in chunks] == ["parent", "child"]
        assert chunks[1].parent_chunk_id == chunks[0].id
        assert [child.chunk_id for child in index.children] == [chunks[1].id]
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_milvus_outage_persists_idempotent_cleanup_compensation(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "milvus-down-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="Milvus故障集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "milvus.txt", "text/plain", "索引故障正文".encode()
        )
        repository = SqlAlchemyIngestionRepository(runtime.session_factory, storage)
        pipeline = IngestionPipeline(
            repository=repository,
            parser=FixedParser(),
            chunker=ParentChildChunker(),
            embedder=FixedEmbedder(),
            index=UnavailableIndex(),
        )
        with pytest.raises(IngestionPipelineError, match="ingestion_failed") as caught:
            await pipeline.run(accepted["job_id"])
        assert "secret" not in str(caught.value)
        async with runtime.session_factory() as session:
            version = await session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == accepted["document_id"]
                )
            )
        assert version is not None and version.status == "failed"
        await repository.schedule_failed_version_cleanup(version.id)
        async with runtime.session_factory() as session:
            events = list(
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_key == f"failed-version:{version.id}"
                    )
                )
            )
        assert len(events) == 1
        assert events[0].status == "pending"
        assert events[0].available_at > datetime.now(UTC)

        client = MilvusClient(uri="http://127.0.0.1:19530")
        try:
            cleanup = KnowledgeCleanupService(
                runtime.session_factory,
                storage,
                MilvusChunkIndex(client, "knowledge_chunks"),
            )
            await cleanup.cleanup_version(version.id)
        finally:
            client.close()
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_embedding_outage_retries_same_persisted_job(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "embedding-down-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="Embedding故障集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "embedding.txt", "text/plain", "向量故障正文".encode()
        )
        embedder = FlakyEmbedder()
        pipeline = IngestionPipeline(
            repository=SqlAlchemyIngestionRepository(runtime.session_factory, storage),
            parser=FixedParser(),
            chunker=ParentChildChunker(),
            embedder=embedder,
            index=MemoryIndex(),
        )
        with pytest.raises(IngestionPipelineError, match="ingestion_failed") as caught:
            await pipeline.run(accepted["job_id"])
        assert "secret" not in str(caught.value)
        async with runtime.session_factory() as session:
            failed_job = await session.get(IngestionJob, accepted["job_id"])
        assert failed_job is not None and failed_job.status == "failed"

        await pipeline.run(accepted["job_id"])

        async with runtime.session_factory() as session:
            job = await session.get(IngestionJob, accepted["job_id"])
            document = await session.get(Document, accepted["document_id"])
        assert embedder.attempts == 2
        assert job is not None and job.status == "succeeded"
        assert document is not None and document.status == "ready"
        assert document.active_version_id is not None
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_transient_failure_schedules_one_durable_retry(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "durable-retry-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="持久重试集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "retry.txt", "text/plain", "重试正文".encode()
        )
        async with runtime.session_factory() as session, session.begin():
            job = await session.get(IngestionJob, accepted["job_id"])
            assert job is not None
            job.status = "failed"
            job.error_code = "ingestion_failed"
        repository = SqlAlchemyIngestionRepository(runtime.session_factory, storage)

        first = await repository.schedule_retry(accepted["job_id"], delay_seconds=2)
        duplicate = await repository.schedule_retry(accepted["job_id"], delay_seconds=2)

        assert first is True
        assert duplicate is False
        async with runtime.session_factory() as session:
            job = await session.get(IngestionJob, accepted["job_id"])
            retry_events = list(
                await session.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_key == f"retry-job:{accepted['job_id']}:1"
                    )
                )
            )
        assert job is not None and job.status == "retry_wait"
        assert job.attempt_count == 1
        assert len(retry_events) == 1
        assert retry_events[0].available_at > datetime.now(UTC)
        assert retry_events[0].payload["job_id"] == str(accepted["job_id"])
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_stale_running_job_is_requeued_from_postgresql_outbox(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "recovery-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="恢复集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id,
            "recovery.txt",
            "text/plain",
            "恢复正文".encode(),
        )
        async with runtime.session_factory() as session, session.begin():
            job = await session.get(IngestionJob, accepted["job_id"])
            assert job is not None
            job.status = "running"
            job.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)

        repository = SqlAlchemyIngestionRepository(runtime.session_factory, storage)
        recovered = await repository.recover_stale_jobs(
            stale_before=datetime.now(UTC) - timedelta(minutes=5), limit=10
        )

        assert recovered == [accepted["job_id"]]
        async with runtime.session_factory() as session:
            job = await session.get(IngestionJob, accepted["job_id"])
            recovery_events = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.event_key.like(f"recover:{accepted['job_id']}:%"))
            )
        assert job is not None and job.status == "retry_wait" and job.attempt_count == 1
        assert recovery_events == 1
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_failed_transient_job_is_recovered_after_worker_crash(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "failed-recovery-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="失败恢复集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "failed.txt", "text/plain", "可恢复正文".encode()
        )
        async with runtime.session_factory() as session, session.begin():
            job = await session.get(IngestionJob, accepted["job_id"])
            assert job is not None
            job.status = "failed"
            job.error_code = "ingestion_failed"
            job.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
        repository = SqlAlchemyIngestionRepository(runtime.session_factory, storage)

        recovered = await repository.recover_stale_jobs(
            stale_before=datetime.now(UTC) - timedelta(minutes=5), limit=10
        )

        assert recovered == [accepted["job_id"]]
        async with runtime.session_factory() as session:
            job = await session.get(IngestionJob, accepted["job_id"])
        assert job is not None and job.status == "retry_wait" and job.attempt_count == 1
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_expired_outbox_claim_is_recovered(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "lease-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="投递恢复集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "lease.txt", "text/plain", "恢复投递".encode()
        )
        async with runtime.session_factory() as session, session.begin():
            version_id = await session.scalar(
                select(DocumentVersion.id).where(
                    DocumentVersion.document_id == accepted["document_id"]
                )
            )
            event = await session.scalar(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == version_id)
            )
            assert event is not None
            event.status = "processing"
            event.available_at = datetime.now(UTC) - timedelta(minutes=1)
            event_id = event.id

        repository = SqlAlchemyOutboxRepository(runtime.session_factory)
        claimed = await repository.claim_pending(100)

        assert event_id in [event.id for event in claimed]
        async with runtime.session_factory() as session:
            event = await session.get(OutboxEvent, event_id)
        assert event is not None and event.attempt_count == 1
        assert event.available_at > datetime.now(UTC)
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


class UnavailablePublisher:
    async def publish(self, event: PendingEvent) -> None:
        raise ConnectionError("redis://secret@broker.test/private")


async def test_outbox_survives_broker_failure_then_reaches_real_redis(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "broker-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    queue = f"knowledge-fault-{uuid4()}"
    redis = Redis.from_url("redis://127.0.0.1:6379/0")
    try:
        created = await management.create_knowledge_base(name="队列故障集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "broker.txt", "text/plain", "已提交正文".encode()
        )
        async with runtime.session_factory() as session, session.begin():
            version_id = await session.scalar(
                select(DocumentVersion.id).where(
                    DocumentVersion.document_id == accepted["document_id"]
                )
            )
            event = await session.scalar(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == version_id)
            )
            assert event is not None
            event.available_at = datetime(2000, 1, 1, tzinfo=UTC)
            event_id = event.id
        repository = SqlAlchemyOutboxRepository(runtime.session_factory)

        assert (
            await OutboxDispatcher(repository, UnavailablePublisher()).dispatch_once(limit=1) == 0
        )
        async with runtime.session_factory() as session:
            failed_event = await session.get(OutboxEvent, event_id)
        assert failed_event is not None
        assert failed_event.status == "pending" and failed_event.attempt_count == 1
        assert failed_event.last_error_reference is not None
        assert "secret" not in failed_event.last_error_reference

        async with runtime.session_factory() as session, session.begin():
            event = await session.get(OutboxEvent, event_id)
            assert event is not None
            event.available_at = datetime(2000, 1, 1, tzinfo=UTC)
        publisher = CeleryEventPublisher("redis://127.0.0.1:6379/0", queue=queue)
        assert await OutboxDispatcher(repository, publisher).dispatch_once(limit=1) == 1
        assert await redis.llen(queue) == 1
        async with runtime.session_factory() as session:
            published_event = await session.get(OutboxEvent, event_id)
        assert published_event is not None and published_event.status == "published"
    finally:
        await redis.delete(queue)
        await redis.aclose()
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_unavailable_postgresql_connection_returns_retryable_safe_error(
    tmp_path: Path,
) -> None:
    unavailable = DatabaseRuntime("postgresql+asyncpg://secret:secret@127.0.0.1:65432/agent")
    repository = SqlAlchemyIngestionRepository(
        unavailable.session_factory, LocalFileStorage(tmp_path / "db-down-uploads")
    )
    pipeline = IngestionPipeline(
        repository=repository,
        parser=FixedParser(),
        chunker=ParentChildChunker(),
        embedder=FixedEmbedder(),
        index=MemoryIndex(),
    )
    try:
        with pytest.raises(IngestionPipelineError, match="ingestion_unavailable") as caught:
            await pipeline.run(uuid4())
        assert "secret" not in str(caught.value)
    finally:
        await unavailable.close()


async def test_delete_requests_are_idempotent_while_queued(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "delete-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="删除幂等集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "delete.txt", "text/plain", "删除正文".encode()
        )

        first = await management.delete_document(accepted["document_id"])
        second = await management.delete_document(accepted["document_id"])
        await management.delete_knowledge_base(knowledge_base_id, confirm=True)
        await management.delete_knowledge_base(knowledge_base_id, confirm=True)

        assert first == second
        async with runtime.session_factory() as session:
            document_events = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.event_key == f"delete:{accepted['document_id']}")
            )
            kb_events = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.event_key == f"delete-kb:{knowledge_base_id}")
            )
        assert document_events == 1
        assert kb_events == 1
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_progress_stream_does_not_repeat_same_job_update(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "stream-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="事件流集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "stream.txt", "text/plain", "事件正文".encode()
        )
        stream = management.stream_events()
        first_event = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.1)
        async with runtime.session_factory() as session, session.begin():
            job = await session.get(IngestionJob, accepted["job_id"])
            assert job is not None
            job.current_stage = "parsing"
            job.progress = 10
        first = await asyncio.wait_for(first_event, timeout=2)
        assert first["job_id"] == str(accepted["job_id"])
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(stream), timeout=1.3)
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_deleting_document_cannot_be_reactivated_by_late_ingestion(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "delete-race-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="删除竞态集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "race.txt", "text/plain", "正文".encode()
        )
        await management.delete_document(accepted["document_id"])
        repository = SqlAlchemyIngestionRepository(runtime.session_factory, storage)

        with pytest.raises(RuntimeError, match="ingestion_document_deleting"):
            await repository.update_stage(accepted["job_id"], "parsing", 10)
        async with runtime.session_factory() as session:
            version_id = await session.scalar(
                select(DocumentVersion.id).where(
                    DocumentVersion.document_id == accepted["document_id"]
                )
            )
        assert version_id is not None
        with pytest.raises(RuntimeError, match="ingestion_document_deleting"):
            await repository.activate(accepted["document_id"], version_id, None)
        await repository.mark_failed(version_id, "document_parse_failed")
        async with runtime.session_factory() as session:
            document = await session.get(Document, accepted["document_id"])
        assert document is not None and document.status == "deleting"
        assert document.active_version_id is None
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_real_redis_worker_consumes_document_deletion(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "worker-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    worker: asyncio.subprocess.Process | None = None
    try:
        created = await management.create_knowledge_base(name="真实队列集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "worker.txt", "text/plain", "队列正文".encode()
        )
        deletion = await management.delete_document(accepted["document_id"])
        worker_env = os.environ.copy()
        worker_env.update(
            {
                "AGENT_MODEL_PROVIDER": "openai",
                "AGENT_OPENAI_BASE_URL": "https://example.test/v1",
                "AGENT_OPENAI_API_KEY": "test-chat-key",
                "AGENT_OPENAI_MODEL": "chat-model",
                "AGENT_KNOWLEDGE_ENABLED": "true",
                "AGENT_DATABASE_URL": DATABASE_URL,
                "AGENT_REDIS_URL": "redis://127.0.0.1:6379/0",
                "AGENT_STORAGE_ROOT": str(settings.storage_root),
                "AGENT_EMBEDDING_BASE_URL": "https://example.test/v1",
                "AGENT_EMBEDDING_API_KEY": "test-embedding-key",
                "AGENT_EMBEDDING_MODEL": "embedding-model",
            }
        )
        worker = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "celery",
            "-A",
            "agent_api.knowledge.worker.celery_app:app",
            "worker",
            "--pool=solo",
            "-Q",
            "knowledge",
            "--loglevel=WARNING",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
            cwd=Path.cwd(),
            env=worker_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        publisher = CeleryEventPublisher("redis://127.0.0.1:6379/0")
        await publisher.publish(
            PendingEvent(
                id=uuid4(),
                event_type="document.deletion.requested",
                aggregate_id=accepted["document_id"],
                payload={
                    "job_id": str(deletion["job_id"]),
                    "document_id": str(accepted["document_id"]),
                },
            )
        )
        for _ in range(60):
            assert worker.returncode is None, "Celery Worker 提前退出"
            async with runtime.session_factory() as session:
                job = await session.get(IngestionJob, deletion["job_id"])
                document = await session.get(Document, accepted["document_id"])
            if job is not None and job.status == "succeeded":
                assert document is not None and document.status == "deleted"
                break
            await asyncio.sleep(1)
        else:
            pytest.fail("Celery Worker 未在 60 秒内消费删除事件")
    finally:
        if worker is not None:
            worker.terminate()
            await asyncio.wait_for(worker.wait(), timeout=10)
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


class LocalEmbeddingHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/v1/embeddings":
            self.send_error(404)
            return
        content_length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(content_length))
        texts = request["input"]
        if isinstance(texts, str):
            texts = [texts]
        body = json.dumps(
            {
                "object": "list",
                "model": "text-embedding-3-small",
                "data": [
                    {"object": "embedding", "index": index, "embedding": [0.1] * 1536}
                    for index, _ in enumerate(texts)
                ],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # 集成测试不输出用户正文或请求头。
        return


async def test_queued_upload_becomes_ready_after_worker_starts(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "queued-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalEmbeddingHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    knowledge_base_id: UUID | None = None
    version_id: UUID | None = None
    worker: asyncio.subprocess.Process | None = None
    try:
        created = await management.create_knowledge_base(name="队列上传集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "queued.txt", "text/plain", "队列启动后处理的正文".encode()
        )
        async with runtime.session_factory() as session:
            version_id = await session.scalar(
                select(DocumentVersion.id).where(
                    DocumentVersion.document_id == accepted["document_id"]
                )
            )
        assert version_id is not None
        publisher = CeleryEventPublisher("redis://127.0.0.1:6379/0")
        await publisher.publish(
            PendingEvent(
                id=uuid4(),
                event_type="document.ingestion.requested",
                aggregate_id=version_id,
                payload={"job_id": str(accepted["job_id"])},
            )
        )
        # 重建 API 侧数据库连接和服务实例，确认任务状态不依赖进程内存。
        await runtime.close()
        runtime = DatabaseRuntime(DATABASE_URL)
        management = SqlAlchemyKnowledgeManagementService(
            runtime.session_factory, storage, settings
        )
        restored_document = await management.get_document(accepted["document_id"])
        assert restored_document["job"]["id"] == accepted["job_id"]
        # 上传已提交且 Worker 尚未启动时重启 Redis，验证持久队列不会丢失任务。
        restart = await asyncio.create_subprocess_exec(
            "docker",
            "compose",
            "restart",
            "redis",
            cwd=Path.cwd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        _, restart_error = await asyncio.wait_for(restart.communicate(), timeout=60)
        assert restart.returncode == 0, restart_error.decode(errors="replace")
        redis = Redis.from_url("redis://127.0.0.1:6379/0")
        try:
            assert await redis.ping()
        finally:
            await redis.aclose()
        worker_env = os.environ.copy()
        worker_env.update(
            {
                "AGENT_MODEL_PROVIDER": "openai",
                "AGENT_OPENAI_BASE_URL": "https://example.test/v1",
                "AGENT_OPENAI_API_KEY": "test-chat-key",
                "AGENT_OPENAI_MODEL": "chat-model",
                "AGENT_KNOWLEDGE_ENABLED": "true",
                "AGENT_DATABASE_URL": DATABASE_URL,
                "AGENT_REDIS_URL": "redis://127.0.0.1:6379/0",
                "AGENT_STORAGE_ROOT": str(settings.storage_root),
                "AGENT_EMBEDDING_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "AGENT_EMBEDDING_API_KEY": "test-embedding-key",
                "AGENT_EMBEDDING_MODEL": "text-embedding-3-small",
            }
        )
        worker = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "celery",
            "-A",
            "agent_api.knowledge.worker.celery_app:app",
            "worker",
            "--pool=solo",
            "-Q",
            "knowledge",
            "--loglevel=WARNING",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
            cwd=Path.cwd(),
            env=worker_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for _ in range(90):
            assert worker.returncode is None, "Celery Worker 提前退出"
            async with runtime.session_factory() as session:
                job = await session.get(IngestionJob, accepted["job_id"])
                document = await session.get(Document, accepted["document_id"])
            if job is not None and job.status == "succeeded":
                assert document is not None and document.active_version_id == version_id
                assert document.status == "ready"
                break
            await asyncio.sleep(1)
        else:
            pytest.fail("Celery Worker 未在 90 秒内完成上传")
        client = MilvusClient(uri="http://127.0.0.1:19530")
        try:
            assert await MilvusChunkIndex(client, "knowledge_chunks").count_version(version_id) > 0
        finally:
            client.close()
    finally:
        if worker is not None:
            worker.terminate()
            await asyncio.wait_for(worker.wait(), timeout=10)
        server.shutdown()
        server.server_close()
        if version_id is not None:
            client = MilvusClient(uri="http://127.0.0.1:19530")
            try:
                await MilvusChunkIndex(client, "knowledge_chunks").delete_version(version_id)
            finally:
                client.close()
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()
