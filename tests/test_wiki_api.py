from __future__ import annotations

from uuid import uuid4

import httpx
from fastapi import FastAPI

from agent_api.knowledge.application.citations import Source
from agent_api.knowledge.wiki.api import get_wiki_retrieval, router
from agent_api.knowledge.wiki.retrieval import (
    WikiMatchedClaim,
    WikiMatchedPage,
    WikiSearchResult,
)


class FakeWikiRetrieval:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search(
        self,
        query: str,
        scope_ids: tuple,
        *,
        mode: str,
        page_limit: int,
        claim_limit: int,
    ) -> WikiSearchResult:
        self.calls.append(
            {
                "query": query,
                "scope_ids": scope_ids,
                "mode": mode,
                "page_limit": page_limit,
                "claim_limit": claim_limit,
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
        )
        claim = WikiMatchedClaim(
            id=uuid4(),
            text="需要两个独立认证因素",
            position=9,
            claim_type="summary_fact",
            score=1.4,
            sources=(source,),
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
        return WikiSearchResult((page,), False, None, None)


def _app(service: FakeWikiRetrieval) -> FastAPI:
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
            },
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["pages"][0]["matched_fields"] == ["alias", "source_chunk"]
    assert payload["pages"][0]["matched_claims"][0]["position"] == 9
    assert payload["pages"][0]["matched_claims"][0]["sources"][0]["chunk_id"]
    assert payload["degraded"] is False
    assert service.calls[0] == {
        "query": "双因素校验",
        "scope_ids": (base_id,),
        "mode": "hybrid",
        "page_limit": 5,
        "claim_limit": 12,
    }


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
