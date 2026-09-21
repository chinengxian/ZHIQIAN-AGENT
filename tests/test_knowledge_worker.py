from uuid import UUID

import pytest
from pydantic import AnyHttpUrl, SecretStr

from agent_api.core.config import ModelProvider, Settings
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
        "document.ingestion.requested",
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
            "document.ingestion.requested",
            {"job_id": "invalid"},
            pipeline,  # type: ignore[arg-type]
        )


async def test_worker_routes_compensating_cleanup_events() -> None:
    pipeline = RecordingPipeline()
    cleanup = RecordingCleanup()

    await handle_event(
        "document.deletion.requested",
        {"document_id": str(DOCUMENT_ID), "job_id": str(JOB_ID)},
        pipeline,  # type: ignore[arg-type]
        cleanup,  # type: ignore[arg-type]
    )
    await handle_event(
        "document.version.cleanup.requested",
        {"document_version_id": str(VERSION_ID)},
        pipeline,  # type: ignore[arg-type]
        cleanup,  # type: ignore[arg-type]
    )
    await handle_event(
        "knowledge_base.deletion.requested",
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
    assert app.conf.beat_schedule["dispatch-knowledge-outbox"]["task"] == (
        "agent_api.knowledge.dispatch_outbox"
    )
    assert app.conf.beat_schedule["recover-stale-ingestion-jobs"]["task"] == (
        "agent_api.knowledge.recover_stale_jobs"
    )
    # 日志应记录 Celery 应用身份而不是展开包含 broker 凭据的内部配置对象。
    assert "queue-secret" not in repr(app)
