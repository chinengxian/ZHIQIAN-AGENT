import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from agent_api.knowledge.infrastructure.database.models import OutboxEvent
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from tests.test_knowledge_management_integration import (
    DATABASE_URL,
    cleanup_knowledge_base,
    knowledge_settings,
)

pytestmark = [
    pytest.mark.knowledge_integration,
    pytest.mark.skipif(
        os.getenv("RUN_KNOWLEDGE_INTEGRATION") != "1",
        reason="set RUN_KNOWLEDGE_INTEGRATION=1 to use local knowledge containers",
    ),
]


async def start_api(port: int, env: dict[str, str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uvicorn",
        "agent_api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        cwd=Path.cwd(),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


async def stop_api(api: asyncio.subprocess.Process | None) -> None:
    if api is None or api.returncode is not None:
        return
    api.terminate()
    try:
        await asyncio.wait_for(api.wait(), timeout=10)
    except TimeoutError:
        api.kill()
        await api.wait()


async def wait_for_api(api: asyncio.subprocess.Process, client: httpx.AsyncClient) -> None:
    for _ in range(60):
        assert api.returncode is None, "API 进程提前退出"
        try:
            response = await client.get("/api/v1/knowledge-bases")
            if response.status_code == 200:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(1)
    pytest.fail("API 未在 60 秒内启动")


async def test_api_process_restart_keeps_committed_upload(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "api-restart-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = os.environ.copy()
    env.update(
        {
            "AGENT_MODEL_PROVIDER": "openai",
            "AGENT_OPENAI_BASE_URL": "https://example.test/v1",
            "AGENT_OPENAI_API_KEY": "test-chat-key",
            "AGENT_OPENAI_MODEL": "chat-model",
            "AGENT_KNOWLEDGE_ENABLED": "true",
            "AGENT_DATABASE_URL": DATABASE_URL,
            "AGENT_REDIS_URL": "redis://127.0.0.1:6379/0",
            "AGENT_MILVUS_URI": "http://127.0.0.1:19530",
            "AGENT_STORAGE_ROOT": str(settings.storage_root),
            "AGENT_EMBEDDING_BASE_URL": "https://example.test/v1",
            "AGENT_EMBEDDING_API_KEY": "test-embedding-key",
            "AGENT_EMBEDDING_MODEL": "text-embedding-3-small",
        }
    )
    api: asyncio.subprocess.Process | None = None
    knowledge_base_id: UUID | None = None
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
            api = await start_api(port, env)
            await wait_for_api(api, client)
            created = await client.post(
                "/api/v1/knowledge-bases",
                json={"name": "API 重启集成", "description": ""},
            )
            assert created.status_code == 201
            knowledge_base_id = UUID(created.json()["id"])
            uploaded = await client.post(
                f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
                files={"file": ("restart.txt", "重启后仍可恢复的正文".encode(), "text/plain")},
            )
            assert uploaded.status_code == 202
            accepted = uploaded.json()

            # 关闭并重启真正的 API 进程，随后读取 PostgreSQL 中已提交的任务。
            await stop_api(api)
            api = await start_api(port, env)
            await wait_for_api(api, client)
            document = await client.get(f"/api/v1/documents/{accepted['document_id']}")
            assert document.status_code == 200
            assert document.json()["job"]["id"] == accepted["job_id"]

        async with runtime.session_factory() as session:
            event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == UUID(accepted["document_version_id"])
                )
            )
        assert event is not None and event.status == "pending"
    finally:
        await stop_api(api)
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()
