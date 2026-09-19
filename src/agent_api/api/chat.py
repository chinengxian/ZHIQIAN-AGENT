import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent_api.llm.protocol import StreamAgent
from agent_api.schemas.chat import ChatRequest

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


def sse_event(event: str, data: dict[str, str]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


async def first_non_empty(stream: AsyncIterator[str]) -> str | None:
    async for chunk in stream:
        if chunk:
            return chunk
    return None


@router.post("/stream")
async def stream_chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    agent: StreamAgent = request.app.state.chat_agent
    stream = agent.stream(payload.message, payload.conversation_id)
    try:
        first = await first_non_empty(stream)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "upstream_unavailable", "message": "Model request failed"},
        ) from exc

    async def events() -> AsyncIterator[str]:
        if first is not None:
            yield sse_event("message", {"content": first})
        try:
            async for chunk in stream:
                if chunk:
                    yield sse_event("message", {"content": chunk})
        except asyncio.CancelledError:
            raise
        except Exception:
            yield sse_event(
                "error",
                {"code": "upstream_stream_error", "message": "Model stream failed"},
            )
            return
        yield sse_event("done", {})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
