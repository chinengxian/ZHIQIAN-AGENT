from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from agent_api.knowledge.domain.events import OutboxEventType
from agent_api.knowledge.infrastructure.database.models import (
    WikiJob,
    WikiPage,
    WikiPageVersion,
)
from agent_api.knowledge.wiki.index_jobs import (
    SqlAlchemyWikiIndexRepository,
    apply_page_index_setup_failure,
    retry_page_index_job,
    mark_and_schedule_page_index,
    schedule_current_page_indexes,
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


@pytest.mark.parametrize(
    ("index_enabled", "origin", "review_state", "expected"),
    [
        (False, "pipeline", "published", "not_required"),
        (True, "pipeline", "pending_review", "not_required"),
        (True, "user", "published", "not_required"),
    ],
)
async def test_non_indexable_publish_states_remain_not_required(
    index_enabled: bool,
    origin: str,
    review_state: str,
    expected: str,
) -> None:
    page = WikiPage(
        id=UUID("81000000-0000-0000-0000-000000000004"),
        knowledge_base_id=KB_ID,
        slug="summary/doc",
        page_type="summary",
        title="规范",
        current_version_id=VERSION_ID,
    )
    version = WikiPageVersion(
        id=VERSION_ID,
        page_id=page.id,
        version_no=1,
        title="规范",
        content="正文",
        summary="摘要",
        origin=origin,
        review_state=review_state,
        index_state="pending",
    )
    session = _ScheduleSession(JOB_ID)

    result = await mark_and_schedule_page_index(
        session,  # type: ignore[arg-type]
        page=page,
        version=version,
        index_enabled=index_enabled,
        fingerprint="fp-v1",
    )

    assert result is None
    assert version.index_state == expected
    assert session.added == []


def test_retry_delay_is_capped_and_uses_attempt_count() -> None:
    assert SqlAlchemyWikiIndexRepository.retry_delay(1) == 2
    assert SqlAlchemyWikiIndexRepository.retry_delay(8) == 256
    assert SqlAlchemyWikiIndexRepository.retry_delay(99) == 256


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


async def test_repeated_backfill_keeps_ready_version_ready() -> None:
    page = WikiPage(
        id=UUID("81000000-0000-0000-0000-000000000004"),
        knowledge_base_id=KB_ID,
        slug="summary/doc",
        page_type="summary",
        title="规范",
        current_version_id=VERSION_ID,
    )
    version = WikiPageVersion(
        id=VERSION_ID,
        page_id=page.id,
        version_no=1,
        title="规范",
        content="正文",
        summary="摘要",
        origin="pipeline",
        review_state="published",
        index_state="ready",
        embedding_fingerprint="fp-v1",
    )

    class _Result:
        def __iter__(self):
            return iter(((page, version),))

    class _Session:
        async def execute(self, statement: object) -> _Result:
            del statement
            return _Result()

        async def scalar(self, statement: object) -> int:
            del statement
            return 1

    result = await schedule_current_page_indexes(
        _Session(),  # type: ignore[arg-type]
        knowledge_base_id=KB_ID,
        index_enabled=True,
        fingerprint="fp-v1",
    )

    assert result == (0, 1, 0)
    assert version.index_state == "ready"


async def test_backfill_creates_two_grounded_jobs_skips_manual_and_keeps_tokens() -> None:
    versions: list[tuple[WikiPage, WikiPageVersion]] = []
    for index, origin in enumerate(("pipeline", "revert", "user"), start=1):
        page_id = UUID(f"82000000-0000-0000-0000-{index:012d}")
        version_id = UUID(f"83000000-0000-0000-0000-{index:012d}")
        page = WikiPage(
            id=page_id,
            knowledge_base_id=KB_ID,
            slug=f"summary/{index}",
            page_type="summary",
            title=f"规范{index}",
            current_version_id=version_id,
        )
        version = WikiPageVersion(
            id=version_id,
            page_id=page_id,
            version_no=1,
            title=page.title,
            content="正文",
            summary="摘要",
            origin=origin,
            review_state="published",
            index_state="not_required",
        )
        versions.append((page, version))

    class _Result:
        def __iter__(self):
            return iter(versions)

    class _Session:
        def __init__(self, values: list[object]) -> None:
            self.values = iter(values)
            self.added: list[object] = []

        async def execute(self, statement: object) -> _Result:
            del statement
            return _Result()

        async def scalar(self, statement: object) -> object:
            del statement
            return next(self.values)

        def add(self, value: object) -> None:
            self.added.append(value)

    tokens_charged = 1234
    first = _Session([1, JOB_ID, 1, UUID("81000000-0000-0000-0000-000000000005")])
    first_result = await schedule_current_page_indexes(
        first,  # type: ignore[arg-type]
        knowledge_base_id=KB_ID,
        index_enabled=True,
        fingerprint="fp-v1",
    )
    second = _Session([1, 1])
    second_result = await schedule_current_page_indexes(
        second,  # type: ignore[arg-type]
        knowledge_base_id=KB_ID,
        index_enabled=True,
        fingerprint="fp-v1",
    )

    assert first_result == (2, 0, 1)
    assert second_result == (0, 2, 1)
    assert tokens_charged == 1234
    assert len(first.added) == 2
    assert second.added == []


async def test_non_executable_job_releases_locked_session() -> None:
    page = WikiPage(
        id=UUID("81000000-0000-0000-0000-000000000004"),
        knowledge_base_id=KB_ID,
        slug="summary/doc",
        page_type="summary",
        title="规范",
        current_version_id=VERSION_ID,
    )
    version = WikiPageVersion(
        id=VERSION_ID,
        page_id=page.id,
        version_no=1,
        title="规范",
        content="正文",
        summary="摘要",
        origin="pipeline",
        review_state="published",
        index_state="ready",
    )
    job = WikiJob(
        id=JOB_ID,
        knowledge_base_id=KB_ID,
        page_version_id=VERSION_ID,
        job_type="page_index",
        status="succeeded",
        stage="completed",
        progress=100,
        attempt_count=1,
        max_attempts=5,
        reserved_tokens=0,
        payload={},
        idempotency_key=f"wiki-index:{VERSION_ID}:fp-v1",
    )

    class _Result:
        def one_or_none(self):
            return job, page, version

    class _Session:
        closed = False
        rolled_back = False

        async def begin(self) -> None:
            return None

        async def execute(self, statement: object) -> _Result:
            del statement
            return _Result()

        async def rollback(self) -> None:
            self.rolled_back = True

        async def close(self) -> None:
            self.closed = True

    session = _Session()
    repository = SqlAlchemyWikiIndexRepository(
        lambda: session,  # type: ignore[arg-type]
        embedding_dimension=3,
    )

    assert await repository.lock_job_and_version(JOB_ID) is None
    assert session.rolled_back is True
    assert session.closed is True


@pytest.mark.parametrize("attempt", [1, 5, 20])
def test_retry_time_does_not_exceed_five_minutes(attempt: int) -> None:
    now = datetime.now(UTC)
    delay = SqlAlchemyWikiIndexRepository.retry_delay(attempt)
    assert now + timedelta(seconds=delay) <= now + timedelta(minutes=5)


def test_setup_failure_schedules_retry_without_exposing_exception_text() -> None:
    version = WikiPageVersion(
        id=VERSION_ID,
        page_id=UUID("81000000-0000-0000-0000-000000000004"),
        version_no=1,
        title="标题",
        content="正文",
        summary="摘要",
        origin="pipeline",
        review_state="published",
        index_state="pending",
    )
    job = WikiJob(
        id=JOB_ID,
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

    event = apply_page_index_setup_failure(
        job,
        version,
        stage="milvus_setup",
        error=RuntimeError("正文与secret都不能进入错误"),
        now=datetime(2026, 10, 8, tzinfo=UTC),
    )

    assert job.attempt_count == 1
    assert job.status == "retry_wait"
    assert version.index_state == "failed"
    assert job.error_code == "wiki_index_worker_setup_failed"
    assert "正文" not in (job.error_reference or "")
    assert "secret" not in (job.error_reference or "")
    assert event is not None
    assert event.payload == {"job_id": str(JOB_ID)}
    assert event.available_at == datetime(2026, 10, 8, tzinfo=UTC) + timedelta(seconds=2)


def test_fifth_setup_failure_is_terminal_without_retry_event() -> None:
    version = WikiPageVersion(
        id=VERSION_ID,
        page_id=UUID("81000000-0000-0000-0000-000000000004"),
        version_no=1,
        title="标题",
        content="正文",
        summary="摘要",
        origin="pipeline",
        review_state="published",
        index_state="failed",
    )
    job = WikiJob(
        id=JOB_ID,
        knowledge_base_id=KB_ID,
        page_version_id=VERSION_ID,
        job_type="page_index",
        status="retry_wait",
        stage="failed",
        progress=0,
        attempt_count=4,
        max_attempts=5,
        reserved_tokens=0,
        payload={},
        idempotency_key=f"wiki-index:{VERSION_ID}:fp-v1",
    )

    event = apply_page_index_setup_failure(
        job,
        version,
        stage="embedding_setup",
        error=RuntimeError("do-not-leak"),
        now=datetime(2026, 10, 8, tzinfo=UTC),
    )

    assert job.attempt_count == 5
    assert job.status == "failed"
    assert event is None


async def test_retry_with_new_fingerprint_creates_new_consistent_job() -> None:
    old = WikiJob(
        id=JOB_ID,
        knowledge_base_id=KB_ID,
        page_version_id=VERSION_ID,
        embedding_fingerprint="old-fp",
        job_type="page_index",
        status="failed",
        stage="failed",
        progress=0,
        attempt_count=5,
        max_attempts=5,
        reserved_tokens=0,
        payload={},
        idempotency_key=f"wiki-index:{VERSION_ID}:old-fp",
    )
    new_id = UUID("81000000-0000-0000-0000-000000000006")
    session = _ScheduleSession(new_id)

    result = await retry_page_index_job(
        session,  # type: ignore[arg-type]
        knowledge_base_id=KB_ID,
        page_version_id=VERSION_ID,
        fingerprint="new-fp",
        failed_job=old,
    )

    assert result == new_id
    assert old.embedding_fingerprint == "old-fp"
    assert old.idempotency_key == f"wiki-index:{VERSION_ID}:old-fp"


async def test_retry_with_existing_new_fingerprint_returns_exact_job() -> None:
    old = WikiJob(
        id=JOB_ID,
        knowledge_base_id=KB_ID,
        page_version_id=VERSION_ID,
        embedding_fingerprint="old-fp",
        job_type="page_index",
        status="failed",
        stage="failed",
        progress=0,
        attempt_count=5,
        max_attempts=5,
        reserved_tokens=0,
        payload={},
        idempotency_key=f"wiki-index:{VERSION_ID}:old-fp",
    )
    existing_id = UUID("81000000-0000-0000-0000-000000000007")

    class _Session(_ScheduleSession):
        def __init__(self) -> None:
            super().__init__(None)
            self.calls = 0

        async def scalar(self, statement: object) -> UUID | None:
            self.statements.append(statement)
            self.calls += 1
            return None if self.calls == 1 else existing_id

    session = _Session()
    result = await retry_page_index_job(
        session,  # type: ignore[arg-type]
        knowledge_base_id=KB_ID,
        page_version_id=VERSION_ID,
        fingerprint="new-fp",
        failed_job=old,
    )

    assert result == existing_id
    lookup_sql = str(session.statements[-1].compile()).lower()
    assert "idempotency_key" in lookup_sql
