import json
import logging
from dataclasses import dataclass
from uuid import UUID

import pytest

from agent_api.knowledge.domain.events import OutboxEventType
from agent_api.knowledge.infrastructure.jobs.outbox import OutboxDispatcher, PendingEvent


@dataclass
class Repository:
    event: PendingEvent
    failed_reference: str | None = None

    async def claim_pending(self, limit: int) -> list[PendingEvent]:
        return [self.event]

    async def mark_published(self, event_id: UUID) -> None:
        return None

    async def mark_failed(self, event_id: UUID, error_reference: str) -> None:
        self.failed_reference = error_reference


class SecretFailurePublisher:
    async def publish(self, event: PendingEvent) -> None:
        raise ConnectionError("redis://password@host/秘密正文")


async def test_outbox_failure_log_contains_only_opaque_identifiers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    event_id = UUID("40000000-0000-0000-0000-000000000001")
    event = PendingEvent(
        event_id,
        OutboxEventType.DOCUMENT_INGESTION_REQUESTED,
        UUID("50000000-0000-0000-0000-000000000001"),
        {"body": "秘密正文", "password": "password"},
    )
    repository = Repository(event)
    with caplog.at_level(logging.INFO, logger="agent_api.knowledge"):
        await OutboxDispatcher(repository, SecretFailurePublisher()).dispatch_once()
    records = [json.loads(record.message) for record in caplog.records]
    assert records == [
        {
            "event": "outbox_publish_failed",
            "event_id": str(event_id),
            "error_reference": repository.failed_reference,
        }
    ]
    assert "password" not in caplog.text
    assert "秘密正文" not in caplog.text
