from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any, Literal
from uuid import UUID

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, model_validator

from agent_api.knowledge.application.citations import Source
from agent_api.knowledge.application.retrieval import KnowledgeRetrievalService
from agent_api.knowledge.wiki.retrieval import WikiRetrievalService
from agent_api.knowledge.wiki.service import WikiService


@dataclass(slots=True)
class TurnKnowledgeState:
    scope_ids: tuple[UUID, ...]
    sources: list[Source] = field(default_factory=list)
    degraded: bool = False

    def register(self, sources: list[Source]) -> list[Source]:
        result: list[Source] = []
        seen = {source.chunk_id: source for source in self.sources}
        for source in sources:
            existing = seen.get(source.chunk_id)
            if existing is not None:
                result.append(existing)
                continue
            if len(self.sources) >= 8:
                break
            registered = replace(
                source,
                citation_id=f"[{len(self.sources) + 1}]",
                rank=len(self.sources) + 1,
            )
            self.sources.append(registered)
            seen[source.chunk_id] = registered
            result.append(registered)
        return result


current_turn: ContextVar[TurnKnowledgeState | None] = ContextVar("knowledge_turn", default=None)


class SearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid"
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=8, ge=1, le=8)


class ReadInput(BaseModel):
    document_id: UUID | None = None
    chunk_id: UUID | None = None

    @model_validator(mode="after")
    def require_one_identifier(self) -> ReadInput:
        if (self.document_id is None) == (self.chunk_id is None):
            raise ValueError("document_id or chunk_id is required")
        return self


class ListInput(BaseModel):
    title: str = Field(default="", max_length=200)
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=20, ge=1, le=20)


class WikiSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=20)
    cursor: str | None = Field(default=None, max_length=2048)


class WikiReadInput(BaseModel):
    knowledge_base_id: UUID
    page_id: UUID
    query: str = Field(default="", max_length=200)
    cursor: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)


def _scope(requested: list[UUID] | None = None) -> tuple[UUID, ...] | None:
    turn = current_turn.get()
    if turn is None:
        return None
    if not requested:
        return turn.scope_ids
    if not set(requested).issubset(turn.scope_ids):
        return None
    return tuple(requested)


def create_knowledge_tools(
    service: KnowledgeRetrievalService,
    wiki_service: WikiService | None = None,
    wiki_retrieval: WikiRetrievalService | None = None,
) -> list[BaseTool]:
    @tool("search_knowledge", args_schema=SearchInput)
    async def search_knowledge(
        query: str,
        mode: str = "hybrid",
        knowledge_base_ids: list[UUID] | None = None,
        limit: int = 8,
    ) -> str:
        """只读检索当前会话允许的知识库，返回可追溯来源。"""

        scope_ids = _scope(knowledge_base_ids)
        if scope_ids is None:
            return json.dumps({"status": "knowledge_scope_unavailable"})
        try:
            sources, degraded = await service.search(query, scope_ids, mode=mode, limit=limit)
        except Exception:
            return json.dumps({"status": "retrieval_unavailable"})
        turn = current_turn.get()
        assert turn is not None
        turn.degraded |= degraded
        registered = turn.register(sources)
        return json.dumps(
            {
                "status": "ok" if registered else "no_relevant_knowledge",
                "sources": [source.public_dict() for source in registered],
                "rerank_degraded": degraded,
            },
            ensure_ascii=False,
        )

    @tool("read_document", args_schema=ReadInput)
    async def read_document(
        document_id: UUID | None = None,
        chunk_id: UUID | None = None,
    ) -> str:
        """只读当前范围内的文档正文或命中块上下文。"""

        scope_ids = _scope()
        if scope_ids is None:
            return json.dumps({"status": "knowledge_scope_unavailable"})
        try:
            sources = await service.read(scope_ids, document_id=document_id, chunk_id=chunk_id)
        except Exception:
            return json.dumps({"status": "retrieval_unavailable"})
        turn = current_turn.get()
        assert turn is not None
        registered = turn.register(sources)
        return json.dumps(
            {
                "status": "ok" if registered else "no_relevant_knowledge",
                "sources": [source.public_dict() for source in registered],
                "truncated": len(sources) > len(registered),
            },
            ensure_ascii=False,
        )

    @tool("list_documents", args_schema=ListInput)
    async def list_documents(
        title: str = "",
        knowledge_base_ids: list[UUID] | None = None,
        limit: int = 20,
    ) -> str:
        """只读列出当前会话允许的文档标题和文件名。"""

        scope_ids = _scope(knowledge_base_ids)
        if scope_ids is None:
            return json.dumps({"status": "knowledge_scope_unavailable"})
        try:
            documents = await service.list_documents(scope_ids, title=title, limit=limit)
        except Exception:
            return json.dumps({"status": "retrieval_unavailable"})
        return json.dumps({"status": "ok", "documents": documents}, ensure_ascii=False)

    tools: list[BaseTool] = [search_knowledge, read_document, list_documents]
    if wiki_service is None or wiki_retrieval is None:
        return tools

    def register_claim(claim: Any, *, score: float | None = None) -> dict[str, object] | None:
        turn = current_turn.get()
        assert turn is not None
        registered = turn.register(list(claim.sources))
        if not registered:
            return None
        result: dict[str, object] = {
            "text": claim.text,
            "position": claim.position,
            "claim_type": claim.claim_type,
            "citations": [source.citation_id for source in registered],
        }
        if score is not None:
            result["score"] = score
        return result

    @tool("wiki_search", args_schema=WikiSearchInput)
    async def wiki_search(
        query: str,
        knowledge_base_ids: list[UUID] | None = None,
        cursor: str | None = None,
    ) -> str:
        """搜索当前范围内启用的 Wiki，只返回有有效原文证据的结论。"""

        scope_ids = _scope(knowledge_base_ids)
        if scope_ids is None:
            return json.dumps({"status": "knowledge_scope_unavailable"})
        try:
            result = await wiki_retrieval.search(query, scope_ids, cursor=cursor)
        except Exception:
            return json.dumps({"status": "retrieval_unavailable"})
        turn = current_turn.get()
        assert turn is not None
        turn.degraded |= result.degraded
        if result.no_answer:
            return json.dumps(
                {
                    "status": "no_relevant_knowledge",
                    "message": "Wiki 中没有足够相关且可核验的知识",
                    "pages": [],
                    "degraded": result.degraded,
                    "degradation_reason": result.degradation_reason,
                    "no_answer_reason": result.no_answer_reason,
                    "next_cursor": result.next_cursor,
                },
                ensure_ascii=False,
            )
        pages: list[dict[str, object]] = []
        for page in result.pages:
            claims = [
                registered
                for claim in page.matched_claims
                if (registered := register_claim(claim, score=claim.score)) is not None
            ]
            if not claims:
                continue
            pages.append(
                {
                    "page_id": str(page.id),
                    "knowledge_base_id": str(page.knowledge_base_id),
                    "slug": page.slug,
                    "title": page.title,
                    "score": page.score,
                    "matched_fields": list(page.matched_fields),
                    "claims": claims,
                    "omitted_claim_count": page.omitted_claim_count,
                }
            )
        return json.dumps(
            {
                "status": "ok" if pages else "no_relevant_knowledge",
                "pages": pages,
                "degraded": result.degraded,
                "degradation_reason": result.degradation_reason,
                "truncation_reason": result.truncation_reason,
                "next_cursor": result.next_cursor,
            },
            ensure_ascii=False,
        )

    @tool("wiki_read_page", args_schema=WikiReadInput)
    async def wiki_read_page(
        knowledge_base_id: UUID,
        page_id: UUID,
        query: str = "",
        cursor: int = 0,
        limit: int = 20,
    ) -> str:
        """读取当前范围内 Wiki 页面的有效结论及原文引用。"""

        scope_ids = _scope([knowledge_base_id])
        if scope_ids is None:
            return json.dumps({"status": "knowledge_scope_unavailable"})
        try:
            page = await wiki_retrieval.read_page(
                knowledge_base_id,
                page_id,
                query=query,
                cursor=cursor,
                limit=limit,
            )
        except Exception:
            return json.dumps({"status": "retrieval_unavailable"})
        pages: list[dict[str, object]] = []
        if page is not None:
            claims = [
                registered
                for claim in page["claims"]
                if (registered := register_claim(claim)) is not None
            ]
            if claims:
                pages.append(
                    {
                        "page_id": str(page["page_id"]),
                        "title": page["title"],
                        "claims": claims,
                        "next_cursor": page["next_cursor"],
                        "omitted_claim_count": page["omitted_claim_count"],
                    }
                )
        return json.dumps(
            {"status": "ok" if pages else "no_relevant_knowledge", "pages": pages},
            ensure_ascii=False,
        )

    return [*tools, wiki_search, wiki_read_page]
