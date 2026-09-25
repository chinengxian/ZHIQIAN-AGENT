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


class WikiReadInput(BaseModel):
    knowledge_base_id: UUID
    page_id: UUID


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
    service: KnowledgeRetrievalService, wiki_service: WikiService | None = None
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
    if wiki_service is None:
        return tools

    def trusted_claims(pages: list[dict[str, Any]]) -> list[dict[str, object]]:
        turn = current_turn.get()
        assert turn is not None
        result: list[dict[str, object]] = []
        for page in pages:
            entries: list[dict[str, object]] = []
            for claim in page["claims"]:
                citations: list[str] = []
                for origin in claim["sources"]:
                    chunk_id = origin["chunk_id"]
                    if chunk_id is None or not origin["valid"]:
                        continue
                    source = Source(
                        citation_id="",
                        chunk_id=chunk_id,
                        document_id=origin["document_id"],
                        document_version_id=origin["document_version_id"],
                        title=origin["document_title"],
                        filename=origin["document_title"],
                        page_start=origin["page_start"],
                        page_end=origin["page_end"],
                        heading_path=tuple(origin["heading_path"]),
                        excerpt=claim["text"],
                        score=1.0,
                        rank=0,
                    )
                    citations.extend(item.citation_id for item in turn.register([source]))
                if citations:
                    entries.append({"text": claim["text"], "citations": citations})
            if entries:
                result.append(
                    {"page_id": str(page["page_id"]), "title": page["title"], "claims": entries}
                )
        return result

    @tool("wiki_search", args_schema=WikiSearchInput)
    async def wiki_search(query: str, knowledge_base_ids: list[UUID] | None = None) -> str:
        """搜索当前范围内启用的 Wiki，只返回有有效原文证据的结论。"""

        scope_ids = _scope(knowledge_base_ids)
        if scope_ids is None:
            return json.dumps({"status": "knowledge_scope_unavailable"})
        try:
            pages = []
            for knowledge_base_id in scope_ids:
                pages.extend(await wiki_service.agent_pages(knowledge_base_id, query, limit=5))
            results = trusted_claims(pages[:8])
        except Exception:
            return json.dumps({"status": "retrieval_unavailable"})
        return json.dumps(
            {"status": "ok" if results else "no_relevant_knowledge", "pages": results},
            ensure_ascii=False,
        )

    @tool("wiki_read_page", args_schema=WikiReadInput)
    async def wiki_read_page(knowledge_base_id: UUID, page_id: UUID) -> str:
        """读取当前范围内 Wiki 页面的有效结论及原文引用。"""

        scope_ids = _scope([knowledge_base_id])
        if scope_ids is None:
            return json.dumps({"status": "knowledge_scope_unavailable"})
        try:
            page = await wiki_service.agent_read_page(knowledge_base_id, page_id)
            results = trusted_claims([page] if page is not None else [])
        except Exception:
            return json.dumps({"status": "retrieval_unavailable"})
        return json.dumps(
            {"status": "ok" if results else "no_relevant_knowledge", "pages": results},
            ensure_ascii=False,
        )

    return [*tools, wiki_search, wiki_read_page]
