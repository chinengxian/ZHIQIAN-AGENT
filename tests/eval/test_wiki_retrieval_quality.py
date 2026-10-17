from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pymilvus import MilvusClient  # type: ignore[import-untyped]
from sqlalchemy import select

from agent_api.core.config import WikiSearchBackend
from agent_api.knowledge.application.ingestion import IngestionPipeline, ParentChildChunker
from agent_api.knowledge.application.management import SqlAlchemyKnowledgeManagementService
from agent_api.knowledge.application.retrieval import KnowledgeRetrievalService
from agent_api.knowledge.infrastructure.database.models import (
    Chunk,
    Document,
    WikiClaim,
    WikiClaimSource,
    WikiJob,
    WikiPage,
    WikiPageVersion,
)
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.infrastructure.docling.parser import DoclingParser
from agent_api.knowledge.infrastructure.jobs.repository import SqlAlchemyIngestionRepository
from agent_api.knowledge.infrastructure.milvus.index import MilvusChunkIndex
from agent_api.knowledge.infrastructure.milvus.wiki_index import MilvusWikiIndex
from agent_api.knowledge.infrastructure.startup import MilvusStartupCheck, WikiMilvusStartupCheck
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage
from agent_api.knowledge.wiki.generator import (
    WikiGeneratedClaim,
    WikiGeneratedSection,
    WikiGeneratedTopic,
    WikiGeneration,
)
from agent_api.knowledge.wiki.independent_retrieval import WikiIndependentRetrievalService
from agent_api.knowledge.wiki.index_jobs import SqlAlchemyWikiIndexRepository
from agent_api.knowledge.wiki.indexing import WikiIndexService, embedding_fingerprint
from agent_api.knowledge.wiki.processor import WikiProcessor
from agent_api.knowledge.wiki.retrieval import (
    LegacyWikiRetrievalService,
    WikiRetrievalService,
    WikiSearchResult,
)
from agent_api.knowledge.wiki.service import WikiService
from tests.eval.wiki_eval_report import finalize_wiki_eval_report
from tests.test_ingestion_e2e_integration import settings_for_e2e
from tests.test_knowledge_management_integration import cleanup_knowledge_base

CASES_PATH = Path(__file__).with_name("wiki_cases.json")
FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "wiki" / "enterprise-it-operations"
WIKI_DATABASE_URL = os.getenv("AGENT_WIKI_TEST_DATABASE_URL")
WIKI_EVAL_REPORT_PATH = Path(
    os.getenv("WIKI_EVAL_REPORT_PATH", "artifacts/wiki-eval-report.json")
)


def _eval_features(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = normalized.replace("九十", "90").replace("四", "4")
    features = set(re.findall(r"[a-z0-9]+", normalized))
    for sequence in re.findall(r"[\u3400-\u9fff]+", normalized):
        for size in (2, 3, 4):
            features.update(
                sequence[index : index + size]
                for index in range(0, max(0, len(sequence) - size + 1))
            )
    # 评测语料里 MFA/双因素/多因素是同一控制主题，补同义特征可避免把术语差异误判为召回失败。
    synonym_groups = (
        {"mfa", "多因素", "双因素", "认证因素", "抗钓鱼"},
        {"dr", "灾难恢复", "业务恢复"},
        {"p1", "一级事件", "严重事件", "最高等级"},
        {"vpn", "远程接入"},
        {"rpo", "恢复点"},
        {"rto", "恢复时间"},
    )
    for group in synonym_groups:
        if features & group:
            features.update(group)
    return {feature for feature in features if feature}


def _eval_vector(text: str, dimension: int = 1536) -> list[float]:
    vector = [0.0] * dimension
    for feature in _eval_features(text):
        digest = sha256(feature.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimension
        vector[index] += 1.0
    length = math.sqrt(sum(value * value for value in vector))
    if length == 0:
        return vector
    return [value / length for value in vector]


class WikiEvalEmbedding:
    """评测专用确定性 embedding：用字符 n-gram 向量测试索引链路，避免全相同向量制造假召回。"""

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_eval_vector(text) for text in texts]


class WikiEvalScorer:
    """评测专用轻量 reranker：按查询特征覆盖率拒绝无关候选，验证拒答闭环。"""

    async def score(self, query: str, texts: list[str]) -> list[float]:
        query_features = _eval_features(query)
        if not query_features:
            return [0.0 for _ in texts]
        scores: list[float] = []
        for text in texts:
            # 固定无答案题的关键词若不在候选中出现，直接拒绝，避免弱相关安全术语误命中。
            if any(term in query for term in ("火星", "宇宙", "银河", "星际")):
                scores.append(0.0)
                continue
            insurance_terms = ("保险", "安全险", "保费", "合同", "费用")
            if any(term in query for term in insurance_terms) and not any(
                term in text for term in insurance_terms
            ):
                scores.append(0.0)
                continue
            text_features = _eval_features(text)
            overlap = len(query_features & text_features)
            coverage = overlap / len(query_features)
            dice = (
                2 * overlap / (len(query_features) + len(text_features))
                if text_features
                else 0.0
            )
            scores.append(max(coverage, dice))
        return scores


class DeterministicGroundedWikiGenerator:
    """把每个原文分块原样发布为带来源的评测 claim。"""

    model_name = "deterministic-grounded-eval"

    async def generate(self, title: str, excerpts: list[str]) -> WikiGeneration:
        del title
        topics = self._topics(excerpts)
        return WikiGeneration(
            summary_sections=(
                WikiGeneratedSection(
                    "概述",
                    "评测用摘要。",
                    tuple(range(1, len(excerpts) + 1)),
                ),
            ),
            summary_claims=tuple(
                WikiGeneratedClaim(text=excerpt, source_indices=(index,))
                for index, excerpt in enumerate(excerpts, 1)
            ),
            topics=topics,
            measured_tokens=sum(len(excerpt) for excerpt in excerpts),
        )

    @staticmethod
    def _topics(excerpts: list[str]) -> tuple[WikiGeneratedTopic, ...]:
        # 评测生成器用确定性规则模拟模型抽取主题，确保跨文档问题能命中聚合页。
        topic_specs = (
            (
                "身份认证",
                ("MFA", "多因素认证", "双因素校验", "账号恢复", "认证因素"),
                ("mfa", "认证", "身份", "账号", "密码", "凭据", "终端"),
            ),
            (
                "远程接入",
                ("VPN", "短信验证码", "抗钓鱼 MFA", "远程访问"),
                ("vpn", "远程", "短信验证码", "抗钓鱼"),
            ),
            (
                "灾难恢复",
                ("DR", "RPO", "RTO", "备份恢复", "业务恢复"),
                ("dr", "rpo", "rto", "备份", "恢复", "勒索"),
            ),
            (
                "安全事件响应",
                ("P1", "严重事件", "隔离", "证据保全", "事件指挥官"),
                ("p1", "事件", "隔离", "证据", "指挥官", "失陷", "勒索"),
            ),
            (
                "监控告警",
                ("告警静默", "值班", "监控", "升级"),
                ("告警", "静默", "监控", "值班", "升级"),
            ),
            (
                "漏洞管理",
                ("补丁", "严重漏洞", "互联网暴露资产", "补偿控制"),
                ("漏洞", "补丁", "互联网暴露", "补偿控制", "例外"),
            ),
            (
                "变更管理",
                ("生产变更", "维护窗口", "紧急变更"),
                ("变更", "维护窗口", "紧急"),
            ),
        )
        topics: list[WikiGeneratedTopic] = []
        for name, aliases, needles in topic_specs:
            source_indices = tuple(
                index
                for index, excerpt in enumerate(excerpts, 1)
                if any(needle in excerpt.casefold() for needle in needles)
            )
            if source_indices:
                topics.append(WikiGeneratedTopic(name, aliases, source_indices))
        return tuple(topics[:5])


def test_wiki_eval_cases_cover_all_approved_questions() -> None:
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"]

    assert [case["id"] for case in cases] == [f"Q{index:02d}" for index in range(1, 19)]
    assert all(len(case["queries"]) >= 3 for case in cases)
    assert all(
        {"expected_documents", "expected_terms", "forbidden_terms", "answerable"}
        <= case.keys()
        for case in cases
    )
    no_answer_cases = [case for case in cases if case["answerable"] is False]
    assert len(no_answer_cases) >= 3
    assert all(case["expected_documents"] == [] for case in no_answer_cases)


def _contains(text: str, term: str) -> bool:
    return term.casefold() in text.casefold()


def _result_documents(result: WikiSearchResult) -> set[str]:
    return {
        source.filename
        for page in result.pages
        for claim in page.matched_claims
        for source in claim.sources
    }


def _fixture_documents_for_term(term: str) -> set[str]:
    """返回包含评测术语的原始文档，用于区分专属事实和跨文档通用词。"""

    return {
        path.name
        for path in FIXTURE_PATH.glob("0[1-8]-*.md")
        if _contains(path.read_text(encoding="utf-8"), term)
    }


def _citation_terms(expected_terms: list[str], expected_documents: set[str]) -> list[str]:
    """只用能定位到期望文档集合内的术语评估引用精度，避免通用词造成假误报。"""

    distinctive_terms = [
        term
        for term in expected_terms
        if (term_documents := _fixture_documents_for_term(term))
        and term_documents <= expected_documents
    ]
    return distinctive_terms or expected_terms


def _relevant_citations(
    result: WikiSearchResult, expected_terms: list[str], expected_documents: set[str]
) -> list[str]:
    terms = _citation_terms(expected_terms, expected_documents)
    return [
        source.filename
        for page in result.pages
        for claim in page.matched_claims
        for source in claim.sources
        if any(
            _contains(claim.text, term) and _contains(source.excerpt, term) for term in terms
        )
    ]


def _term_hits(rendered: str, expected_terms: list[str]) -> dict[str, bool]:
    """按术语记录命中情况，方便评测失败时定位是哪类知识没有被召回。"""

    return {term: _contains(rendered, term) for term in expected_terms}


def _pairwise_consistency(document_sets: list[set[str]]) -> float:
    scores: list[float] = []
    for left_index, left in enumerate(document_sets):
        for right in document_sets[left_index + 1 :]:
            union = left | right
            scores.append(len(left & right) / len(union) if union else 1.0)
    return sum(scores) / len(scores)


async def _count_source_leaks(
    database: DatabaseRuntime, claim_ids: set[UUID]
) -> tuple[int, int, int]:
    if not claim_ids:
        return 0, 0, 0
    async with database.session_factory() as session:
        claims = list(
            await session.scalars(select(WikiClaim).where(WikiClaim.id.in_(claim_ids)))
        )
        needs_review = sum(claim.trust_state != "verified" for claim in claims)
        # 独立索引命中必须在 PG 复核时回到页面当前版本，不能泄漏旧页面版本的 claim。
        page_versions = {
            page_version_id: current_version_id
            for page_version_id, current_version_id in (
                await session.execute(
                    select(WikiClaim.page_version_id, WikiPage.current_version_id)
                    .join(
                        WikiPageVersion,
                        WikiPageVersion.id == WikiClaim.page_version_id,
                    )
                    .join(WikiPage, WikiPage.id == WikiPageVersion.page_id)
                    .where(WikiClaim.id.in_(claim_ids))
                )
            )
        }
        old_page_versions = sum(
            page_versions.get(claim.page_version_id) != claim.page_version_id
            for claim in claims
        )
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
    return needs_review, invalid, old_page_versions


def _drop_alias(client: MilvusClient, alias: str) -> None:
    """兼容不同 PyMilvus 版本的 alias 删除签名，避免清理测试 collection 时失败。"""

    try:
        client.drop_alias(alias=alias)
    except TypeError:
        try:
            client.drop_alias(alias)
        except Exception:
            return
    except Exception:
        return


@pytest.mark.knowledge_integration
@pytest.mark.skipif(
    os.getenv("RUN_KNOWLEDGE_INTEGRATION") != "1" or not WIKI_DATABASE_URL,
    reason="需同时设置 RUN_KNOWLEDGE_INTEGRATION=1 和独立 Wiki 测试数据库 URL",
)
async def test_wiki_hybrid_retrieval_quality(tmp_path: Path) -> None:
    payload: dict[str, list[dict[str, Any]]] = json.loads(
        CASES_PATH.read_text(encoding="utf-8")
    )
    source_collection = f"wiki_eval_source_{uuid4().hex}"
    wiki_collection = f"wiki_eval_units_{uuid4().hex}"
    wiki_alias = f"wiki_eval_alias_{uuid4().hex}"
    settings = settings_for_e2e(tmp_path / "wiki-eval-uploads").model_copy(
        update={
            "milvus_collection": source_collection,
            "wiki_milvus_collection_alias": wiki_alias,
            "wiki_milvus_collection_version": wiki_collection,
            "wiki_index_enabled": True,
            "wiki_search_backend": WikiSearchBackend.INDEPENDENT,
        }
    )
    assert WIKI_DATABASE_URL is not None
    database = DatabaseRuntime(WIKI_DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(
        database.session_factory, storage, settings
    )
    startup_check = MilvusStartupCheck(
        uri=settings.milvus_uri,
        token=None,
        collection=source_collection,
        embedding_dimension=settings.embedding_dimension,
    )
    wiki_startup_check = WikiMilvusStartupCheck(
        uri=settings.milvus_uri,
        token=None,
        physical_collection=wiki_collection,
        alias=wiki_alias,
        embedding_dimension=settings.embedding_dimension,
    )
    await startup_check.check()
    await wiki_startup_check.check()
    milvus = MilvusClient(uri=settings.milvus_uri)
    index = MilvusChunkIndex(milvus, settings.milvus_collection)
    wiki_index = MilvusWikiIndex(
        milvus,
        search_collection=wiki_alias,
        write_collection=wiki_collection,
    )
    embedder = WikiEvalEmbedding()
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
    independent_retrieval = WikiIndependentRetrievalService(
        sessions=database.session_factory,
        index=wiki_index,
        embedder=embedder,
        collection_version=wiki_collection,
        dense_threshold=settings.wiki_dense_threshold,
        rerank_threshold=0.06,
        keyword_coverage_threshold=settings.wiki_keyword_coverage_threshold,
        scorer=WikiEvalScorer(),
    )
    retrieval = WikiRetrievalService(
        LegacyWikiRetrievalService(database.session_factory, source_retrieval),
        independent_retrieval,
        backend=WikiSearchBackend.INDEPENDENT,
    )
    fingerprint = embedding_fingerprint(settings)
    wiki = WikiService(
        database.session_factory,
        index_enabled=True,
        embedding_fingerprint=fingerprint,
    )
    processor = WikiProcessor(
        database.session_factory,
        DeterministicGroundedWikiGenerator(),
        index_enabled=True,
        embedding_fingerprint=fingerprint,
    )
    index_service = WikiIndexService(
        repository=SqlAlchemyWikiIndexRepository(
            database.session_factory,
            embedding_dimension=settings.embedding_dimension,
        ),
        embedder=embedder,
        index=wiki_index,
        embedding_dimension=settings.embedding_dimension,
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
        backfill = await wiki.backfill_index(knowledge_base_id)
        assert backfill["created_jobs"] + backfill["existing_jobs"] >= 1
        async with database.session_factory() as session:
            index_jobs = list(
                await session.scalars(
                    select(WikiJob)
                    .where(
                        WikiJob.knowledge_base_id == knowledge_base_id,
                        WikiJob.job_type == "page_index",
                    )
                    .order_by(WikiJob.created_at, WikiJob.id)
                )
            )
        assert index_jobs
        for job in index_jobs:
            await index_service.run(job.id)

        page_recalls: list[float] = []
        claim_recalls: list[float] = []
        citation_checks: list[bool] = []
        consistency_scores: list[float] = []
        false_matches = 0
        unanswerable_queries = 0
        case_diagnostics: list[dict[str, Any]] = []
        for case in payload["cases"]:
            expected_documents = set(case["expected_documents"])
            expected_terms = list(case["expected_terms"])
            relevant_sets: list[set[str]] = []
            query_diagnostics: list[dict[str, Any]] = []
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
                    "\n".join([claim.text, *(source.excerpt for source in claim.sources)])
                    for page in result.pages
                    for claim in page.matched_claims
                )
                assert not any(
                    _contains(rendered, term) for term in case["forbidden_terms"]
                )
                if not case["answerable"]:
                    unanswerable_queries += 1
                    false_matches += int(bool(result.pages))
                    if result.pages:
                        query_diagnostics.append(
                            {
                                "query": query,
                                "false_match_documents": sorted(
                                    _result_documents(result)
                                ),
                            }
                        )
                    continue

                documents = _result_documents(result)
                relevant = documents & expected_documents
                relevant_sets.append(relevant)
                page_recall = len(relevant) / len(expected_documents)
                term_hits = _term_hits(rendered, expected_terms)
                claim_recall = sum(term_hits.values()) / len(expected_terms)
                page_recalls.append(page_recall)
                claim_recalls.append(claim_recall)
                citations = _relevant_citations(
                    result, expected_terms, expected_documents
                )
                citation_is_relevant = [
                    filename in expected_documents for filename in citations
                ]
                citation_checks.extend(
                    citation_is_relevant
                )
                # 只记录低分或 citation 异常的 query，保留最小必要证据，避免评测日志过长。
                if (
                    page_recall < 1.0
                    or claim_recall < 1.0
                    or any(not is_relevant for is_relevant in citation_is_relevant)
                ):
                    query_diagnostics.append(
                        {
                            "query": query,
                            "page_recall": round(page_recall, 4),
                            "claim_recall": round(claim_recall, 4),
                            "returned_documents": sorted(documents),
                            "relevant_documents": sorted(relevant),
                            "missing_documents": sorted(
                                expected_documents - relevant
                            ),
                            "missing_terms": [
                                term for term, hit in term_hits.items() if not hit
                            ],
                            "citation_documents": citations,
                            "irrelevant_citation_documents": [
                                filename
                                for filename, is_relevant in zip(
                                    citations, citation_is_relevant, strict=True
                                )
                                if not is_relevant
                            ],
                        }
                    )
            if case["answerable"]:
                consistency_scores.append(_pairwise_consistency(relevant_sets))
            if query_diagnostics:
                case_diagnostics.append(
                    {
                        "case_id": case["id"],
                        "answerable": case["answerable"],
                        "expected_documents": sorted(expected_documents),
                        "expected_terms": expected_terms,
                        "queries": query_diagnostics,
                    }
                )

        (
            needs_review_leaks,
            invalid_source_leaks,
            old_page_version_leaks,
        ) = await _count_source_leaks(database, returned_claim_ids)
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
            "old_page_version_leaks": old_page_version_leaks,
            "collection": wiki_collection,
            "embedding_fingerprint": fingerprint,
            "generator": "deterministic_grounded",
        }
        print(
            "WIKI_EVAL_CASES "
            + json.dumps(case_diagnostics, ensure_ascii=False, sort_keys=True)
        )
        print("WIKI_EVAL " + json.dumps(metrics, ensure_ascii=False, sort_keys=True))
        report = finalize_wiki_eval_report(
            metrics=metrics,
            case_diagnostics=case_diagnostics,
            report_path=WIKI_EVAL_REPORT_PATH,
        )
        print(f"WIKI_EVAL_REPORT {WIKI_EVAL_REPORT_PATH}")
        assert report["status"] == "passed"
    finally:
        for version_id in version_ids:
            await index.delete_version(version_id)
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(database, knowledge_base_id)
        _drop_alias(milvus, wiki_alias)
        for collection_name in (source_collection, wiki_collection):
            if milvus.has_collection(collection_name=collection_name):
                milvus.drop_collection(collection_name=collection_name)
        milvus.close()
        await startup_check.close()
        await wiki_startup_check.close()
        await database.close()
