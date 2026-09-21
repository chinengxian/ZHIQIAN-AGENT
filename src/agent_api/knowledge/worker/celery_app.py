from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from celery import Task  # type: ignore[import-untyped]

from agent_api.core.config import get_settings
from agent_api.knowledge.application.ingestion import IngestionPipelineError
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.infrastructure.jobs.outbox import (
    CeleryEventPublisher,
    OutboxDispatcher,
    SqlAlchemyOutboxRepository,
)
from agent_api.knowledge.infrastructure.jobs.repository import SqlAlchemyIngestionRepository
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage
from agent_api.knowledge.worker.celery_factory import create_celery_app
from agent_api.knowledge.worker.runtime import KnowledgeWorkerRuntime, handle_event

settings = get_settings()
app = create_celery_app(settings)


async def _process_event(event_type: str, payload: dict[str, object]) -> None:
    async with KnowledgeWorkerRuntime(settings) as runtime:
        if runtime.pipeline is None:
            raise RuntimeError("knowledge_worker_initialization_failed")
        await handle_event(event_type, payload, runtime.pipeline, runtime.cleanup)


async def _schedule_durable_retry(job_id: UUID, *, delay_seconds: int) -> None:
    database = DatabaseRuntime(settings.database_url.get_secret_value())
    try:
        repository = SqlAlchemyIngestionRepository(
            database.session_factory,
            LocalFileStorage(settings.storage_root),
        )
        await repository.schedule_retry(job_id, delay_seconds=delay_seconds)
    finally:
        await database.close()


@app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="agent_api.knowledge.process_event",
    max_retries=3,
)
def process_event(
    task: Task,
    *,
    event_id: str,
    event_type: str,
    aggregate_id: str,
    payload: dict[str, object],
) -> None:
    """消费 outbox 事件；正文不会进入参数或日志。"""

    del event_id, aggregate_id
    try:
        asyncio.run(_process_event(event_type, payload))
    except IngestionPipelineError as error:
        # 损坏文档是确定性失败；瞬时基础设施错误才指数退避重试。
        if error.code == "document_parse_failed":
            raise
        countdown = 2 ** min(task.request.retries, 6)
        try:
            job_id = UUID(str(payload["job_id"]))
            asyncio.run(_schedule_durable_retry(job_id, delay_seconds=countdown))
        except Exception:
            # PostgreSQL 不可用时，先利用 Celery 自身的重试维持任务存活。
            raise task.retry(exc=RuntimeError(error.code), countdown=countdown) from None


async def _dispatch_outbox() -> int:
    database = DatabaseRuntime(settings.database_url.get_secret_value())
    try:
        repository = SqlAlchemyOutboxRepository(database.session_factory)
        publisher = CeleryEventPublisher(settings.redis_url.get_secret_value())
        return await OutboxDispatcher(repository, publisher).dispatch_once()
    finally:
        await database.close()


@app.task(name="agent_api.knowledge.dispatch_outbox")  # type: ignore[untyped-decorator]
def dispatch_outbox() -> int:
    """由 Celery Beat 周期扫描，Redis 短暂中断不会丢失数据库事件。"""

    return asyncio.run(_dispatch_outbox())


async def _recover_stale_jobs() -> int:
    database = DatabaseRuntime(settings.database_url.get_secret_value())
    try:
        repository = SqlAlchemyIngestionRepository(
            database.session_factory,
            LocalFileStorage(settings.storage_root),
        )
        recovered = await repository.recover_stale_jobs(
            stale_before=datetime.now(UTC) - timedelta(minutes=5),
            limit=100,
        )
        return len(recovered)
    finally:
        await database.close()


@app.task(name="agent_api.knowledge.recover_stale_jobs")  # type: ignore[untyped-decorator]
def recover_stale_jobs() -> int:
    """周期恢复 Worker 崩溃后遗留的心跳超时任务。"""

    return asyncio.run(_recover_stale_jobs())
