import os
from pathlib import Path
from uuid import UUID

import pytest
from pymilvus import MilvusClient
from sqlalchemy import select

from agent_api.knowledge.application.ingestion import (
    IngestionPipeline,
    IngestionPipelineError,
    ParentChildChunker,
)
from agent_api.knowledge.application.management import SqlAlchemyKnowledgeManagementService
from agent_api.knowledge.infrastructure.database.models import (
    Document,
    DocumentVersion,
    OutboxEvent,
)
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.infrastructure.jobs.repository import SqlAlchemyIngestionRepository
from agent_api.knowledge.infrastructure.milvus.index import IndexedChild, MilvusChunkIndex
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage
from agent_api.knowledge.worker.cleanup import KnowledgeCleanupService
from tests.test_knowledge_management_integration import (
    DATABASE_URL,
    FixedParser,
    cleanup_knowledge_base,
    knowledge_settings,
)

pytestmark = [
    pytest.mark.knowledge_integration,
    pytest.mark.skipif(
        os.getenv("RUN_KNOWLEDGE_INTEGRATION") != "1",
        reason="set RUN_KNOWLEDGE_INTEGRATION=1 to use local knowledge containers",
    ),
]


class WideEmbedder:
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 1536 for _ in texts]


class MismatchedCountIndex:
    def __init__(self, actual: MilvusChunkIndex, failed_version_id: UUID) -> None:
        self.actual = actual
        self.failed_version_id = failed_version_id

    async def replace_version(self, version_id: UUID, children: list[IndexedChild]) -> None:
        await self.actual.replace_version(version_id, children)

    async def count_version(self, version_id: UUID) -> int:
        if version_id == self.failed_version_id:
            return 0
        return await self.actual.count_version(version_id)

    async def delete_version(self, version_id: UUID) -> None:
        await self.actual.delete_version(version_id)


async def test_failed_reindex_retains_ready_version_and_cleans_new_index(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "versions").model_copy(
        update={"embedding_dimension": 1536}
    )
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    client = MilvusClient(uri="http://127.0.0.1:19530")
    index = MilvusChunkIndex(client, "knowledge_chunks")
    knowledge_base_id: UUID | None = None
    version_ids: list[UUID] = []
    try:
        created = await management.create_knowledge_base(name="重建失败集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "version.txt", "text/plain", "原始版本正文".encode()
        )
        repository = SqlAlchemyIngestionRepository(runtime.session_factory, storage)
        pipeline = IngestionPipeline(
            repository=repository,
            parser=FixedParser(),
            chunker=ParentChildChunker(),
            embedder=WideEmbedder(),
            index=index,
        )
        await pipeline.run(accepted["job_id"])
        async with runtime.session_factory() as session:
            first_version = await session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == accepted["document_id"]
                )
            )
        assert first_version is not None
        version_ids.append(first_version.id)
        assert await index.count_version(first_version.id) > 0

        reindex = await management.request_document_action(accepted["document_id"], "reindex")
        async with runtime.session_factory() as session:
            second_version = await session.scalar(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == accepted["document_id"])
                .order_by(DocumentVersion.version_no.desc())
                .limit(1)
            )
        assert second_version is not None and second_version.id != first_version.id
        version_ids.append(second_version.id)
        failing_pipeline = IngestionPipeline(
            repository=repository,
            parser=FixedParser(),
            chunker=ParentChildChunker(),
            embedder=WideEmbedder(),
            index=MismatchedCountIndex(index, second_version.id),
        )
        with pytest.raises(IngestionPipelineError, match="index_verification_failed"):
            await failing_pipeline.run(reindex["job_id"])

        async with runtime.session_factory() as session:
            document = await session.get(Document, accepted["document_id"])
            failed_version = await session.get(DocumentVersion, second_version.id)
        assert document is not None
        assert document.active_version_id == first_version.id
        assert document.status == "ready"
        assert failed_version is not None and failed_version.status == "failed"
        assert await index.count_version(first_version.id) > 0
        assert await index.count_version(second_version.id) == 0

        successful_reindex = await management.request_document_action(
            accepted["document_id"], "reindex"
        )
        await pipeline.run(successful_reindex["job_id"])
        async with runtime.session_factory() as session:
            document = await session.get(Document, accepted["document_id"])
            cleanup_event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_key == f"cleanup-version:{first_version.id}"
                )
            )
        assert document is not None and document.active_version_id is not None
        assert document.active_version_id not in {first_version.id, second_version.id}
        version_ids.append(document.active_version_id)
        assert cleanup_event is not None
        assert await index.count_version(first_version.id) > 0
        assert await index.count_version(document.active_version_id) > 0
        cleanup = KnowledgeCleanupService(runtime.session_factory, storage, index)
        await cleanup.cleanup_version(first_version.id)
        await cleanup.cleanup_version(first_version.id)
        assert await index.count_version(first_version.id) == 0
        assert await index.count_version(document.active_version_id) > 0
        await cleanup.cleanup_version(document.active_version_id)
        assert await index.count_version(document.active_version_id) > 0
    finally:
        for version_id in version_ids:
            await index.delete_version(version_id)
        client.close()
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()
