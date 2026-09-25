import json
import logging
from typing import Literal
from uuid import UUID

logger = logging.getLogger("agent_api.knowledge")


def record_event(
    event: Literal[
        "outbox_published",
        "outbox_publish_failed",
        "job_requeued",
        "job_retry_exhausted",
        "job_already_activated",
    ],
    identifier: UUID,
    *,
    error_reference: str | None = None,
) -> None:
    """只记录固定事件名、UUID 和不透明错误引用，不接收异常或正文。"""

    key = "event_id" if event.startswith("outbox_") else "job_id"
    fields = {"event": event, key: str(identifier)}
    if error_reference is not None:
        fields["error_reference"] = error_reference
    logger.info(json.dumps(fields, separators=(",", ":")))
