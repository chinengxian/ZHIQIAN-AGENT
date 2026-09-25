import os
from pathlib import Path
from uuid import UUID

import pytest
from pymilvus import MilvusClient  # type: ignore[import-untyped]
from sqlalchemy import update

from agent_api.knowledge.application.ingestion import IngestionPipeline, ParentChildChunker
from agent_api.knowledge.application.management import SqlAlchemyKnowledgeManagementService
from agent_api.knowledge.application.retrieval import (
    KnowledgeRetrievalService,
    KnowledgeScopeError,
)
from agent_api.knowledge.infrastructure.database.models import Document, KnowledgeBase
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.infrastructure.docling.parser import DoclingParser
from agent_api.knowledge.infrastructure.jobs.repository import SqlAlchemyIngestionRepository
from agent_api.knowledge.infrastructure.milvus.index import MilvusChunkIndex
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage
from tests.test_ingestion_e2e_integration import DeterministicEmbedding, settings_for_e2e
from tests.test_knowledge_management_integration import cleanup_knowledge_base

pytestmark = [
    pytest.mark.knowledge_integration,
    pytest.mark.skipif(
        os.getenv("RUN_KNOWLEDGE_INTEGRATION") != "1",
        reason="set RUN_KNOWLEDGE_INTEGRATION=1 to use local knowledge containers",
    ),
]


class FailingReranker:
    async def rerank(self, query: str, texts: list[str]) -> list[int]:
        raise RuntimeError("model unavailable")


async def test_real_milvus_modes_scope_and_active_version(tmp_path: Path) -> None:
    settings = settings_for_e2e(tmp_path / "uploads")
    database = DatabaseRuntime(settings.database_url.get_secret_value())
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(database.session_factory, storage, settings)
    client = MilvusClient(uri=settings.milvus_uri)
    index = MilvusChunkIndex(client, settings.milvus_collection)
    embedder = DeterministicEmbedding()
    pipeline = IngestionPipeline(
        repository=SqlAlchemyIngestionRepository(database.session_factory, storage),
        parser=DoclingParser(),
        chunker=ParentChildChunker(),
        embedder=embedder,
        index=index,
    )
    service = KnowledgeRetrievalService(database.session_factory, index, embedder)
    base_id: UUID | None = None
    version_ids: list[UUID] = []
    try:
        base = await management.create_knowledge_base(name="检索联调", description="")
        base_id = base["id"]
        accepted = await management.upload_document(
            base_id,
            "orbit.txt",
            "text/plain",
            b"orbit engine maintenance manual and launch procedures",
        )
        version_id = accepted["document_version_id"]
        version_ids.append(version_id)
        await pipeline.run(accepted["job_id"])
        assert await service.resolve_scope("all_enabled") == (base_id,)
        assert await service.resolve_scope("selected", (base_id,)) == (base_id,)
        with pytest.raises(KnowledgeScopeError, match="knowledge_scope_unavailable"):
            await service.resolve_scope("selected", (UUID(int=99),))

        for mode in ("hybrid", "semantic", "keyword"):
            sources, degraded = await service.search("orbit engine", (base_id,), mode=mode)
            assert sources and sources[0].document_id == accepted["document_id"]
            assert sources[0].document_version_id == version_id
            assert "orbit engine" in sources[0].excerpt
            assert sources[0].citation_id == "[1]"
            assert degraded is False

        documents = await service.list_documents((base_id,), title="orbit")
        assert documents[0]["document_id"] == str(accepted["document_id"])
        read = await service.read((base_id,), document_id=accepted["document_id"])
        assert read and "orbit engine" in read[0].excerpt
        assert await service.search("orbit engine", ()) == ([], False)
        assert await service.read((), document_id=accepted["document_id"]) == []

        second = await management.upload_document(
            base_id,
            "checklist.txt",
            "text/plain",
            b"orbit engine fuel checklist and launch safety",
        )
        version_ids.append(second["document_version_id"])
        await pipeline.run(second["job_id"])
        degraded_service = KnowledgeRetrievalService(
            database.session_factory, index, embedder, FailingReranker()
        )
        degraded_sources, degraded = await degraded_service.search("orbit engine", (base_id,))
        assert len(degraded_sources) == 2
        assert degraded is True

        async with database.session_factory() as session, session.begin():
            await session.execute(
                update(Document)
                .where(Document.id == accepted["document_id"])
                .values(active_version_id=None)
            )
        current_sources, _ = await service.search("orbit engine", (base_id,))
        assert all(source.document_id != accepted["document_id"] for source in current_sources)

        async with database.session_factory() as session, session.begin():
            await session.execute(
                update(KnowledgeBase).where(KnowledgeBase.id == base_id).values(enabled=False)
            )
        assert await service.resolve_scope("all_enabled") == ()
        with pytest.raises(KnowledgeScopeError, match="knowledge_scope_unavailable"):
            await service.resolve_scope("selected", (base_id,))
        assert await service.search("orbit engine", (base_id,)) == ([], False)
    finally:
        for version_id in version_ids:
            await index.delete_version(version_id)
        if base_id is not None:
            await cleanup_knowledge_base(database, base_id)
        client.close()
        await database.close()
