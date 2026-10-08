import importlib
from uuid import UUID, uuid4

import pytest
from celery.exceptions import Retry
from pydantic import AnyHttpUrl, SecretStr

from agent_api.core.config import ModelProvider, Settings
from agent_api.knowledge.domain.events import OutboxEventType
from agent_api.knowledge.worker.celery_factory import create_celery_app
from agent_api.knowledge.worker.runtime import handle_event

JOB_ID = UUID("90000000-0000-0000-0000-000000000001")
DOCUMENT_ID = UUID("90000000-0000-0000-0000-000000000002")
VERSION_ID = UUID("90000000-0000-0000-0000-000000000003")
KB_ID = UUID("90000000-0000-0000-0000-000000000004")


class RecordingPipeline:
    def __init__(self) -> None:
        self.jobs: list[UUID] = []

    async def run(self, job_id: UUID) -> None:
        self.jobs.append(job_id)


class RecordingCleanup:
    def __init__(self) -> None:
        self.calls: list[tuple[str, UUID, UUID | None]] = []

    async def delete_document(self, document_id: UUID, job_id: UUID) -> None:
        self.calls.append(("document", document_id, job_id))

    async def cleanup_version(self, version_id: UUID) -> None:
        self.calls.append(("version", version_id, None))

    async def delete_knowledge_base(self, knowledge_base_id: UUID) -> None:
        self.calls.append(("knowledge_base", knowledge_base_id, None))


async def test_worker_routes_ingestion_event_to_idempotent_job() -> None:
    pipeline = RecordingPipeline()

    await handle_event(
        OutboxEventType.DOCUMENT_INGESTION_REQUESTED,
        {"job_id": str(JOB_ID)},
        pipeline,  # type: ignore[arg-type]
    )

    assert pipeline.jobs == [JOB_ID]


async def test_worker_rejects_unknown_or_malformed_events() -> None:
    pipeline = RecordingPipeline()

    with pytest.raises(ValueError, match="unsupported_event_type"):
        await handle_event("unknown", {}, pipeline)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="invalid_job_id"):
        await handle_event(
            OutboxEventType.DOCUMENT_INGESTION_REQUESTED,
            {"job_id": "invalid"},
            pipeline,  # type: ignore[arg-type]
        )


async def test_worker_routes_compensating_cleanup_events() -> None:
    pipeline = RecordingPipeline()
    cleanup = RecordingCleanup()

    await handle_event(
        OutboxEventType.DOCUMENT_DELETION_REQUESTED,
        {"document_id": str(DOCUMENT_ID), "job_id": str(JOB_ID)},
        pipeline,  # type: ignore[arg-type]
        cleanup,  # type: ignore[arg-type]
    )
    await handle_event(
        OutboxEventType.DOCUMENT_VERSION_CLEANUP_REQUESTED,
        {"document_version_id": str(VERSION_ID)},
        pipeline,  # type: ignore[arg-type]
        cleanup,  # type: ignore[arg-type]
    )
    await handle_event(
        OutboxEventType.KNOWLEDGE_BASE_DELETION_REQUESTED,
        {"knowledge_base_id": str(KB_ID)},
        pipeline,  # type: ignore[arg-type]
        cleanup,  # type: ignore[arg-type]
    )

    assert cleanup.calls == [
        ("document", DOCUMENT_ID, JOB_ID),
        ("version", VERSION_ID, None),
        ("knowledge_base", KB_ID, None),
    ]


def test_celery_factory_uses_json_and_schedules_outbox_dispatch() -> None:
    settings = Settings(
        model_provider=ModelProvider.OPENAI,
        openai_base_url=AnyHttpUrl("https://example.test/v1"),
        openai_api_key=SecretStr("chat-secret"),
        openai_model="chat-model",
        redis_url=SecretStr("redis://:queue-secret@127.0.0.1:6379/0"),
        _env_file=None,  # type: ignore[call-arg]
    )

    app = create_celery_app(settings)

    assert app.conf.task_serializer == "json"
    assert app.conf.accept_content == ["json"]
    assert app.conf.task_acks_late is True
    assert app.conf.task_soft_time_limit == 1500
    assert app.conf.task_time_limit == 1800
    assert app.conf.worker_concurrency == 2
    assert app.conf.worker_max_memory_per_child == 1_048_576
    assert app.conf.beat_schedule["dispatch-knowledge-outbox"]["task"] == (
        "agent_api.knowledge.dispatch_outbox"
    )
    assert app.conf.beat_schedule["recover-stale-ingestion-jobs"]["task"] == (
        "agent_api.knowledge.recover_stale_jobs"
    )
    assert app.conf.beat_schedule["recover-stale-wiki-index-jobs"]["task"] == (
        "agent_api.knowledge.recover_stale_wiki_index_jobs"
    )
    assert app.conf.beat_schedule["recover-stale-wiki-index-jobs"]["schedule"] == 60.0
    # 日志应记录 Celery 应用身份而不是展开包含 broker 凭据的内部配置对象。
    assert "queue-secret" not in repr(app)


async def test_wiki_worker_assembles_index_and_delete_events_without_deleting_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """索引事件装配完整服务；删除事件只清 Milvus 单元并关闭资源。"""

    worker_settings = Settings(
        model_provider=ModelProvider.OPENAI,
        openai_base_url=AnyHttpUrl("https://example.test/v1"),
        openai_api_key=SecretStr("test-secret"),
        openai_model="test-model",
        _env_file=None,  # type: ignore[call-arg]
    )
    monkeypatch.setattr("agent_api.core.config.get_settings", lambda: worker_settings)
    module = importlib.import_module("agent_api.knowledge.worker.celery_app")
    calls: list[tuple[str, UUID | None]] = []

    class _Database:
        session_factory = object()

        def __init__(self, url: str) -> None:
            del url
            calls.append(("database_open", None))

        async def close(self) -> None:
            calls.append(("database_close", None))

    class _Milvus:
        def __init__(self, **kwargs: str) -> None:
            del kwargs
            calls.append(("milvus_open", None))

        def close(self) -> None:
            calls.append(("milvus_close", None))

    class _Index:
        def __init__(self, client: object, **kwargs: str) -> None:
            del client, kwargs

        async def delete_page_version(self, version_id: UUID) -> None:
            calls.append(("delete", version_id))

    class _Repository:
        def __init__(self, sessions: object, *, embedding_dimension: int) -> None:
            del sessions, embedding_dimension

    class _Service:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        async def run(self, job_id: UUID) -> None:
            calls.append(("run", job_id))

    monkeypatch.setattr(module, "DatabaseRuntime", _Database)
    monkeypatch.setattr(module, "MilvusClient", _Milvus)
    monkeypatch.setattr(module, "MilvusWikiIndex", _Index)
    monkeypatch.setattr(module, "SqlAlchemyWikiIndexRepository", _Repository)
    monkeypatch.setattr(module, "WikiIndexService", _Service)
    monkeypatch.setattr(module, "create_embedding_adapter", lambda _: object())

    await module._process_event(
        OutboxEventType.WIKI_PAGE_INDEX_REQUESTED,
        {"job_id": str(JOB_ID)},
    )
    await module._process_event(
        OutboxEventType.WIKI_PAGE_INDEX_DELETE_REQUESTED,
        {"page_version_id": str(VERSION_ID)},
    )

    async def _recover(_: object) -> int:
        calls.append(("recover", None))
        return 2

    monkeypatch.setattr(module, "recover_stale_page_index_jobs", _recover)
    assert await module._recover_stale_wiki_index_jobs() == 2

    assert ("run", JOB_ID) in calls
    assert ("delete", VERSION_ID) in calls
    assert ("recover", None) in calls
    assert calls.count(("database_close", None)) == 3
    assert calls.count(("milvus_close", None)) == 2


async def test_wiki_index_setup_failures_are_persisted_and_resources_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_settings = Settings(
        model_provider=ModelProvider.OPENAI,
        openai_base_url=AnyHttpUrl("https://example.test/v1"),
        openai_api_key=SecretStr("test-secret"),
        openai_model="test-model",
        _env_file=None,  # type: ignore[call-arg]
    )
    monkeypatch.setattr("agent_api.core.config.get_settings", lambda: worker_settings)
    module = importlib.import_module("agent_api.knowledge.worker.celery_app")
    calls: list[str] = []

    class _Database:
        session_factory = object()

        def __init__(self, url: str) -> None:
            del url

        async def close(self) -> None:
            calls.append("database_close")

    class _BrokenMilvus:
        def __init__(self, **kwargs: str) -> None:
            del kwargs
            raise RuntimeError("secret-milvus-error")

    async def _record(
        sessions: object,
        job_id: UUID,
        *,
        stage: str,
        error: BaseException,
    ) -> None:
        del sessions, job_id, error
        calls.append(f"record:{stage}")

    monkeypatch.setattr(module, "DatabaseRuntime", _Database)
    monkeypatch.setattr(module, "MilvusClient", _BrokenMilvus)
    monkeypatch.setattr(module, "record_page_index_setup_failure", _record)

    await module._process_event(
        OutboxEventType.WIKI_PAGE_INDEX_REQUESTED,
        {"job_id": str(JOB_ID)},
    )

    assert calls == ["record:milvus_setup", "database_close"]

    class _Milvus:
        def __init__(self, **kwargs: str) -> None:
            del kwargs

        def close(self) -> None:
            calls.append("milvus_close")

    monkeypatch.setattr(module, "MilvusClient", _Milvus)
    monkeypatch.setattr(
        module,
        "create_embedding_adapter",
        lambda _: (_ for _ in ()).throw(RuntimeError("secret-embedding-error")),
    )
    await module._process_event(
        OutboxEventType.WIKI_PAGE_INDEX_REQUESTED,
        {"job_id": str(JOB_ID)},
    )

    assert "record:embedding_setup" in calls
    assert calls.count("database_close") == 2
    assert "milvus_close" in calls


async def test_wiki_delete_failure_is_retried_and_database_failure_uses_celery_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_settings = Settings(
        model_provider=ModelProvider.OPENAI,
        openai_base_url=AnyHttpUrl("https://example.test/v1"),
        openai_api_key=SecretStr("test-secret"),
        openai_model="test-model",
        _env_file=None,  # type: ignore[call-arg]
    )
    monkeypatch.setattr("agent_api.core.config.get_settings", lambda: worker_settings)
    module = importlib.import_module("agent_api.knowledge.worker.celery_app")
    calls: list[str] = []

    class _Database:
        session_factory = object()

        def __init__(self, url: str) -> None:
            del url

        async def close(self) -> None:
            calls.append("database_close")

    class _Milvus:
        def __init__(self, **kwargs: str) -> None:
            del kwargs

        def close(self) -> None:
            calls.append("milvus_close")

    class _BrokenDeleteIndex:
        def __init__(self, client: object, **kwargs: str) -> None:
            del client, kwargs

        async def delete_page_version(self, version_id: UUID) -> None:
            del version_id
            raise RuntimeError("secret-delete-error")

    monkeypatch.setattr(module, "DatabaseRuntime", _Database)
    monkeypatch.setattr(module, "MilvusClient", _Milvus)
    monkeypatch.setattr(module, "MilvusWikiIndex", _BrokenDeleteIndex)
    with pytest.raises(module.WikiIndexWorkerError, match="wiki_index_delete_failed"):
        await module._process_event(
            OutboxEventType.WIKI_PAGE_INDEX_DELETE_REQUESTED,
            {"page_version_id": str(VERSION_ID)},
        )
    assert calls == ["milvus_close", "database_close"]

    def _run_and_fail(coroutine: object) -> None:
        coroutine.close()  # type: ignore[attr-defined]
        raise module.WikiIndexWorkerError("wiki_index_database_unavailable")

    monkeypatch.setattr(module.asyncio, "run", _run_and_fail)
    with pytest.raises(Retry):
        module.process_event.run(
            event_id=str(uuid4()),
            event_type=OutboxEventType.WIKI_PAGE_INDEX_REQUESTED.value,
            aggregate_id=str(JOB_ID),
            payload={"job_id": str(JOB_ID)},
        )
