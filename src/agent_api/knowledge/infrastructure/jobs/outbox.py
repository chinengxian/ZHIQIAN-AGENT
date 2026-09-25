from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from celery import Celery  # type: ignore[import-untyped]
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_api.knowledge.domain.events import OutboxEventType
from agent_api.knowledge.domain.statuses import OutboxStatus
from agent_api.knowledge.infrastructure.database.models import OutboxEvent
from agent_api.knowledge.infrastructure.observability.events import record_event


@dataclass(frozen=True, slots=True)
class PendingEvent:
    id: UUID
    event_type: OutboxEventType
    aggregate_id: UUID
    payload: dict[str, Any]


class OutboxRepository(Protocol):
    async def claim_pending(self, limit: int) -> list[PendingEvent]: ...

    async def mark_published(self, event_id: UUID) -> None: ...

    async def mark_failed(self, event_id: UUID, error_reference: str) -> None: ...


class EventPublisher(Protocol):
    async def publish(self, event: PendingEvent) -> None: ...


class OutboxDispatcher:
    """从持久化 outbox 向队列做至少一次投递。"""

    def __init__(self, repository: OutboxRepository, publisher: EventPublisher) -> None:
        self._repository = repository
        self._publisher = publisher

    async def dispatch_once(self, *, limit: int = 100) -> int:
        published = 0
        for event in await self._repository.claim_pending(limit):
            try:
                await self._publisher.publish(event)
            except Exception:
                reference = f"outbox-{uuid4()}"
                await self._repository.mark_failed(event.id, reference)
                record_event("outbox_publish_failed", event.id, error_reference=reference)
            else:
                await self._repository.mark_published(event.id)
                record_event("outbox_published", event.id)
                published += 1
        return published


class SqlAlchemyOutboxRepository:
    # 已领取事件在租约到期后可重新领取，覆盖进程崩溃窗口。
    CLAIM_LEASE = timedelta(minutes=2)

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def claim_pending(self, limit: int) -> list[PendingEvent]:
        now = datetime.now(UTC)
        async with self._sessions() as session, session.begin():
            rows = list(
                await session.scalars(
                    select(OutboxEvent)
                    .where(
                        or_(
                            OutboxEvent.status == OutboxStatus.PENDING.value,
                            OutboxEvent.status == OutboxStatus.PROCESSING.value,
                        ),
                        OutboxEvent.available_at <= now,
                    )
                    .order_by(OutboxEvent.available_at, OutboxEvent.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                row.status = OutboxStatus.PROCESSING.value
                row.attempt_count += 1
                row.available_at = now + self.CLAIM_LEASE
            return [
                PendingEvent(
                    row.id,
                    OutboxEventType(row.event_type),
                    row.aggregate_id,
                    row.payload,
                )
                for row in rows
            ]

    async def mark_published(self, event_id: UUID) -> None:
        async with self._sessions() as session, session.begin():
            event = await session.get(OutboxEvent, event_id, with_for_update=True)
            if event is not None:
                event.status = OutboxStatus.PUBLISHED.value
                event.published_at = datetime.now(UTC)
                event.last_error_reference = None

    async def mark_failed(self, event_id: UUID, error_reference: str) -> None:
        async with self._sessions() as session, session.begin():
            event = await session.get(OutboxEvent, event_id, with_for_update=True)
            if event is not None:
                event.status = OutboxStatus.PENDING.value
                event.last_error_reference = error_reference
                delay_seconds = min(300, 2 ** min(event.attempt_count, 8))
                event.available_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)


class CeleryEventPublisher:
    """只传递稳定 ID；正文和密钥不会进入队列消息。"""

    def __init__(self, redis_url: str, *, queue: str = "knowledge") -> None:
        self._app = Celery("agent_api.knowledge", broker=redis_url)
        self._queue = queue
        self._app.conf.update(
            task_serializer="json",
            accept_content=["json"],
            result_backend=None,
        )

    async def publish(self, event: PendingEvent) -> None:
        await asyncio.to_thread(
            self._app.send_task,
            "agent_api.knowledge.process_event",
            kwargs={
                "event_id": str(event.id),
                "event_type": event.event_type,
                "aggregate_id": str(event.aggregate_id),
                "payload": event.payload,
            },
            queue=self._queue,
        )
