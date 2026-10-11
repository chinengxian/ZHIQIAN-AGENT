from __future__ import annotations

import json
import logging
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from agent_api.knowledge.application.citations import Source
from agent_api.knowledge.wiki.api import (
    WikiJobResponse,
    WikiPageResponse,
    WikiPageSummary,
    get_wiki_retrieval,
    router,
)
from agent_api.knowledge.wiki.cursor import WikiCursorError
from agent_api.knowledge.wiki.independent_retrieval import WikiIndependentRetrievalService
from agent_api.knowledge.wiki.retrieval import (
    WikiMatchedClaim,
    WikiMatchedPage,
    WikiMatchedPageSummary,
    WikiSearchResult,
)
from agent_api.knowledge.wiki.service import WikiError


class FakeWikiRetrieval:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.raise_cursor_error = False

    async def search(
        self,
        query: str,
        scope_ids: tuple[UUID, ...],
        *,
        mode: str,
        page_limit: int,
        claim_limit: int,
        cursor: str | None = None,
    ) -> WikiSearchResult:
        if self.raise_cursor_error:
            from agent_api.knowledge.wiki.cursor import WikiCursorError

            raise WikiCursorError("wiki_search_cursor_expired")
        self.calls.append(
            {
                "query": query,
                "scope_ids": scope_ids,
                "mode": mode,
                "page_limit": page_limit,
                "claim_limit": claim_limit,
                "cursor": cursor,
            }
        )
        source = Source(
            citation_id="",
            chunk_id=uuid4(),
            document_id=uuid4(),
            document_version_id=uuid4(),
            title="认证规范",
            filename="auth.md",
            page_start=1,
            page_end=1,
            heading_path=("认证",),
            excerpt="需要两个独立认证因素",
            score=0.9,
            rank=1,
            parent_chunk_id=uuid4(),
            focus_start=2,
            focus_end=12,
        )
        claim = WikiMatchedClaim(
            id=uuid4(),
            text="需要两个独立认证因素",
            position=9,
            claim_type="summary_fact",
            score=1.4,
            sources=(source,),
            also_in_pages=(
                WikiMatchedPageSummary(
                    id=uuid4(),
                    knowledge_base_id=scope_ids[0],
                    slug="topic/auth-copy",
                    title="认证镜像",
                    page_type="topic",
                    current_version_id=uuid4(),
                ),
            ),
        )
        page = WikiMatchedPage(
            id=uuid4(),
            knowledge_base_id=scope_ids[0],
            slug="topic/auth",
            title="身份认证",
            page_type="topic",
            score=0.8,
            matched_fields=("alias", "source_chunk"),
            matched_claims=(claim,),
            omitted_claim_count=2,
        )
        return WikiSearchResult(
            (page,),
            False,
            None,
            None,
            no_answer=False,
            next_cursor="cursor-2",
            collection_version="wiki_units_v1",
        )


class FakeWikiService:
    async def get_page(self, knowledge_base_id, page_id, *, claim_cursor=None, claim_limit=20):
        del knowledge_base_id, page_id, claim_cursor, claim_limit
        raise WikiCursorError("wiki_page_cursor_expired")

    async def rebuild_aggregation_pages(self, knowledge_base_id):
        return {
            "knowledge_base_id": knowledge_base_id,
            "topic_pages": 2,
            "summary_pages": 3,
        }

    async def retry_page_index(self, knowledge_base_id, page_id):
        del knowledge_base_id, page_id
        raise WikiError("wiki_index_retry_not_allowed", 409)

    async def backfill_index(self, knowledge_base_id):
        del knowledge_base_id
        raise WikiError("wiki_index_rebuild_in_progress", 409)


class UnusedWikiIndex:
    async def search_dense(self, vector, knowledge_base_ids, limit):
        raise AssertionError("expired cursor should be rejected before dense search")

    async def search_keyword(self, query, knowledge_base_ids, limit):
        raise AssertionError("expired cursor should be rejected before keyword search")


class UnusedWikiEmbedder:
    async def embed_documents(self, texts):
        raise AssertionError("expired cursor should be rejected before embedding")


class UnusedReadModel:
    async def search_metadata(self, query, scope_ids, limit):
        raise AssertionError("expired cursor should be rejected before metadata search")

    async def load_verified_claims(self, claim_ids, scope_ids, require_index_ready):
        raise AssertionError("expired cursor should be rejected before claim load")


def _independent_service() -> WikiIndependentRetrievalService:
    return WikiIndependentRetrievalService(
        index=UnusedWikiIndex(),
        embedder=UnusedWikiEmbedder(),
        read_model=UnusedReadModel(),
        collection_version="wiki_units_v1",
        dense_threshold=0.55,
        rerank_threshold=0.5,
        keyword_coverage_threshold=0.5,
    )


def _app(service: object) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_wiki_retrieval] = lambda: service
    return app


async def test_wiki_search_api_returns_ranked_claims_and_sources() -> None:
    base_id = uuid4()
    service = FakeWikiRetrieval()
    transport = httpx.ASGITransport(app=_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/knowledge-bases/{base_id}/wiki/search",
            params={
                "q": "双因素校验",
                "mode": "hybrid",
                "limit": 5,
                "claim_limit": 12,
                "cursor": "cursor-1",
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["pages"][0]["matched_fields"] == ["alias", "source_chunk"]
    assert payload["pages"][0]["matched_claims"][0]["position"] == 9
    source = payload["pages"][0]["matched_claims"][0]["sources"][0]
    assert payload["pages"][0]["matched_claims"][0]["also_in_pages"][0]["title"] == "认证镜像"
    assert source["chunk_id"]
    assert source["parent_chunk_id"]
    assert source["excerpt"] == "需要两个独立认证因素"
    assert source["focus_start"] == 2
    assert source["focus_end"] == 12
    assert source["invalid_reason"] is None
    assert payload["degraded"] is False
    assert payload["no_answer"] is False
    assert payload["next_cursor"] == "cursor-2"
    assert payload["collection_version"] == "wiki_units_v1"
    assert service.calls[0] == {
        "query": "双因素校验",
        "scope_ids": (base_id,),
        "mode": "hybrid",
        "page_limit": 5,
        "claim_limit": 12,
        "cursor": "cursor-1",
    }


async def test_wiki_search_api_maps_expired_cursor_to_conflict(
    caplog: pytest.LogCaptureFixture,
) -> None:
    base_id = uuid4()
    service = FakeWikiRetrieval()
    service.raise_cursor_error = True
    caplog.set_level(logging.INFO, logger="agent_api.knowledge")
    transport = httpx.ASGITransport(app=_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/knowledge-bases/{base_id}/wiki/search",
            params={"q": "认证", "cursor": "expired"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "wiki_search_cursor_expired"
    events = [json.loads(record.getMessage()) for record in caplog.records]
    search_event = next(event for event in events if event["event"] == "wiki_search")
    assert search_event["cursor_expired"] is True
    assert "认证" not in caplog.text


async def test_wiki_search_api_records_expired_independent_cursor_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    base_id = uuid4()
    caplog.set_level(logging.INFO, logger="agent_api.knowledge")
    transport = httpx.ASGITransport(app=_app(_independent_service()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/knowledge-bases/{base_id}/wiki/search",
            params={"q": "MFA", "cursor": "expired"},
        )

    assert response.status_code == 409
    events = [json.loads(record.getMessage()) for record in caplog.records]
    cursor_events = [
        event
        for event in events
        if event["event"] == "wiki_search" and event["cursor_expired"] is True
    ]
    assert len(cursor_events) == 1
    assert "MFA" not in caplog.text


async def test_wiki_page_api_records_expired_claim_cursor(
    caplog: pytest.LogCaptureFixture,
) -> None:
    base_id = uuid4()
    page_id = uuid4()
    app = _app(FakeWikiRetrieval())
    app.state.wiki_service = FakeWikiService()
    caplog.set_level(logging.INFO, logger="agent_api.knowledge")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/knowledge-bases/{base_id}/wiki/pages/{page_id}",
            params={"claim_cursor": "expired"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "wiki_page_cursor_expired"
    events = [json.loads(record.getMessage()) for record in caplog.records]
    cursor_event = next(event for event in events if event["event"] == "wiki_cursor_expired")
    assert cursor_event["cursor_type"] == "page_claim"


async def test_wiki_search_api_validates_options() -> None:
    base_id = uuid4()
    transport = httpx.ASGITransport(app=_app(FakeWikiRetrieval()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [
            await client.get(
                f"/api/v1/knowledge-bases/{base_id}/wiki/search",
                params={"q": "认证", "mode": "unknown"},
            ),
            await client.get(
                f"/api/v1/knowledge-bases/{base_id}/wiki/search",
                params={"q": "认证", "limit": 21},
            ),
            await client.get(
                f"/api/v1/knowledge-bases/{base_id}/wiki/search",
                params={"q": "认证", "claim_limit": 51},
            ),
        ]

    assert [response.status_code for response in responses] == [422, 422, 422]


async def test_wiki_rebuild_aggregation_api_is_model_free() -> None:
    """聚合页重建接口只返回页面计数，不应进入文档生成任务。"""

    base_id = uuid4()
    app = _app(FakeWikiRetrieval())
    app.state.wiki_service = FakeWikiService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/v1/knowledge-bases/{base_id}/wiki/rebuild-aggregation")

    assert response.status_code == 200
    assert response.json() == {
        "knowledge_base_id": str(base_id),
        "topic_pages": 2,
        "summary_pages": 3,
    }


async def test_wiki_index_retry_and_backfill_return_stable_conflict_codes() -> None:
    base_id = uuid4()
    page_id = uuid4()
    app = _app(FakeWikiRetrieval())
    app.state.wiki_service = FakeWikiService()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        retry = await client.post(
            f"/api/v1/knowledge-bases/{base_id}/wiki/pages/{page_id}/index/retry"
        )
        backfill = await client.post(f"/api/v1/knowledge-bases/{base_id}/wiki/index/backfill")

    assert retry.status_code == 409
    assert retry.json()["detail"]["code"] == "wiki_index_retry_not_allowed"
    assert backfill.status_code == 409
    assert backfill.json()["detail"]["code"] == "wiki_index_rebuild_in_progress"


def test_page_response_exposes_sections_and_metadata_separately_from_claims() -> None:
    """前端页面契约要把阅读段落和 Agent 事实明确分开。"""

    page = WikiPageResponse.model_validate(
        {
            "id": uuid4(),
            "knowledge_base_id": uuid4(),
            "slug": "summary/doc",
            "page_type": "summary",
            "title": "身份与访问控制规范",
            "current_version_id": uuid4(),
            "version_no": 1,
            "content": "本规范覆盖身份与访问控制。",
            "summary": "本规范覆盖身份与访问控制。",
            "origin": "pipeline",
            "summary_sections": [
                {
                    "id": uuid4(),
                    "title": "概述",
                    "text": "本规范覆盖身份与访问控制。",
                    "position": 0,
                    "trust_state": "verified",
                    "reason": None,
                    "sources": [],
                }
            ],
            "metadata": [{"key": "version", "label": "版本", "value": "3.0"}],
            "claims": [],
            "claims_next_cursor": None,
        }
    )

    assert page.summary_sections[0].title == "概述"
    assert page.metadata[0].value == "3.0"


def test_page_and_job_responses_expose_index_diagnostics_without_content() -> None:
    page = WikiPageSummary.model_validate(
        {
            "id": uuid4(),
            "knowledge_base_id": uuid4(),
            "slug": "summary/doc",
            "page_type": "summary",
            "title": "规范",
            "current_version_id": uuid4(),
            "index_state": "failed",
            "index_error_code": "embedding_failed",
        }
    )
    job = WikiJobResponse.model_validate(
        {
            "id": uuid4(),
            "page_version_id": uuid4(),
            "embedding_fingerprint": "fp-v1",
            "job_type": "page_index",
            "status": "failed",
            "stage": "failed",
            "progress": 70,
            "error_code": "embedding_failed",
            "error_reference": "ref-123",
            "estimated_tokens": 0,
            "actual_tokens": None,
            "charged_tokens": 0,
            "created_at": "2026-10-08T00:00:00Z",
        }
    )

    assert page.index_state == "failed"
    assert job.page_version_id is not None
    assert "content" not in job.model_dump()
