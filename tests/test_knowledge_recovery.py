import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select, update

from agent_api.knowledge.application.ingestion import IngestionAlreadyClaimed
from agent_api.knowledge.application.management import SqlAlchemyKnowledgeManagementService
from agent_api.knowledge.infrastructure.database.models import (
    Document,
    DocumentVersion,
    IngestionJob,
    OutboxEvent,
)
from agent_api.knowledge.infrastructure.database.runtime import DatabaseRuntime
from agent_api.knowledge.infrastructure.jobs.repository import SqlAlchemyIngestionRepository
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage
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


@pytest.mark.parametrize("initial_status", ["pending", "retry_wait"])
async def test_published_but_unconsumed_event_is_recovered_once(
    tmp_path: Path, initial_status: str
) -> None:
    settings = knowledge_settings(tmp_path / "recovery-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="丢失投递恢复", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "lost.txt", "text/plain", "恢复正文".encode()
        )
        old = datetime.now(UTC) - timedelta(minutes=10)
        async with runtime.session_factory() as session, session.begin():
            await session.execute(
                update(IngestionJob)
                .where(IngestionJob.id == accepted["job_id"])
                .values(status=initial_status, updated_at=old, next_retry_at=old)
            )
            event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == accepted["document_version_id"]
                )
            )
            assert event is not None
            event.status = "published"
        repository = SqlAlchemyIngestionRepository(runtime.session_factory, storage)
        recovered = await repository.recover_stale_jobs(
            stale_before=datetime.now(UTC) - timedelta(minutes=5), limit=10
        )
        repeated = await repository.recover_stale_jobs(
            stale_before=datetime.now(UTC) - timedelta(minutes=5), limit=10
        )
        async with runtime.session_factory() as session:
            job = await session.get(IngestionJob, accepted["job_id"])
        assert recovered == [accepted["job_id"]]
        assert repeated == []
        assert job is not None and job.status == "retry_wait" and job.attempt_count == 1
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_exhausted_stale_job_finishes_document_state(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "exhausted-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="重试耗尽恢复", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "exhausted.txt", "text/plain", "恢复正文".encode()
        )
        async with runtime.session_factory() as session, session.begin():
            job = await session.get(IngestionJob, accepted["job_id"])
            assert job is not None
            job.status = "running"
            job.attempt_count = job.max_attempts - 1
            job.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
            document = await session.get(Document, accepted["document_id"])
            assert document is not None
            document.status = "processing"
        repository = SqlAlchemyIngestionRepository(runtime.session_factory, storage)
        await repository.recover_stale_jobs(
            stale_before=datetime.now(UTC) - timedelta(minutes=5), limit=10
        )
        async with runtime.session_factory() as session:
            job = await session.get(IngestionJob, accepted["job_id"])
            document = await session.get(Document, accepted["document_id"])
            version = await session.get(DocumentVersion, accepted["document_version_id"])
        assert job is not None and job.status == "failed" and job.error_code == "retry_exhausted"
        assert document is not None and document.status == "failed"
        assert version is not None and version.status == "failed"
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_unpublished_event_does_not_consume_retry_budget(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "unpublished-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="待投递恢复", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "unpublished.txt", "text/plain", "正文".encode()
        )
        async with runtime.session_factory() as session, session.begin():
            await session.execute(
                update(IngestionJob)
                .where(IngestionJob.id == accepted["job_id"])
                .values(updated_at=datetime.now(UTC) - timedelta(minutes=10))
            )
        repository = SqlAlchemyIngestionRepository(runtime.session_factory, storage)
        recovered = await repository.recover_stale_jobs(
            stale_before=datetime.now(UTC) - timedelta(minutes=5), limit=10
        )
        async with runtime.session_factory() as session:
            job = await session.get(IngestionJob, accepted["job_id"])
        assert recovered == []
        assert job is not None and job.status == "pending" and job.attempt_count == 0
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_crash_after_activation_marks_job_succeeded_without_reindex(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "activated-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="激活后恢复", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "activated.txt", "text/plain", "正文".encode()
        )
        async with runtime.session_factory() as session, session.begin():
            document = await session.get(Document, accepted["document_id"])
            version = await session.get(DocumentVersion, accepted["document_version_id"])
            job = await session.get(IngestionJob, accepted["job_id"])
            assert document is not None and version is not None and job is not None
            document.active_version_id = version.id
            document.status = "ready"
            version.status = "ready"
            job.status = "running"
            job.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
        repository = SqlAlchemyIngestionRepository(runtime.session_factory, storage)
        recovered = await repository.recover_stale_jobs(
            stale_before=datetime.now(UTC) - timedelta(minutes=5), limit=10
        )
        async with runtime.session_factory() as session:
            job = await session.get(IngestionJob, accepted["job_id"])
        assert recovered == []
        assert job is not None and job.status == "succeeded" and job.progress == 100
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_duplicate_delivery_cannot_claim_running_job(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path / "claim-uploads")
    runtime = DatabaseRuntime(DATABASE_URL)
    storage = LocalFileStorage(settings.storage_root)
    management = SqlAlchemyKnowledgeManagementService(runtime.session_factory, storage, settings)
    knowledge_base_id: UUID | None = None
    try:
        created = await management.create_knowledge_base(name="重复投递领取", description="")
        knowledge_base_id = created["id"]
        accepted = await management.upload_document(
            knowledge_base_id, "claim.txt", "text/plain", "正文".encode()
        )
        repository = SqlAlchemyIngestionRepository(runtime.session_factory, storage)
        context = await repository.load_context(accepted["job_id"])
        assert context.job_id == accepted["job_id"]
        with pytest.raises(IngestionAlreadyClaimed):
            await repository.load_context(accepted["job_id"])
        async with runtime.session_factory() as session:
            job = await session.get(IngestionJob, accepted["job_id"])
        assert job is not None and job.status == "running" and job.heartbeat_at is not None
    finally:
        if knowledge_base_id is not None:
            await cleanup_knowledge_base(runtime, knowledge_base_id)
        await runtime.close()


async def test_late_message_for_deleted_job_is_acknowledged(tmp_path: Path) -> None:
    runtime = DatabaseRuntime(DATABASE_URL)
    repository = SqlAlchemyIngestionRepository(
        runtime.session_factory, LocalFileStorage(tmp_path / "late-uploads")
    )
    try:
        with pytest.raises(IngestionAlreadyClaimed):
            await repository.load_context(UUID(int=999999))
    finally:
        await runtime.close()
