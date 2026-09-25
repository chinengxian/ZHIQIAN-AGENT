import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pymilvus import MilvusClient  # type: ignore[import-untyped]

from agent_api.knowledge.application.ingestion import IngestionPipeline, ParentChildChunker
from agent_api.knowledge.application.management import SqlAlchemyKnowledgeManagementService
from agent_api.knowledge.application.retrieval import KnowledgeRetrievalService
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

CASES = json.loads((Path(__file__).parent / "knowledge_cases.json").read_text(encoding="utf-8"))


async def test_fixed_hybrid_retrieval_baseline(tmp_path: Path) -> None:
    settings = settings_for_e2e(tmp_path / "eval-uploads")
    database = DatabaseRuntime(settings.database_url.get_secret_value())
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(database.session_factory, storage, settings)
    milvus = MilvusClient(uri=settings.milvus_uri)
    index = MilvusChunkIndex(milvus, settings.milvus_collection)
    embedder = DeterministicEmbedding()
    pipeline = IngestionPipeline(
        repository=SqlAlchemyIngestionRepository(database.session_factory, storage),
        parser=DoclingParser(),
        chunker=ParentChildChunker(),
        embedder=embedder,
        index=index,
    )
    retrieval = KnowledgeRetrievalService(database.session_factory, index, embedder)
    base_id: UUID | None = None
    versions: list[UUID] = []
    expected: dict[str, UUID] = {}
    try:
        created = await management.create_knowledge_base(name=f"固定评测-{uuid4()}", description="")
        base_id = created["id"]
        for case in CASES["cases"]:
            accepted = await management.upload_document(
                base_id,
                f"{case['keyword']}.txt",
                "text/plain",
                case["text"].encode(),
            )
            versions.append(accepted["document_version_id"])
            expected[case["keyword"]] = accepted["document_id"]
            await pipeline.run(accepted["job_id"])

        ranks: list[int | None] = []
        citation_correct = 0
        for case in CASES["cases"]:
            sources, _ = await retrieval.search(case["question"], (base_id,), mode="hybrid")
            rank = next(
                (
                    index + 1
                    for index, source in enumerate(sources)
                    if source.document_id == expected[case["keyword"]]
                ),
                None,
            )
            ranks.append(rank)
            if sources and sources[0].document_id == expected[case["keyword"]]:
                citation_correct += 1
        recall_at_8 = sum(rank is not None for rank in ranks) / len(ranks)
        mrr = sum(1 / rank for rank in ranks if rank is not None) / len(ranks)
        citation_correctness = citation_correct / len(ranks)

        false_answers = 0
        for question in CASES["unanswerable"]:
            sources, _ = await retrieval.search(question, (base_id,), mode="hybrid")
            if any(question.casefold() in source.excerpt.casefold() for source in sources):
                false_answers += 1
        false_answer_rate = false_answers / len(CASES["unanswerable"])
        print(
            "EVAL "
            + json.dumps(
                {
                    "recall_at_8": recall_at_8,
                    "mrr": mrr,
                    "citation_correctness_top_1": citation_correctness,
                    "no_answer_false_answer_rate": false_answer_rate,
                    "ranks": ranks,
                }
            )
        )
        assert recall_at_8 >= 0.9
        assert false_answer_rate == 0
    finally:
        for version in versions:
            await index.delete_version(version)
        if base_id is not None:
            await cleanup_knowledge_base(database, base_id)
        milvus.close()
        await database.close()
