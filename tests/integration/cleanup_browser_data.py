import asyncio
import sys
from pathlib import Path
from uuid import UUID

from pymilvus import MilvusClient  # type: ignore[import-untyped]
from sqlalchemy import delete, select

from agent_api.knowledge.infrastructure.database.models import (
    Document,
    DocumentVersion,
    KnowledgeBase,
    OutboxEvent,
)
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.infrastructure.milvus.index import MilvusChunkIndex
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage
from agent_api.knowledge.worker.cleanup import KnowledgeCleanupService
from tests.test_knowledge_management_integration import DATABASE_URL


async def cleanup(ids: list[UUID]) -> None:
    database = DatabaseRuntime(DATABASE_URL)
    milvus = MilvusClient(uri="http://127.0.0.1:19530")
    service = KnowledgeCleanupService(
        database.session_factory,
        LocalFileStorage(Path("data/browser-knowledge-e2e/uploads")),
        MilvusChunkIndex(milvus, "knowledge_chunks"),
    )
    try:
        for base_id in ids:
            async with database.session_factory() as session:
                base = await session.get(KnowledgeBase, base_id)
                if base is None:
                    continue
                if not base.name.startswith("引用闭环-") or base.enabled:
                    raise RuntimeError("refusing_to_cleanup_non_test_knowledge_base")
                versions = list(
                    await session.scalars(
                        select(DocumentVersion.id)
                        .join(Document, Document.id == DocumentVersion.document_id)
                        .where(Document.knowledge_base_id == base_id)
                    )
                )
            await service.delete_knowledge_base(base_id)
            async with database.session_factory() as session, session.begin():
                await session.execute(
                    delete(OutboxEvent).where(OutboxEvent.aggregate_id.in_([base_id, *versions]))
                )
    finally:
        milvus.close()
        await database.close()


if __name__ == "__main__":
    asyncio.run(cleanup([UUID(value) for value in sys.argv[1:]]))
