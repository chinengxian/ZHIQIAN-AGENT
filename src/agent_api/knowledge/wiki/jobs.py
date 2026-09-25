from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.knowledge.domain.events import OutboxEventType
from agent_api.knowledge.infrastructure.database.models import (
    OutboxEvent,
    WikiClaim,
    WikiClaimSource,
    WikiConfig,
    WikiJob,
)

WIKI_PIPELINE_VERSION = "wiki-v1"


async def schedule_document_wiki(
    session: AsyncSession, knowledge_base_id: UUID, document_version_id: UUID
) -> UUID | None:
    """在来源事务中幂等创建 Wiki 任务和 outbox 事件。"""

    config = await session.get(WikiConfig, knowledge_base_id)
    if config is None or not config.enabled:
        return None
    key = f"wiki:{document_version_id}:{WIKI_PIPELINE_VERSION}"
    job_id = uuid4()
    inserted = await session.scalar(
        pg_insert(WikiJob)
        .values(
            id=job_id,
            knowledge_base_id=knowledge_base_id,
            document_version_id=document_version_id,
            job_type="document_generate",
            status="pending",
            stage="pending",
            progress=0,
            attempt_count=0,
            max_attempts=3,
            reserved_tokens=0,
            idempotency_key=key,
        )
        .on_conflict_do_nothing(index_elements=[WikiJob.idempotency_key])
        .returning(WikiJob.id)
    )
    if inserted is None:
        return None
    session.add(
        OutboxEvent(
            event_key=key,
            event_type=OutboxEventType.WIKI_DOCUMENT_GENERATE_REQUESTED,
            aggregate_id=inserted,
            payload={"job_id": str(inserted)},
        )
    )
    return inserted


async def invalidate_document_claims(session: AsyncSession, document_id: UUID) -> int:
    """原文退出活动状态前使相关 Wiki 结论失去可信标记。"""

    claim_ids = select(WikiClaimSource.claim_id).where(WikiClaimSource.document_id == document_id)
    result = await session.execute(
        update(WikiClaim)
        .where(WikiClaim.id.in_(claim_ids), WikiClaim.trust_state == "verified")
        .values(trust_state="needs_review", reason="source_not_current")
    )
    return int(getattr(result, "rowcount", 0) or 0)
