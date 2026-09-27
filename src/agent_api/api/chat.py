import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent_api.knowledge.application.retrieval import (
    KnowledgeRetrievalService,
    KnowledgeScopeError,
)
from agent_api.llm.agent import AgentStreamEvent, LangChainAgentStream
from agent_api.llm.protocol import StreamAgent
from agent_api.schemas.chat import ChatRequest

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


def sse_event(event: str, data: dict[str, object]) -> str:
    # 将内部事件统一包装成浏览器 EventSource 可识别的 SSE 帧。
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


async def first_non_empty(stream: AsyncIterator[str]) -> str | None:
    # 先读到首个有效内容，便于在响应开始前把上游启动失败转成 HTTP 502。
    async for chunk in stream:
        if chunk:
            return chunk
    return None


@router.post("/stream")
async def stream_chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    # 节点 1：从应用生命周期发布的共享状态中取出聊天 Agent 和可选知识库服务。
    agent: StreamAgent = request.app.state.chat_agent
    retrieval: KnowledgeRetrievalService | None = getattr(
        request.app.state, "knowledge_retrieval", None
    )
    # 节点 2：用户显式选择知识库时，必须保证知识库服务已经启用。
    if (
        payload.knowledge_scope is not None
        and payload.knowledge_scope.mode == "selected"
        and retrieval is None
    ):
        raise HTTPException(
            status_code=503,
            detail={"code": "knowledge_unavailable", "message": "Knowledge service unavailable"},
        )
    scope_ids: tuple[UUID, ...] = ()
    if retrieval is not None:
        # 节点 3：把请求中的知识库范围解析成当前工作区真实可用的知识库 ID。
        scope = payload.knowledge_scope
        try:
            scope_ids = await retrieval.resolve_scope(
                scope.mode if scope is not None else "all_enabled",
                tuple(scope.knowledge_base_ids) if scope is not None else (),
            )
        except KnowledgeScopeError as error:
            # 节点 4：范围非法、知识库不存在或已禁用时，返回可读的 422 业务错误。
            raise HTTPException(
                status_code=422,
                detail={"code": str(error), "message": "Knowledge scope is unavailable"},
            ) from None

    # 节点 5：显式请求知识库且 Agent 支持事件流时，返回带 status/sources/message 的增强 SSE。
    if (
        payload.knowledge_scope is not None
        and retrieval is not None
        and isinstance(agent, LangChainAgentStream)
    ):
        stream_events = agent.stream_events(payload.message, payload.conversation_id, scope_ids)
        buffered: list[AgentStreamEvent] = []
        try:
            # 节点 6：预读到首个 message，保证模型首包前失败仍能改成 HTTP 502。
            async for event in stream_events:
                buffered.append(event)
                if event.event == "message":
                    break
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "upstream_unavailable", "message": "Model request failed"},
            ) from exc

        async def knowledge_events() -> AsyncIterator[str]:
            # 节点 7：先吐出预读期间产生的状态、来源和首个消息事件。
            for event in buffered:
                yield sse_event(event.event, event.data)
            try:
                # 节点 8：继续转发 Agent 后续事件，保持原始事件类型。
                async for event in stream_events:
                    yield sse_event(event.event, event.data)
            except asyncio.CancelledError:
                raise
            except Exception:
                # 节点 9：响应已开始后不能再改状态码，只能用 SSE error 告知前端。
                yield sse_event(
                    "error",
                    {"code": "upstream_stream_error", "message": "Model stream failed"},
                )
                return
            # 节点 10：正常耗尽上游流后，发送 done 作为客户端收尾信号。
            yield sse_event("done", {})

        return StreamingResponse(
            knowledge_events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    # 节点 11：未进入增强事件流时，退回兼容旧前端的纯 message 流。
    if isinstance(agent, LangChainAgentStream) and retrieval is not None:
        stream = agent.stream(payload.message, payload.conversation_id, scope_ids)
    else:
        stream = agent.stream(payload.message, payload.conversation_id)
    try:
        # 节点 12：普通流同样先预读首个非空 chunk，用于区分启动失败和流中失败。
        first = await first_non_empty(stream)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "upstream_unavailable", "message": "Model request failed"},
        ) from exc

    async def events() -> AsyncIterator[str]:
        # 节点 13：把预读到的首个 chunk 先补回给客户端，避免吞掉模型输出。
        if first is not None:
            yield sse_event("message", {"content": first})
        try:
            # 节点 14：后续非空文本 chunk 统一包装成 message 事件。
            async for chunk in stream:
                if chunk:
                    yield sse_event("message", {"content": chunk})
        except asyncio.CancelledError:
            raise
        except Exception:
            # 节点 15：普通流已开始后发生异常，也通过 SSE error 收尾。
            yield sse_event(
                "error",
                {"code": "upstream_stream_error", "message": "Model stream failed"},
            )
            return
        # 节点 16：普通流正常结束，发送 done 事件。
        yield sse_event("done", {})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
