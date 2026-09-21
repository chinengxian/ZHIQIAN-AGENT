import asyncio
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pymilvus import MilvusClient
from sqlalchemy import select

from agent_api.knowledge.application.management import SqlAlchemyKnowledgeManagementService
from agent_api.knowledge.infrastructure.database.models import (
    Document,
    DocumentVersion,
    IngestionJob,
    OutboxEvent,
)
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.infrastructure.jobs.outbox import CeleryEventPublisher, PendingEvent
from agent_api.knowledge.infrastructure.jobs.repository import SqlAlchemyIngestionRepository
from agent_api.knowledge.infrastructure.milvus.index import MilvusChunkIndex
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage
from tests.test_knowledge_management_integration import (
    DATABASE_URL,
    LocalEmbeddingHandler,
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


class BlockingEmbeddingHandler(LocalEmbeddingHandler):
    entered = threading.Event()
    release = threading.Event()

    def do_POST(self) -> None:
        self.entered.set()
        self.release.wait(60)
        try:
            super().do_POST()
        except (BrokenPipeError, ConnectionResetError):
            return


class FlakyEmbeddingHandler(LocalEmbeddingHandler):
    allow_success = threading.Event()
    failed_request = threading.Event()

    def do_POST(self) -> None:
        if not self.allow_success.is_set():
            self.failed_request.set()
            body = b'{"error":{"message":"temporary unavailable","type":"server_error"}}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_POST()


async def start_worker(env: dict[str, str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "celery",
        "-A",
        "agent_api.knowledge.worker.celery_app:app",
        "worker",
        "--pool=solo",
        "-Q",
        "knowledge",
        "--loglevel=WARNING",
        "--without-gossip",
        "--without-mingle",
        "--without-heartbeat",
        cwd=Path.cwd(),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


async def stop_worker(worker: asyncio.subprocess.Process) -> None:
    if worker.returncode is not None:
        return
    worker.terminate()
    try:
        await asyncio.wait_for(worker.wait(), timeout=10)
    except TimeoutError:
        worker.kill()
        await worker.wait()


async def test_worker_restart_recovers_stale_upload_from_outbox(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "restart-uploads").model_copy(
        update={"embedding_dimension": 1536}
    )
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    BlockingEmbeddingHandler.entered.clear()
    BlockingEmbeddingHandler.release.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), BlockingEmbeddingHandler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    worker: asyncio.subprocess.Process | None = None
    knowledge_base_id: UUID | None = None
    version_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="Worker重启集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "restart.txt", "text/plain", "中断后继续入库".encode()
        )
        async with runtime.session_factory() as session:
            version_id = await session.scalar(
                select(DocumentVersion.id).where(
                    DocumentVersion.document_id == accepted["document_id"]
                )
            )
        assert version_id is not None
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
                "AGENT_STORAGE_ROOT": str(settings.storage_root),
                "AGENT_EMBEDDING_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "AGENT_EMBEDDING_API_KEY": "test-embedding-key",
                "AGENT_EMBEDDING_MODEL": "text-embedding-3-small",
            }
        )
        worker = await start_worker(env)
        publisher = CeleryEventPublisher("redis://127.0.0.1:6379/0")
        await publisher.publish(
            PendingEvent(
                id=uuid4(),
                event_type="document.ingestion.requested",
                aggregate_id=version_id,
                payload={"job_id": str(accepted["job_id"])},
            )
        )
        assert await asyncio.to_thread(BlockingEmbeddingHandler.entered.wait, 60)
        async with runtime.session_factory() as session:
            job = await session.get(IngestionJob, accepted["job_id"])
        assert job is not None and job.status == "running"
        await stop_worker(worker)
        worker = None
        BlockingEmbeddingHandler.release.set()

        async with runtime.session_factory() as session, session.begin():
            job = await session.get(IngestionJob, accepted["job_id"])
            assert job is not None
            job.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
        repository = SqlAlchemyIngestionRepository(runtime.session_factory, storage)
        recovered = await repository.recover_stale_jobs(
            stale_before=datetime.now(UTC) - timedelta(minutes=5), limit=10
        )
        assert recovered == [accepted["job_id"]]
        async with runtime.session_factory() as session:
            recovery_event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_key == f"recover:{accepted['job_id']}:1"
                )
            )
        assert recovery_event is not None
        await publisher.publish(
            PendingEvent(
                recovery_event.id,
                recovery_event.event_type,
                recovery_event.aggregate_id,
                recovery_event.payload,
            )
        )
        worker = await start_worker(env)
        for _ in range(90):
            assert worker.returncode is None, "重启 Worker 提前退出"
            async with runtime.session_factory() as session:
                job = await session.get(IngestionJob, accepted["job_id"])
                document = await session.get(Document, accepted["document_id"])
            if job is not None and job.status == "succeeded":
                assert document is not None and document.active_version_id == version_id
                assert document.status == "ready"
                break
            await asyncio.sleep(1)
        else:
            pytest.fail("重启 Worker 未在 90 秒内完成恢复任务")
        client = MilvusClient(uri="http://127.0.0.1:19530")
        try:
            assert await MilvusChunkIndex(client, "knowledge_chunks").count_version(version_id) > 0
        finally:
            client.close()
    finally:
        BlockingEmbeddingHandler.release.set()
        if worker is not None:
            await stop_worker(worker)
        server.shutdown()
        server.server_close()
        if version_id is not None:
            client = MilvusClient(uri="http://127.0.0.1:19530")
            try:
                await MilvusChunkIndex(client, "knowledge_chunks").delete_version(version_id)
            finally:
                client.close()
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_worker_embedding_outage_uses_durable_retry_outbox(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "flaky-worker-uploads").model_copy(
        update={"embedding_dimension": 1536}
    )
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    FlakyEmbeddingHandler.allow_success.clear()
    FlakyEmbeddingHandler.failed_request.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), FlakyEmbeddingHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    worker: asyncio.subprocess.Process | None = None
    knowledge_base_id: UUID | None = None
    version_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="Worker暂时故障集成", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "flaky.txt", "text/plain", "故障恢复正文".encode()
        )
        version_id = accepted["document_version_id"]
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
                "AGENT_STORAGE_ROOT": str(settings.storage_root),
                "AGENT_EMBEDDING_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                "AGENT_EMBEDDING_API_KEY": "test-embedding-key",
                "AGENT_EMBEDDING_MODEL": "text-embedding-3-small",
            }
        )
        worker = await start_worker(env)
        publisher = CeleryEventPublisher("redis://127.0.0.1:6379/0")
        await publisher.publish(
            PendingEvent(
                id=uuid4(),
                event_type="document.ingestion.requested",
                aggregate_id=version_id,
                payload={"job_id": str(accepted["job_id"])},
            )
        )
        for _ in range(90):
            assert worker.returncode is None, "Celery Worker 提前退出"
            async with runtime.session_factory() as session:
                job = await session.get(IngestionJob, accepted["job_id"])
            if job is not None and job.status == "retry_wait":
                break
            await asyncio.sleep(1)
        else:
            pytest.fail("Embedding 瞬时故障未进入持久化重试状态")
        assert FlakyEmbeddingHandler.failed_request.is_set()
        assert job is not None and job.attempt_count == 1
        async with runtime.session_factory() as session:
            retry_event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_key == f"retry-job:{accepted['job_id']}:1"
                )
            )
        assert retry_event is not None and retry_event.status == "pending"
        FlakyEmbeddingHandler.allow_success.set()
        await publisher.publish(
            PendingEvent(
                retry_event.id,
                retry_event.event_type,
                retry_event.aggregate_id,
                retry_event.payload,
            )
        )
        for _ in range(90):
            async with runtime.session_factory() as session:
                job = await session.get(IngestionJob, accepted["job_id"])
                document = await session.get(Document, accepted["document_id"])
            if job is not None and job.status == "succeeded":
                assert document is not None and document.active_version_id == version_id
                assert document.status == "ready"
                break
            await asyncio.sleep(1)
        else:
            pytest.fail("Embedding 恢复后 Worker 未完成重试")
    finally:
        FlakyEmbeddingHandler.allow_success.set()
        if worker is not None:
            await stop_worker(worker)
        server.shutdown()
        server.server_close()
        if version_id is not None:
            client = MilvusClient(uri="http://127.0.0.1:19530")
            try:
                await MilvusChunkIndex(client, "knowledge_chunks").delete_version(version_id)
            finally:
                client.close()
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()
