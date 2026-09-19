import json
from uuid import UUID

import httpx
import pytest
from asgi_lifespan import LifespanManager

from agent_api.core.config import Settings
from agent_api.main import create_app
from tests.fakes import FakeStreamAgent

CONVERSATION_ID = "82de55a8-6065-4eeb-84af-079ea2e2b2c5"


async def request_stream(
    settings: Settings,
    agent: FakeStreamAgent,
    payload: dict[str, object],
) -> httpx.Response:
    app = create_app(settings_factory=lambda: settings, agent_factory=lambda _: agent)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/v1/chat/stream", json=payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"messages": []},
        {"conversation_id": "not-a-uuid", "message": "hello"},
        {"conversation_id": CONVERSATION_ID, "message": ""},
        {"conversation_id": CONVERSATION_ID, "message": "   "},
        {"conversation_id": CONVERSATION_ID, "message": 123},
        {"conversation_id": CONVERSATION_ID},
    ],
)
async def test_invalid_requests_return_422_without_calling_model(
    settings: Settings,
    payload: dict[str, object],
) -> None:
    agent = FakeStreamAgent(["unused"])

    response = await request_stream(settings, agent, payload)

    assert response.status_code == 422
    assert agent.calls == []


async def test_stream_emits_ordered_messages_and_done(settings: Settings) -> None:
    agent = FakeStreamAgent(["你\n", "好"])

    response = await request_stream(
        settings,
        agent,
        {"conversation_id": CONVERSATION_ID, "message": "hello"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.text.split("\n\n")[:-1] == [
        'event: message\ndata: {"content":"你\\n"}',
        'event: message\ndata: {"content":"好"}',
        "event: done\ndata: {}",
    ]
    assert agent.calls == [("hello", UUID(CONVERSATION_ID))]


async def test_empty_model_stream_emits_only_done(settings: Settings) -> None:
    response = await request_stream(
        settings,
        FakeStreamAgent(),
        {"conversation_id": CONVERSATION_ID, "message": "hello"},
    )

    assert response.text == "event: done\ndata: {}\n\n"


async def test_failure_before_first_chunk_returns_sanitized_502(settings: Settings) -> None:
    response = await request_stream(
        settings,
        FakeStreamAgent(fail_before_first=True),
        {"conversation_id": CONVERSATION_ID, "message": "hello"},
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": {"code": "upstream_unavailable", "message": "Model request failed"}
    }
    assert "provider details" not in response.text


async def test_failure_after_first_chunk_emits_error_without_done(settings: Settings) -> None:
    response = await request_stream(
        settings,
        FakeStreamAgent(["partial"], fail_after_chunks=True),
        {"conversation_id": CONVERSATION_ID, "message": "hello"},
    )

    frames = response.text.split("\n\n")[:-1]
    assert frames[0] == 'event: message\ndata: {"content":"partial"}'
    assert json.loads(frames[1].split("data: ", 1)[1]) == {
        "code": "upstream_stream_error",
        "message": "Model stream failed",
    }
    assert all("event: done" not in frame for frame in frames)
