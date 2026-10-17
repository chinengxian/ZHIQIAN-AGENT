from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from agent_api.knowledge.application.management import DEFAULT_WORKSPACE_ID
from agent_api.knowledge.infrastructure.database.models import (
    Chunk,
    Document,
    DocumentVersion,
    KnowledgeBase,
    WikiClaim,
    WikiClaimSource,
    WikiJob,
    WikiPage,
    WikiPageVersion,
)
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.infrastructure.milvus.wiki_index import (
    WikiIndexedUnit,
    WikiIndexHit,
)
from agent_api.knowledge.wiki.independent_retrieval import WikiIndependentRetrievalService
from agent_api.knowledge.wiki.index_jobs import SqlAlchemyWikiIndexRepository
from agent_api.knowledge.wiki.indexing import WikiIndexService
from agent_api.knowledge.wiki.service import WikiService
from tests.test_knowledge_management_integration import cleanup_knowledge_base

pytestmark = pytest.mark.knowledge_integration

FINGERPRINT = "f" * 64


class DeterministicWikiEmbedder:
    """集成测试只验证索引闭环，向量固定以避免外部模型依赖。"""

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class RecordingWikiIndex:
    """记录 WikiIndexService 对索引的真实写入，并为独立检索返回同一批单元。"""

    def __init__(self, *, count_override: int | None = None) -> None:
        self.count_override = count_override
        self.units: list[WikiIndexedUnit] = []
        self.deleted_versions: list[UUID] = []

    async def replace_page_version(
        self,
        page_version_id: UUID,
        units: list[WikiIndexedUnit] | tuple[WikiIndexedUnit, ...],
    ) -> None:
        assert all(unit.page_version_id == page_version_id for unit in units)
        self.units = list(units)

    async def count_page_version(self, page_version_id: UUID) -> int:
        if self.count_override is not None:
            return self.count_override
        return sum(unit.page_version_id == page_version_id for unit in self.units)

    async def delete_page_version(self, page_version_id: UUID) -> None:
        self.deleted_versions.append(page_version_id)
        self.units = [unit for unit in self.units if unit.page_version_id != page_version_id]

    async def search_dense(
        self,
        vector: list[float],
        knowledge_base_ids: tuple[UUID, ...],
        limit: int,
    ) -> list[WikiIndexHit]:
        del vector
        return self._hits(knowledge_base_ids, limit, "dense")

    async def search_keyword(
        self,
        query: str,
        knowledge_base_ids: tuple[UUID, ...],
        limit: int,
    ) -> list[WikiIndexHit]:
        del query
        return self._hits(knowledge_base_ids, limit, "keyword")

    def _hits(
        self,
        knowledge_base_ids: tuple[UUID, ...],
        limit: int,
        channel: str,
    ) -> list[WikiIndexHit]:
        return [
            WikiIndexHit(
                unit_id=unit.unit_id,
                page_id=unit.page_id,
                page_version_id=unit.page_version_id,
                claim_id=unit.claim_id,
                unit_type=unit.unit_type,
                content=unit.content,
                score=0.95,
                channel=channel,  # type: ignore[arg-type]
            )
            for unit in self.units
            if unit.knowledge_base_id in knowledge_base_ids
        ][:limit]


@dataclass(frozen=True, slots=True)
class WikiIndexFixture:
    knowledge_base_id: UUID
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    page_id: UUID
    page_version_id: UUID
    claim_id: UUID


def _database() -> DatabaseRuntime:
    url = os.environ.get("AGENT_WIKI_TEST_DATABASE_URL")
    if not url or os.getenv("RUN_KNOWLEDGE_INTEGRATION") != "1":
        pytest.skip("需设置 RUN_KNOWLEDGE_INTEGRATION=1 和 AGENT_WIKI_TEST_DATABASE_URL")
    return DatabaseRuntime(url)


async def _seed_indexable_page(
    database: DatabaseRuntime,
    *,
    origin: str = "pipeline",
) -> WikiIndexFixture:
    knowledge_base_id = uuid4()
    document_id = uuid4()
    document_version_id = uuid4()
    page_id = uuid4()
    page_version_id = uuid4()
    claim_id = uuid4()
    async with database.session_factory() as session, session.begin():
        session.add(
            KnowledgeBase(
                id=knowledge_base_id,
                workspace_id=DEFAULT_WORKSPACE_ID,
                name=f"Wiki 独立索引集成 {knowledge_base_id}",
                enabled=True,
            )
        )
        await session.flush()
        document = Document(
            id=document_id,
            knowledge_base_id=knowledge_base_id,
            title="身份规范",
            filename="identity.md",
            mime_type="text/markdown",
            status="ready",
        )
        session.add(document)
        await session.flush()
        session.add(
            DocumentVersion(
                id=document_version_id,
                document_id=document_id,
                version_no=1,
                file_path="identity.md",
                sha256="0" * 64,
                file_size=128,
                pipeline_version="test",
                embedding_provider="test",
                embedding_model="test",
                embedding_dimension=3,
                embedding_fingerprint="test",
                status="ready",
            )
        )
        await session.flush()
        document.active_version_id = document_version_id
        chunk = Chunk(
            document_version_id=document_version_id,
            ordinal=0,
            kind="child",
            content="员工登录必须启用 MFA，并使用唯一身份。",
            token_count=20,
        )
        session.add(chunk)
        await session.flush()
        page = WikiPage(
            id=page_id,
            knowledge_base_id=knowledge_base_id,
            slug="summary/identity",
            page_type="summary",
            title="身份规范",
            current_version_id=None,
        )
        session.add(page)
        await session.flush()
        version = WikiPageVersion(
            id=page_version_id,
            page_id=page_id,
            version_no=1,
            title="身份规范",
            content="身份规范摘要。",
            summary="身份规范摘要。",
            origin=origin,
            review_state="published",
            index_state="not_required",
        )
        session.add(version)
        await session.flush()
        page.current_version_id = page_version_id
        claim = WikiClaim(
            id=claim_id,
            page_version_id=page_version_id,
            claim_key="identity:mfa",
            text="员工登录必须启用 MFA，并使用唯一身份。",
            trust_state="verified",
            position=0,
            claim_type="summary_fact",
        )
        session.add(claim)
        await session.flush()
        session.add(
            WikiClaimSource(
                claim_id=claim_id,
                document_id=document_id,
                document_version_id=document_version_id,
                chunk_id=chunk.id,
                chunk_kind=chunk.kind,
                chunk_ordinal=chunk.ordinal,
                content_sha256=sha256(chunk.content.encode()).hexdigest(),
                document_title=document.title,
                heading_path=["身份认证"],
                page_start=None,
                page_end=None,
            )
        )
    await WikiService(
        database.session_factory,
        index_enabled=True,
        embedding_fingerprint=FINGERPRINT,
    ).update_config(knowledge_base_id, enabled=True, token_limit=10_000)
    return WikiIndexFixture(
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        document_version_id=document_version_id,
        chunk_id=chunk.id,
        page_id=page_id,
        page_version_id=page_version_id,
        claim_id=claim_id,
    )


async def _run_first_index_job(
    database: DatabaseRuntime,
    knowledge_base_id: UUID,
    index: RecordingWikiIndex,
) -> WikiJob:
    async with database.session_factory() as session:
        job = await session.scalar(
            select(WikiJob)
            .where(
                WikiJob.knowledge_base_id == knowledge_base_id,
                WikiJob.job_type == "page_index",
            )
            .order_by(WikiJob.created_at, WikiJob.id)
        )
        assert job is not None
        job_id = job.id
    service = WikiIndexService(
        repository=SqlAlchemyWikiIndexRepository(
            database.session_factory,
            embedding_dimension=3,
        ),
        embedder=DeterministicWikiEmbedder(),
        index=index,
        embedding_dimension=3,
    )
    await service.run(job_id)
    async with database.session_factory() as session:
        refreshed = await session.get(WikiJob, job_id)
        assert refreshed is not None
        return refreshed


async def test_independent_index_backfill_ready_and_recheck_rejects_stale_source() -> None:
    database = _database()
    fixture: WikiIndexFixture | None = None
    try:
        fixture = await _seed_indexable_page(database)
        wiki = WikiService(
            database.session_factory,
            index_enabled=True,
            embedding_fingerprint=FINGERPRINT,
        )
        assert await wiki.backfill_index(fixture.knowledge_base_id) == {
            "created_jobs": 1,
            "existing_jobs": 0,
            "skipped_versions": 0,
        }
        index = RecordingWikiIndex()
        job = await _run_first_index_job(database, fixture.knowledge_base_id, index)
        assert job.status == "succeeded"
        assert index.units and index.units[0].claim_id == fixture.claim_id
        async with database.session_factory() as session:
            version = await session.get(WikiPageVersion, fixture.page_version_id)
            assert version is not None
            assert version.index_state == "ready"

        retrieval = WikiIndependentRetrievalService(
            index=index,
            embedder=DeterministicWikiEmbedder(),
            collection_version="test-units",
            dense_threshold=0.5,
            rerank_threshold=0.5,
            keyword_coverage_threshold=0.1,
            sessions=database.session_factory,
        )
        found = await retrieval.search("MFA 登录", (fixture.knowledge_base_id,))
        assert found.pages
        assert found.pages[0].matched_claims[0].id == fixture.claim_id

        next_version_id = uuid4()
        async with database.session_factory() as session, session.begin():
            document = await session.get(Document, fixture.document_id)
            assert document is not None
            session.add(
                DocumentVersion(
                    id=next_version_id,
                    document_id=fixture.document_id,
                    version_no=2,
                    file_path="identity-v2.md",
                    sha256="1" * 64,
                    file_size=128,
                    pipeline_version="test",
                    embedding_provider="test",
                    embedding_model="test",
                    embedding_dimension=3,
                    embedding_fingerprint="test",
                    status="ready",
                )
            )
            await session.flush()
            document.active_version_id = next_version_id
            session.add(
                Chunk(
                    document_version_id=next_version_id,
                    ordinal=0,
                    kind="child",
                    content="新版身份规范要求使用抗钓鱼 MFA。",
                    token_count=20,
                )
            )
        stale = await retrieval.search("MFA 登录", (fixture.knowledge_base_id,))
        assert stale.no_answer is True
        assert stale.pages == ()
    finally:
        if fixture is not None:
            await cleanup_knowledge_base(database, fixture.knowledge_base_id)
        await database.close()


async def test_index_count_mismatch_fails_version_and_keeps_retry_state() -> None:
    database = _database()
    fixture: WikiIndexFixture | None = None
    try:
        fixture = await _seed_indexable_page(database)
        wiki = WikiService(
            database.session_factory,
            index_enabled=True,
            embedding_fingerprint=FINGERPRINT,
        )
        await wiki.backfill_index(fixture.knowledge_base_id)
        job = await _run_first_index_job(
            database,
            fixture.knowledge_base_id,
            RecordingWikiIndex(count_override=0),
        )
        assert job.status == "retry_wait"
        assert job.error_code == "index_count_mismatch"
        async with database.session_factory() as session:
            version = await session.get(WikiPageVersion, fixture.page_version_id)
            assert version is not None
            assert version.index_state == "failed"
            assert version.index_error_code == "index_count_mismatch"
    finally:
        if fixture is not None:
            await cleanup_knowledge_base(database, fixture.knowledge_base_id)
        await database.close()


async def test_user_origin_current_page_is_not_backfilled_into_independent_index() -> None:
    database = _database()
    fixture: WikiIndexFixture | None = None
    try:
        fixture = await _seed_indexable_page(database, origin="user")
        wiki = WikiService(
            database.session_factory,
            index_enabled=True,
            embedding_fingerprint=FINGERPRINT,
        )
        assert await wiki.backfill_index(fixture.knowledge_base_id) == {
            "created_jobs": 0,
            "existing_jobs": 0,
            "skipped_versions": 1,
        }
        async with database.session_factory() as session:
            version = await session.get(WikiPageVersion, fixture.page_version_id)
            assert version is not None
            assert version.index_state == "not_required"
            job_count = len(
                list(
                    await session.scalars(
                        select(WikiJob).where(
                            WikiJob.knowledge_base_id == fixture.knowledge_base_id,
                            WikiJob.job_type == "page_index",
                        )
                    )
                )
            )
            assert job_count == 0
    finally:
        if fixture is not None:
            await cleanup_knowledge_base(database, fixture.knowledge_base_id)
        await database.close()
