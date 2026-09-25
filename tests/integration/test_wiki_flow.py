from __future__ import annotations

import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from agent_api.knowledge.application.management import DEFAULT_WORKSPACE_ID
from agent_api.knowledge.infrastructure.database.models import (
    Chunk,
    Document,
    DocumentVersion,
    KnowledgeBase,
    WikiJob,
)
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.wiki.api import router as wiki_router
from agent_api.knowledge.wiki.generator import WikiGeneration
from agent_api.knowledge.wiki.jobs import schedule_document_wiki
from agent_api.knowledge.wiki.processor import WikiProcessor
from agent_api.knowledge.wiki.service import WikiService


class FakeWikiGenerator:
    model_name = "fake-wiki"

    async def generate(self, title: str, excerpts: list[str]) -> WikiGeneration:
        assert excerpts
        return WikiGeneration(f"{title} 讨论共同主题。", ("共同主题",), 64)


@pytest.mark.knowledge_integration
async def test_wiki_generates_source_backed_pages_and_keeps_review_boundary() -> None:
    url = os.environ.get("AGENT_WIKI_TEST_DATABASE_URL")
    if not url:
        pytest.skip("需要独立 Wiki 测试数据库 URL")
    database = DatabaseRuntime(url)
    knowledge_base_id = uuid4()
    document_ids = [uuid4(), uuid4()]
    version_ids = [uuid4(), uuid4()]
    service = WikiService(database.session_factory)
    try:
        async with database.session_factory() as session, session.begin():
            session.add(
                KnowledgeBase(
                    id=knowledge_base_id,
                    workspace_id=DEFAULT_WORKSPACE_ID,
                    name=f"Wiki 集成测试 {knowledge_base_id}",
                    enabled=True,
                )
            )
            await session.flush()
            for index, (document_id, version_id) in enumerate(
                zip(document_ids, version_ids, strict=True)
            ):
                document = Document(
                    id=document_id,
                    knowledge_base_id=knowledge_base_id,
                    title=f"文档 {index + 1}",
                    filename=f"doc-{index + 1}.txt",
                    mime_type="text/plain",
                    status="ready",
                )
                session.add(document)
                await session.flush()
                session.add(
                    DocumentVersion(
                        id=version_id,
                        document_id=document_id,
                        version_no=1,
                        file_path=f"dummy-{index}.txt",
                        sha256="0" * 64,
                        file_size=20,
                        pipeline_version="test",
                        embedding_provider="test",
                        embedding_model="test",
                        embedding_dimension=3,
                        embedding_fingerprint="test",
                        status="ready",
                    )
                )
                await session.flush()
                document.active_version_id = version_id
                session.add(
                    Chunk(
                        document_version_id=version_id,
                        ordinal=0,
                        kind="child",
                        content=f"文档 {index + 1} 的共同主题原文证据。",
                        token_count=20,
                    )
                )
                if index == 1:
                    for ordinal in range(1, 14):
                        session.add(
                            Chunk(
                                document_version_id=version_id,
                                ordinal=ordinal,
                                kind="child",
                                content=f"文档尾部证据 {ordinal}。",
                                token_count=20,
                            )
                        )
        config = await service.update_config(knowledge_base_id, enabled=True, token_limit=100_000)
        assert config["enabled"] is True
        async with database.session_factory() as session:
            jobs = list(
                await session.scalars(
                    select(WikiJob).where(WikiJob.knowledge_base_id == knowledge_base_id)
                )
            )
        assert len(jobs) == 2
        processor = WikiProcessor(database.session_factory, FakeWikiGenerator())
        for job in jobs:
            await processor.run(job.id)
        pages = await service.list_pages(knowledge_base_id)
        assert [page["page_type"] for page in pages].count("summary") == 2
        assert [page["page_type"] for page in pages].count("topic") == 1
        assert [page["page_type"] for page in pages].count("index") == 1
        topic = next(page for page in pages if page["page_type"] == "topic")
        detail = await service.get_page(knowledge_base_id, topic["id"])
        assert detail["claims"]
        assert all(claim["trust_state"] == "verified" for claim in detail["claims"])
        assert any(claim["text"] == "文档尾部证据 13。" for claim in detail["claims"])
        summary = next(page for page in pages if page["slug"] == f"summary/{document_ids[0]}")
        edited = await service.edit_page(
            knowledge_base_id,
            summary["id"],
            base_version=summary["current_version_id"],
            title="人工标题",
            content="# 人工标题\n\n人工补充。",
        )
        assert edited["claims"][0]["trust_state"] == "needs_review"
        versions = await service.list_versions(knowledge_base_id, summary["id"])
        reverted = await service.revert_page(
            knowledge_base_id,
            summary["id"],
            base_version=edited["current_version_id"],
            target_version=versions[-1]["id"],
        )
        assert reverted["origin"] == "revert"
        next_version_id = uuid4()
        async with database.session_factory() as session, session.begin():
            document = await session.get(Document, document_ids[0])
            assert document is not None
            session.add(
                DocumentVersion(
                    id=next_version_id,
                    document_id=document_ids[0],
                    version_no=2,
                    file_path="dummy-new.txt",
                    sha256="1" * 64,
                    file_size=30,
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
                    content="新版的共同主题原文证据。",
                    token_count=20,
                )
            )
            await schedule_document_wiki(session, knowledge_base_id, next_version_id)
        async with database.session_factory() as session:
            next_job = await session.scalar(
                select(WikiJob).where(WikiJob.document_version_id == next_version_id)
            )
            assert next_job is not None
        await processor.run(next_job.id)
        still_published = await service.get_page(knowledge_base_id, summary["id"])
        assert still_published["current_version_id"] == reverted["current_version_id"]
        versions = await service.list_versions(knowledge_base_id, summary["id"])
        pending = next(item for item in versions if item["review_state"] == "pending_review")
        reviewed = await service.review_page(
            knowledge_base_id,
            summary["id"],
            candidate_version_id=pending["id"],
            base_version=reverted["current_version_id"],
            decision="publish",
        )
        assert reviewed["review_state"] == "published"
        assert (await service.get_page(knowledge_base_id, summary["id"]))[
            "current_version_id"
        ] == pending["id"]
        refreshed_topic = await service.get_page(knowledge_base_id, topic["id"])
        assert any(
            source["document_version_id"] == next_version_id
            for claim in refreshed_topic["claims"]
            for source in claim["sources"]
        )
        disabled = await service.update_config(knowledge_base_id, enabled=False, token_limit=None)
        assert disabled["enabled"] is False
        assert await service.get_page(knowledge_base_id, summary["id"])
        assert await service.agent_pages(knowledge_base_id, "共同主题") == []
        app = FastAPI()
        app.state.wiki_service = service
        app.include_router(wiki_router)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://wiki.test"
        ) as client:
            response = await client.get(
                f"/api/v1/knowledge-bases/{knowledge_base_id}/wiki/pages/{summary['id']}"
            )
            assert response.status_code == 200
            assert response.json()["current_version_id"] == str(pending["id"])
        async with database.session_factory() as session, session.begin():
            document = await session.get(Document, document_ids[0])
            assert document is not None
            document.status = "deleting"
        stale_topic = await service.get_page(knowledge_base_id, topic["id"])
        assert any(claim["trust_state"] == "needs_review" for claim in stale_topic["claims"])
        assert await service.agent_read_page(knowledge_base_id, topic["id"]) is None
        budget_version_id = uuid4()
        async with database.session_factory() as session, session.begin():
            document = await session.get(Document, document_ids[1])
            assert document is not None
            session.add(
                DocumentVersion(
                    id=budget_version_id,
                    document_id=document_ids[1],
                    version_no=2,
                    file_path="dummy-budget.txt",
                    sha256="2" * 64,
                    file_size=30,
                    pipeline_version="test",
                    embedding_provider="test",
                    embedding_model="test",
                    embedding_dimension=3,
                    embedding_fingerprint="test",
                    status="ready",
                )
            )
            await session.flush()
            document.active_version_id = budget_version_id
            session.add(
                Chunk(
                    document_version_id=budget_version_id,
                    ordinal=0,
                    kind="child",
                    content="额度测试的新证据。",
                    token_count=20,
                )
            )
        previous_usage = disabled["tokens_charged"]
        resumed = await service.update_config(
            knowledge_base_id, enabled=True, token_limit=previous_usage + 1
        )
        assert resumed["tokens_charged"] == previous_usage
        trusted_topic = await service.agent_read_page(knowledge_base_id, topic["id"])
        assert trusted_topic is not None
        assert all(
            all(source["valid"] for source in claim["sources"]) for claim in trusted_topic["claims"]
        )
        async with database.session_factory() as session:
            budget_job = await session.scalar(
                select(WikiJob).where(WikiJob.document_version_id == budget_version_id)
            )
            assert budget_job is not None
        await processor.run(budget_job.id)
        assert any(
            job["id"] == budget_job.id and job["status"] == "paused_budget"
            for job in await service.list_jobs(knowledge_base_id)
        )
        await service.update_config(
            knowledge_base_id, enabled=True, token_limit=previous_usage + 100_000
        )
        await processor.run(budget_job.id)
        assert any(
            job["id"] == budget_job.id and job["status"] == "succeeded"
            for job in await service.list_jobs(knowledge_base_id)
        )
        assert (await service.get_config(knowledge_base_id))["tokens_charged"] > previous_usage
    finally:
        async with database.session_factory() as session, session.begin():
            await session.execute(
                delete(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
            )
        await database.close()
