from dataclasses import dataclass
from uuid import UUID

from agent_api.knowledge.infrastructure.jobs.outbox import OutboxDispatcher, PendingEvent

EVENT_ID = UUID("40000000-0000-0000-0000-000000000001")


@dataclass
class FakeOutboxRepository:
    events: list[PendingEvent]
    published: list[UUID]
    failed: list[tuple[UUID, str]]

    async def claim_pending(self, limit: int) -> list[PendingEvent]:
        return self.events[:limit]

    async def mark_published(self, event_id: UUID) -> None:
        self.published.append(event_id)

    async def mark_failed(self, event_id: UUID, error_reference: str) -> None:
        self.failed.append((event_id, error_reference))


class RecordingPublisher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[PendingEvent] = []

    async def publish(self, event: PendingEvent) -> None:
        self.messages.append(event)
        if self.fail:
            raise RuntimeError("redis://secret@host/internal")


def pending_event() -> PendingEvent:
    return PendingEvent(
        id=EVENT_ID,
        event_type="document.ingestion.requested",
        aggregate_id=UUID("50000000-0000-0000-0000-000000000001"),
        payload={"job_id": "safe-id"},
    )


async def test_dispatcher_publishes_then_marks_event() -> None:
    repository = FakeOutboxRepository([pending_event()], [], [])
    publisher = RecordingPublisher()

    count = await OutboxDispatcher(repository, publisher).dispatch_once()

    assert count == 1
    assert publisher.messages == [pending_event()]
    assert repository.published == [EVENT_ID]
    assert repository.failed == []


async def test_dispatcher_records_opaque_reference_without_exception_text() -> None:
    repository = FakeOutboxRepository([pending_event()], [], [])
    publisher = RecordingPublisher(fail=True)

    count = await OutboxDispatcher(repository, publisher).dispatch_once()

    assert count == 0
    assert repository.published == []
    assert repository.failed[0][0] == EVENT_ID
    assert repository.failed[0][1].startswith("outbox-")
    assert "secret" not in repository.failed[0][1]
