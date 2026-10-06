from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pymilvus import MilvusClient  # type: ignore[import-untyped]
from sqlalchemy import select

from agent_api.knowledge.application.ingestion import IngestionPipeline, ParentChildChunker
from agent_api.knowledge.application.management import SqlAlchemyKnowledgeManagementService
from agent_api.knowledge.application.retrieval import KnowledgeRetrievalService
from agent_api.knowledge.infrastructure.database.models import (
    Chunk,
    Document,
    WikiClaim,
    WikiClaimSource,
    WikiJob,
)
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.infrastructure.docling.parser import DoclingParser
from agent_api.knowledge.infrastructure.jobs.repository import SqlAlchemyIngestionRepository
from agent_api.knowledge.infrastructure.milvus.index import MilvusChunkIndex
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage
from agent_api.knowledge.wiki.generator import WikiGeneratedClaim, WikiGeneration
from agent_api.knowledge.wiki.processor import WikiProcessor
from agent_api.knowledge.wiki.retrieval import WikiRetrievalService, WikiSearchResult
from agent_api.knowledge.wiki.service import WikiService
from tests.test_ingestion_e2e_integration import DeterministicEmbedding, settings_for_e2e
from tests.test_knowledge_management_integration import cleanup_knowledge_base

CASES_PATH = Path(__file__).with_name("wiki_cases.json")
FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "wiki" / "enterprise-it-operations"
WIKI_DATABASE_URL = os.getenv("AGENT_WIKI_TEST_DATABASE_URL")


class DeterministicGroundedWikiGenerator:
    """把每个原文分块原样发布为带来源的评测 claim。"""

    model_name = "deterministic-grounded-eval"

    async def generate(self, title: str, excerpts: list[str]) -> WikiGeneration:
        del title
        return WikiGeneration(
            summary_claims=tuple(
                WikiGeneratedClaim(text=excerpt, source_indices=(index,))
                for index, excerpt in enumerate(excerpts, 1)
            ),
            topics=(),
            measured_tokens=sum(len(excerpt) for excerpt in excerpts),
        )


def test_wiki_eval_cases_cover_all_approved_questions() -> None:
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"]

    assert [case["id"] for case in cases] == [f"Q{index:02d}" for index in range(1, 17)]
    assert all(len(case["queries"]) >= 3 for case in cases)
    assert all(
        {"expected_documents", "expected_terms", "forbidden_terms", "answerable"}
        <= case.keys()
        for case in cases
    )
    assert cases[-1]["answerable"] is False
    assert cases[-1]["expected_documents"] == []


def _contains(text: str, term: str) -> bool:
    return term.casefold() in text.casefold()


def _result_documents(result: WikiSearchResult) -> set[str]:
    return {
        source.filename
        for page in result.pages
        for claim in page.matched_claims
        for source in claim.sources
    }


def _relevant_citations(
    result: WikiSearchResult, expected_terms: list[str]
) -> list[str]:
    return [
        source.filename
        for page in result.pages
        for claim in page.matched_claims
        if any(_contains(claim.text, term) for term in expected_terms)
        for source in claim.sources
    ]


def _pairwise_consistency(document_sets: list[set[str]]) -> float:
    scores: list[float] = []
    for left_index, left in enumerate(document_sets):
        for right in document_sets[left_index + 1 :]:
            union = left | right
            scores.append(len(left & right) / len(union) if union else 1.0)
    return sum(scores) / len(scores)


async def _count_source_leaks(
    database: DatabaseRuntime, claim_ids: set[UUID]
) -> tuple[int, int]:
    if not claim_ids:
        return 0, 0
    async with database.session_factory() as session:
        claims = list(
            await session.scalars(select(WikiClaim).where(WikiClaim.id.in_(claim_ids)))
        )
        needs_review = sum(claim.trust_state != "verified" for claim in claims)
        sources = list(
            await session.scalars(
                select(WikiClaimSource).where(WikiClaimSource.claim_id.in_(claim_ids))
            )
        )
        invalid = 0
        for source in sources:
            document = await session.get(Document, source.document_id)
            chunk = await session.get(Chunk, source.chunk_id) if source.chunk_id else None
            if (
                document is None
                or document.active_version_id != source.document_version_id
                or document.status != "ready"
                or document.deleted_at is not None
                or chunk is None
                or chunk.kind != source.chunk_kind
                or chunk.ordinal != source.chunk_ordinal
                or sha256(chunk.content.encode()).hexdigest() != source.content_sha256
            ):
                invalid += 1
    return needs_review, invalid


@pytest.mark.knowledge_integration
@pytest.mark.skipif(
    os.getenv("RUN_KNOWLEDGE_INTEGRATION") != "1" or not WIKI_DATABASE_URL,
    reason="需同时设置 RUN_KNOWLEDGE_INTEGRATION=1 和独立 Wiki 测试数据库 URL",
)
async def test_wiki_hybrid_retrieval_quality(tmp_path: Path) -> None:
    payload: dict[str, list[dict[str, Any]]] = json.loads(
        CASES_PATH.read_text(encoding="utf-8")
    )
    settings = settings_for_e2e(tmp_path / "wiki-eval-uploads")
    assert WIKI_DATABASE_URL is not None
    database = DatabaseRuntime(WIKI_DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(
        database.session_factory, storage, settings
    )
    milvus = MilvusClient(uri=settings.milvus_uri)
    index = MilvusChunkIndex(milvus, settings.milvus_collection)
    embedder = DeterministicEmbedding()
    pipeline = IngestionPipeline(
        repository=SqlAlchemyIngestionRepository(database.session_factory, storage),
        parser=DoclingParser(),
        chunker=ParentChildChunker(max_child_characters=600),
        embedder=embedder,
        index=index,
    )
    source_retrieval = KnowledgeRetrievalService(
        database.session_factory, index, embedder
    )
    retrieval = WikiRetrievalService(database.session_factory, source_retrieval)
    wiki = WikiService(database.session_factory)
    processor = WikiProcessor(
        database.session_factory, DeterministicGroundedWikiGenerator()
    )
    knowledge_base_id: UUID | None = None
    version_ids: list[UUID] = []
    returned_claim_ids: set[UUID] = set()
    try:
        created = await management.create_knowledge_base(
            name=f"Wiki 固定评测-{uuid4()}", description=""
        )
        knowledge_base_id = created["id"]
        for path in sorted(FIXTURE_PATH.glob("0[1-8]-*.md")):
            accepted = await management.upload_document(
                knowledge_base_id,
                path.name,
                "text/markdown",
                path.read_bytes(),
            )
            version_ids.append(accepted["document_version_id"])
            await pipeline.run(accepted["job_id"])

        await wiki.update_config(
            knowledge_base_id, enabled=True, token_limit=2_000_000
        )
        async with database.session_factory() as session:
            jobs = list(
                await session.scalars(
                    select(WikiJob).where(
                        WikiJob.knowledge_base_id == knowledge_base_id
                    )
                )
            )
        assert len(jobs) == 8
        for job in jobs:
            await processor.run(job.id)

        page_recalls: list[float] = []
        claim_recalls: list[float] = []
        citation_checks: list[bool] = []
        consistency_scores: list[float] = []
        false_matches = 0
        unanswerable_queries = 0
        for case in payload["cases"]:
            expected_documents = set(case["expected_documents"])
            expected_terms = list(case["expected_terms"])
            relevant_sets: list[set[str]] = []
            for query in case["queries"]:
                result = await retrieval.search(
                    query,
                    (knowledge_base_id,),
                    mode="hybrid",
                    page_limit=5,
                    claim_limit=8,
                )
                returned_claim_ids.update(
                    claim.id
                    for page in result.pages
                    for claim in page.matched_claims
                )
                rendered = "\n".join(
                    claim.text
                    for page in result.pages
                    for claim in page.matched_claims
                )
                assert not any(
                    _contains(rendered, term) for term in case["forbidden_terms"]
                )
                if not case["answerable"]:
                    unanswerable_queries += 1
                    false_matches += int(bool(result.pages))
                    continue

                documents = _result_documents(result)
                relevant = documents & expected_documents
                relevant_sets.append(relevant)
                page_recalls.append(len(relevant) / len(expected_documents))
                claim_recalls.append(
                    sum(_contains(rendered, term) for term in expected_terms)
                    / len(expected_terms)
                )
                citations = _relevant_citations(result, expected_terms)
                citation_checks.extend(
                    filename in expected_documents for filename in citations
                )
            if case["answerable"]:
                consistency_scores.append(_pairwise_consistency(relevant_sets))

        needs_review_leaks, invalid_source_leaks = await _count_source_leaks(
            database, returned_claim_ids
        )
        page_recall_at_5 = sum(page_recalls) / len(page_recalls)
        claim_recall_at_8 = sum(claim_recalls) / len(claim_recalls)
        citation_precision = (
            sum(citation_checks) / len(citation_checks) if citation_checks else 0.0
        )
        synonym_result_consistency = sum(consistency_scores) / len(consistency_scores)
        no_answer_false_match_rate = false_matches / unanswerable_queries
        metrics: dict[str, float | int | str] = {
            "page_recall_at_5": page_recall_at_5,
            "claim_recall_at_8": claim_recall_at_8,
            "citation_precision": citation_precision,
            "synonym_result_consistency": synonym_result_consistency,
            "no_answer_false_match_rate": no_answer_false_match_rate,
            "needs_review_leaks": needs_review_leaks,
            "invalid_source_leaks": invalid_source_leaks,
            "generator": "deterministic_grounded",
        }
        print("WIKI_EVAL " + json.dumps(metrics, ensure_ascii=False, sort_keys=True))
        assert page_recall_at_5 >= 0.90
        assert claim_recall_at_8 >= 0.85
        assert citation_precision >= 0.95
        assert synonym_result_consistency >= 0.85
        assert no_answer_false_match_rate <= 0.05
        assert needs_review_leaks == 0
        assert invalid_source_leaks == 0
    finally:
        for version_id in version_ids:
            await index.delete_version(version_id)
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(database, knowledge_base_id)
        milvus.close()
        await database.close()
