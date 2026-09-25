import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
from asgi_lifespan import LifespanManager
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from agent_api.core.config import Settings
from agent_api.knowledge.tools.readonly import create_knowledge_tools
from agent_api.llm.agent import LangChainAgentStream
from agent_api.main import create_app
from tests.test_knowledge_tools import BASE_ID, FakeRetrieval

CONVERSATION_ID = "82de55a8-6065-4eeb-84af-079ea2e2b2c5"


class ToolCallingChatModel(BaseChatModel):
    use_tool: bool = Field(default=True)

    @property
    def _llm_type(self) -> str:
        return "knowledge-tool-test-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> BaseChatModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="unused"))])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        if not self.use_tool:
            yield ChatGenerationChunk(message=AIMessageChunk(content="没有依据[1]"))
            return
        if any(isinstance(message, ToolMessage) for message in messages):
            yield ChatGenerationChunk(message=AIMessageChunk(content="根据指南[1]回答"))
        else:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": "search_knowledge",
                            "args": '{"query":"指南"}',
                            "id": "call-1",
                            "index": 0,
                        }
                    ],
                )
            )


class ScopeRetrieval(FakeRetrieval):
    async def resolve_scope(
        self, mode: str, selected_ids: tuple[UUID, ...] = ()
    ) -> tuple[UUID, ...]:
        if mode == "selected" and selected_ids != (BASE_ID,):
            from agent_api.knowledge.application.retrieval import KnowledgeScopeError

            raise KnowledgeScopeError("knowledge_scope_unavailable")
        return (BASE_ID,)


async def test_chat_sends_sources_before_model_text_and_preserves_legacy_stream(
    settings: Settings,
) -> None:
    retrieval = ScopeRetrieval()
    agent = LangChainAgentStream(ToolCallingChatModel())
    agent.enable_knowledge(create_knowledge_tools(retrieval))  # type: ignore[arg-type]
    app = create_app(settings_factory=lambda: settings, agent_factory=lambda _: agent)
    async with LifespanManager(app):
        app.state.knowledge_retrieval = retrieval
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/chat/stream",
                json={
                    "conversation_id": CONVERSATION_ID,
                    "message": "指南说了什么？",
                    "knowledge_scope": {
                        "mode": "selected",
                        "knowledge_base_ids": [str(BASE_ID)],
                    },
                },
            )
            assert response.status_code == 200
            frames = response.text.split("\n\n")[:-1]
            events = [frame.split("\n", 1)[0] for frame in frames]
            assert "event: sources" in events
            assert events.index("event: sources") < events.index("event: message")
            assert events[-1] == "event: done"
            source_frame = frames[events.index("event: sources")]
            sources = json.loads(source_frame.split("data: ", 1)[1])["items"]
            assert sources[0]["citation_id"] == "[1]"
            assert "file_path" not in sources[0]

            legacy = await client.post(
                "/api/v1/chat/stream",
                json={"conversation_id": str(UUID(int=99)), "message": "再说一次"},
            )
            assert legacy.status_code == 200
            assert "event: sources" not in legacy.text
            assert "event: message" in legacy.text


async def test_knowledge_scope_errors_and_no_evidence_citation(settings: Settings) -> None:
    retrieval = ScopeRetrieval()
    agent = LangChainAgentStream(ToolCallingChatModel(use_tool=False))
    agent.enable_knowledge(create_knowledge_tools(retrieval))  # type: ignore[arg-type]
    app = create_app(settings_factory=lambda: settings, agent_factory=lambda _: agent)
    async with LifespanManager(app):
        app.state.knowledge_retrieval = retrieval
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            base_payload = {"conversation_id": CONVERSATION_ID, "message": "无依据问题"}
            empty = await client.post(
                "/api/v1/chat/stream",
                json={
                    **base_payload,
                    "knowledge_scope": {"mode": "selected", "knowledge_base_ids": []},
                },
            )
            assert empty.status_code == 422
            unknown = await client.post(
                "/api/v1/chat/stream",
                json={
                    **base_payload,
                    "knowledge_scope": {
                        "mode": "selected",
                        "knowledge_base_ids": [str(UUID(int=99))],
                    },
                },
            )
            assert unknown.status_code == 422
            response = await client.post(
                "/api/v1/chat/stream",
                json={
                    **base_payload,
                    "knowledge_scope": {"mode": "all_enabled", "knowledge_base_ids": []},
                },
            )
            assert response.status_code == 200
            assert "event: sources" not in response.text
            assert "[1]" not in response.text
            assert "没有依据" in response.text
            assert retrieval.calls == []


async def test_all_enabled_degrades_to_legacy_chat_when_knowledge_is_disabled(
    settings: Settings,
) -> None:
    agent = LangChainAgentStream(ToolCallingChatModel(use_tool=False))
    app = create_app(settings_factory=lambda: settings, agent_factory=lambda _: agent)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {"conversation_id": CONVERSATION_ID, "message": "hello"}
            response = await client.post(
                "/api/v1/chat/stream",
                json={**payload, "knowledge_scope": {"mode": "all_enabled"}},
            )
            assert response.status_code == 200
            assert "event: message" in response.text
            assert "event: sources" not in response.text
            selected = await client.post(
                "/api/v1/chat/stream",
                json={
                    **payload,
                    "knowledge_scope": {
                        "mode": "selected",
                        "knowledge_base_ids": [str(BASE_ID)],
                    },
                },
            )
            assert selected.status_code == 503
