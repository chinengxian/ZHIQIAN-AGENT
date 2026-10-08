from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from agent_api.knowledge.domain.events import OutboxEventType
from agent_api.knowledge.infrastructure.database.models import WikiJob, WikiPageVersion
from agent_api.knowledge.wiki.index_jobs import (
    SqlAlchemyWikiIndexRepository,
    schedule_page_index,
)

KB_ID = UUID("81000000-0000-0000-0000-000000000001")
VERSION_ID = UUID("81000000-0000-0000-0000-000000000002")
JOB_ID = UUID("81000000-0000-0000-0000-000000000003")


class _ScheduleSession:
    def __init__(self, inserted: UUID | None) -> None:
        self.inserted = inserted
        self.statements: list[object] = []
        self.added: list[object] = []

    async def scalar(self, statement: object) -> UUID | None:
        self.statements.append(statement)
        return self.inserted

    def add(self, value: object) -> None:
        self.added.append(value)


async def test_schedule_page_index_writes_one_minimal_outbox_event() -> None:
    session = _ScheduleSession(JOB_ID)

    result = await schedule_page_index(
        session,  # type: ignore[arg-type]
        knowledge_base_id=KB_ID,
        page_version_id=VERSION_ID,
        fingerprint="fp-v1",
    )

    assert result == JOB_ID
    event = session.added[0]
    assert event.event_type == OutboxEventType.WIKI_PAGE_INDEX_REQUESTED
    assert event.payload == {"job_id": str(JOB_ID)}
    sql = str(session.statements[0].compile()).lower()
    assert "on conflict" in sql
    assert "wiki-index:" not in repr(event.payload)


async def test_schedule_page_index_duplicate_adds_no_event() -> None:
    session = _ScheduleSession(None)

    result = await schedule_page_index(
        session,  # type: ignore[arg-type]
        knowledge_base_id=KB_ID,
        page_version_id=VERSION_ID,
        fingerprint="fp-v1",
    )

    assert result is None
    assert session.added == []


def test_retry_delay_is_capped_and_uses_attempt_count() -> None:
    assert SqlAlchemyWikiIndexRepository.retry_delay(1) == 2
    assert SqlAlchemyWikiIndexRepository.retry_delay(8) == 256
    assert SqlAlchemyWikiIndexRepository.retry_delay(99) == 300


def test_recovered_indexing_version_remains_indexing() -> None:
    version = WikiPageVersion(
        id=VERSION_ID,
        page_id=UUID("81000000-0000-0000-0000-000000000004"),
        version_no=1,
        title="标题",
        content="正文",
        summary="摘要",
        origin="pipeline",
        review_state="published",
        index_state="indexing",
    )
    job = WikiJob(
        id=JOB_ID,
        knowledge_base_id=KB_ID,
        page_version_id=VERSION_ID,
        job_type="page_index",
        status="running",
        stage="writing",
        progress=70,
        attempt_count=1,
        max_attempts=5,
        reserved_tokens=0,
        heartbeat_at=datetime.now(UTC) - timedelta(minutes=6),
        payload={},
        idempotency_key=f"wiki-index:{VERSION_ID}:fp-v1",
    )

    SqlAlchemyWikiIndexRepository.prepare_stale_recovery(job, version)

    assert job.status == "retry_wait"
    assert job.stage == "writing"
    assert version.index_state == "indexing"


def test_index_job_never_reserves_wiki_tokens() -> None:
    job = WikiJob(
        knowledge_base_id=KB_ID,
        page_version_id=VERSION_ID,
        job_type="page_index",
        status="pending",
        stage="pending",
        progress=0,
        attempt_count=0,
        max_attempts=5,
        reserved_tokens=0,
        payload={},
        idempotency_key=f"wiki-index:{VERSION_ID}:fp-v1",
    )
    assert job.reserved_tokens == 0


@pytest.mark.parametrize("attempt", [1, 5, 20])
def test_retry_time_does_not_exceed_five_minutes(attempt: int) -> None:
    now = datetime.now(UTC)
    delay = SqlAlchemyWikiIndexRepository.retry_delay(attempt)
    assert now + timedelta(seconds=delay) <= now + timedelta(minutes=5)
