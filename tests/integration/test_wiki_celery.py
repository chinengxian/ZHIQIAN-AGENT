from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest
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
from agent_api.knowledge.infrastructure.jobs.outbox import (
    CeleryEventPublisher,
    OutboxDispatcher,
    PendingEvent,
    SqlAlchemyOutboxRepository,
)
from agent_api.knowledge.wiki.service import WikiService

ROOT = Path(__file__).resolve().parents[2]


class WikiModelStub(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = {
            "id": "chatcmpl-wiki-test",
            "object": "chat.completion",
            "created": 1,
            "model": "wiki-test",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {"summary": "这篇文档介绍离线运行。", "topics": ["离线运行"]},
                            ensure_ascii=False,
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
        }
        encoded = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class FailingPublisher:
    async def publish(self, event: PendingEvent) -> None:
        raise ConnectionError(f"queue_unavailable:{event.id}")


@pytest.mark.knowledge_integration
async def test_wiki_outbox_event_survives_worker_absence_and_is_consumed() -> None:
    url = os.environ.get("AGENT_WIKI_TEST_DATABASE_URL")
    if not url:
        pytest.skip("需要独立 Wiki 测试数据库 URL")
    server = ThreadingHTTPServer(("127.0.0.1", 0), WikiModelStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    database = DatabaseRuntime(url)
    base_id, document_id, version_id = uuid4(), uuid4(), uuid4()
    worker: asyncio.subprocess.Process | None = None
    try:
        async with database.session_factory() as session, session.begin():
            session.add(
                KnowledgeBase(
                    id=base_id,
                    workspace_id=DEFAULT_WORKSPACE_ID,
                    name=f"Wiki 队列测试 {base_id}",
                    enabled=True,
                )
            )
            await session.flush()
            document = Document(
                id=document_id,
                knowledge_base_id=base_id,
                title="离线指南",
                filename="offline.txt",
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
                    file_path="offline.txt",
                    sha256="0" * 64,
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
            document.active_version_id = version_id
            session.add(
                Chunk(
                    document_version_id=version_id,
                    ordinal=0,
                    kind="child",
                    content="离线运行不需要网络连接。",
                    token_count=20,
                )
            )
        service = WikiService(database.session_factory)
        await service.update_config(base_id, enabled=True, token_limit=10_000)
        redis_url = "redis://127.0.0.1:6379/15"
        repository = SqlAlchemyOutboxRepository(database.session_factory)
        failed_dispatch = OutboxDispatcher(repository, FailingPublisher())
        assert await failed_dispatch.dispatch_once() == 0
        await asyncio.sleep(2.2)
        dispatcher = OutboxDispatcher(
            repository,
            CeleryEventPublisher(redis_url),
        )
        assert await dispatcher.dispatch_once() >= 1
        async with database.session_factory() as session:
            job = await session.scalar(
                select(WikiJob).where(WikiJob.document_version_id == version_id)
            )
            assert job is not None and job.status == "pending"
            job_id = job.id
        environment = os.environ.copy()
        environment.update(
            {
                "AGENT_MODEL_PROVIDER": "openai",
                "AGENT_OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "AGENT_OPENAI_API_KEY": "local-test-key",
                "AGENT_OPENAI_MODEL": "wiki-test",
                "AGENT_DATABASE_URL": url,
                "AGENT_REDIS_URL": redis_url,
            }
        )
        worker = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "celery",
            "-A",
            "agent_api.knowledge.worker.celery_app:app",
            "worker",
            "--pool=solo",
            "-Q",
            "knowledge",
            "--loglevel=ERROR",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
            cwd=ROOT,
            env=environment,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for _ in range(80):
            if worker.returncode is not None:
                raise AssertionError(f"Wiki Worker exited: {worker.returncode}")
            async with database.session_factory() as session:
                current = await session.get(WikiJob, job_id)
                if current is not None and current.status == "succeeded":
                    break
            await asyncio.sleep(0.5)
        else:
            raise AssertionError("Wiki Worker did not complete queued event")
        pages = await service.list_pages(base_id)
        assert {page["page_type"] for page in pages} == {"summary", "index"}
        assert (await service.get_config(base_id))["tokens_charged"] == 60
    finally:
        if worker is not None:
            worker.terminate()
            await asyncio.wait_for(worker.wait(), timeout=10)
        async with database.session_factory() as session, session.begin():
            await session.execute(delete(KnowledgeBase).where(KnowledgeBase.id == base_id))
        await database.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
