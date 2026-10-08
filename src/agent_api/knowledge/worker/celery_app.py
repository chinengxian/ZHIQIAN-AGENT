from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from celery import Task  # type: ignore[import-untyped]
from pymilvus import MilvusClient  # type: ignore[import-untyped]

from agent_api.core.config import get_settings
from agent_api.knowledge.application.ingestion import IngestionPipelineError
from agent_api.knowledge.domain.events import OutboxEventType
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.infrastructure.embedding.embedding import create_embedding_adapter
from agent_api.knowledge.infrastructure.jobs.outbox import (
    CeleryEventPublisher,
    OutboxDispatcher,
    SqlAlchemyOutboxRepository,
)
from agent_api.knowledge.infrastructure.jobs.repository import SqlAlchemyIngestionRepository
from agent_api.knowledge.infrastructure.milvus import MilvusWikiIndex
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage
from agent_api.knowledge.wiki.generator import ChatWikiGenerator
from agent_api.knowledge.wiki.index_jobs import (
    SqlAlchemyWikiIndexRepository,
    recover_stale_page_index_jobs,
)
from agent_api.knowledge.wiki.indexing import WikiIndexService, embedding_fingerprint
from agent_api.knowledge.wiki.processor import WikiProcessor
from agent_api.knowledge.worker.celery_factory import create_celery_app
from agent_api.knowledge.worker.runtime import KnowledgeWorkerRuntime, handle_event
from agent_api.llm.strategy import STRATEGIES

settings = get_settings()
app = create_celery_app(settings)


async def _process_event(event_type: OutboxEventType, payload: dict[str, object]) -> None:
    if event_type is OutboxEventType.WIKI_DOCUMENT_GENERATE_REQUESTED:
        database = DatabaseRuntime(settings.database_url.get_secret_value())
        try:
            model = STRATEGIES[settings.model_provider].create_model(settings)
            model_name = (
                settings.openai_model
                if settings.model_provider.value == "openai"
                else settings.anthropic_model
            ) or "configured-model"
            processor = WikiProcessor(
                database.session_factory,
                ChatWikiGenerator(model, model_name),
                index_enabled=settings.wiki_index_enabled,
                embedding_fingerprint=embedding_fingerprint(settings),
            )
            await processor.run(UUID(str(payload["job_id"])))
        finally:
            await database.close()
        return
    if event_type in {
        OutboxEventType.WIKI_PAGE_INDEX_REQUESTED,
        OutboxEventType.WIKI_PAGE_INDEX_DELETE_REQUESTED,
    }:
        database = DatabaseRuntime(settings.database_url.get_secret_value())
        token = settings.milvus_token.get_secret_value() if settings.milvus_token else None
        options: dict[str, str] = {"uri": settings.milvus_uri}
        if token:
            options["token"] = token
        milvus = await asyncio.to_thread(MilvusClient, **options)
        try:
            index = MilvusWikiIndex(
                milvus,
                search_collection=settings.wiki_milvus_collection_alias,
                write_collection=settings.wiki_milvus_collection_version,
            )
            if event_type is OutboxEventType.WIKI_PAGE_INDEX_DELETE_REQUESTED:
                await index.delete_page_version(UUID(str(payload["page_version_id"])))
                return
            repository = SqlAlchemyWikiIndexRepository(
                database.session_factory,
                embedding_dimension=settings.embedding_dimension,
            )
            service = WikiIndexService(
                repository=repository,
                embedder=create_embedding_adapter(settings),
                index=index,
                embedding_dimension=settings.embedding_dimension,
            )
            await service.run(UUID(str(payload["job_id"])))
        finally:
            await asyncio.to_thread(milvus.close)
            await database.close()
        return
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
        asyncio.run(_process_event(OutboxEventType(event_type), payload))
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


# 每两秒扫描一次
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


async def _recover_stale_wiki_jobs() -> int:
    database = DatabaseRuntime(settings.database_url.get_secret_value())
    try:
        return await WikiProcessor.recover_stale(database.session_factory)
    finally:
        await database.close()


@app.task(name="agent_api.knowledge.recover_stale_wiki_jobs")  # type: ignore[untyped-decorator]
def recover_stale_wiki_jobs() -> int:
    """周期恢复 Wiki Worker 崩溃后遗留的任务。"""

    return asyncio.run(_recover_stale_wiki_jobs())


async def _recover_stale_wiki_index_jobs() -> int:
    database = DatabaseRuntime(settings.database_url.get_secret_value())
    try:
        return await recover_stale_page_index_jobs(database.session_factory)
    finally:
        await database.close()


@app.task(name="agent_api.knowledge.recover_stale_wiki_index_jobs")  # type: ignore[untyped-decorator]
def recover_stale_wiki_index_jobs() -> int:
    """每分钟恢复心跳超过五分钟的 Wiki 页面索引任务。"""

    return asyncio.run(_recover_stale_wiki_index_jobs())
