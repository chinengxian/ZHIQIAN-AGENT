import json
import logging
from typing import Any, Literal, Protocol
from uuid import UUID

logger = logging.getLogger("agent_api.knowledge")


class WikiShadowMetricsLike(Protocol):
    @property
    def query_fingerprint(self) -> str: ...

    @property
    def legacy_page_ids(self) -> tuple[UUID, ...]: ...

    @property
    def independent_page_ids(self) -> tuple[UUID, ...]: ...

    @property
    def legacy_claim_ids(self) -> tuple[UUID, ...]: ...

    @property
    def independent_claim_ids(self) -> tuple[UUID, ...]: ...

    @property
    def legacy_elapsed_ms(self) -> float | None: ...

    @property
    def independent_elapsed_ms(self) -> float | None: ...

    @property
    def legacy_no_answer(self) -> bool: ...

    @property
    def independent_no_answer(self) -> bool | None: ...

    @property
    def independent_degradation_reason(self) -> str | None: ...


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


def record_wiki_search(
    *,
    query_fingerprint: str,
    latency_ms: float,
    dense_hit_count: int,
    keyword_hit_count: int,
    alias_hit_count: int,
    page_count: int,
    no_answer: bool,
    threshold_filtered_count: int,
    reranker_degraded: bool,
    degradation_reason: str | None,
    index_backlog_count: int = 0,
    cursor_expired: bool = False,
) -> None:
    """记录 Wiki 搜索聚合指标；调用方不得传入查询原文或正文。"""

    fields: dict[str, Any] = {
        "event": "wiki_search",
        "query_fingerprint": query_fingerprint,
        "latency_ms": latency_ms,
        "dense_hit_count": dense_hit_count,
        "keyword_hit_count": keyword_hit_count,
        "alias_hit_count": alias_hit_count,
        "page_count": page_count,
        "no_answer": no_answer,
        "threshold_filtered_count": threshold_filtered_count,
        "reranker_degraded": reranker_degraded,
        "index_backlog_count": index_backlog_count,
        "cursor_expired": cursor_expired,
    }
    if degradation_reason is not None:
        fields["degradation_reason"] = degradation_reason
    logger.info(json.dumps(fields, separators=(",", ":")))


def record_wiki_shadow_diff(metrics: WikiShadowMetricsLike) -> None:
    """记录 shadow 对比差异，只输出指纹、ID、耗时和降级状态。"""

    fields = {
        "event": "wiki_shadow_diff",
        "query_fingerprint": metrics.query_fingerprint,
        "legacy_page_ids": [str(value) for value in metrics.legacy_page_ids],
        "independent_page_ids": [str(value) for value in metrics.independent_page_ids],
        "legacy_claim_ids": [str(value) for value in metrics.legacy_claim_ids],
        "independent_claim_ids": [str(value) for value in metrics.independent_claim_ids],
        "legacy_elapsed_ms": metrics.legacy_elapsed_ms,
        "independent_elapsed_ms": metrics.independent_elapsed_ms,
        "legacy_no_answer": metrics.legacy_no_answer,
        "independent_no_answer": metrics.independent_no_answer,
    }
    degradation_reason = metrics.independent_degradation_reason
    if degradation_reason is not None:
        fields["independent_degradation_reason"] = degradation_reason
    logger.info(json.dumps(fields, separators=(",", ":")))
