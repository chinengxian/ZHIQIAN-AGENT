from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from agent_api.knowledge.wiki.ranking import WikiScoredCandidate


def _normalize_fact_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s+", " ", normalized)


def fact_fingerprint(
    text: str,
    *,
    source_chunk_ids: Sequence[UUID],
    active_document_version_ids: Sequence[UUID],
) -> str:
    """生成跨页面事实去重指纹，输入顺序变化不影响结果。"""

    payload = "\n".join(
        (
            _normalize_fact_text(text),
            ",".join(sorted(str(item) for item in source_chunk_ids)),
            ",".join(sorted(str(item) for item in active_document_version_ids)),
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class WikiRelevancePolicy:
    dense_threshold: float
    rerank_threshold: float
    keyword_threshold: float

    def accept(self, candidate: WikiScoredCandidate) -> bool:
        if candidate.exact_metadata_match:
            return True
        if candidate.rerank_score is not None:
            return candidate.rerank_score >= self.rerank_threshold
        dense_ok = (
            candidate.dense_score is not None and candidate.dense_score >= self.dense_threshold
        )
        keyword_ok = candidate.keyword_coverage >= self.keyword_threshold
        return dense_ok or keyword_ok

    def rejection_reasons(self, candidate: WikiScoredCandidate) -> tuple[str, ...]:
        """返回低于阈值的稳定信号，便于日志和评测定位。"""

        if candidate.exact_metadata_match:
            return ()
        if candidate.rerank_score is not None:
            if candidate.rerank_score < self.rerank_threshold:
                return ("below_rerank_threshold",)
            return ()
        reasons: list[str] = []
        if candidate.dense_score is None or candidate.dense_score < self.dense_threshold:
            reasons.append("below_dense_threshold")
        if candidate.keyword_coverage < self.keyword_threshold:
            reasons.append("below_keyword_coverage")
        return tuple(reasons) or ("below_relevance_threshold",)

    def reject_reason(self, candidate: WikiScoredCandidate) -> str | None:
        if self.accept(candidate):
            return None
        reasons = self.rejection_reasons(candidate)
        if len(reasons) == 1:
            return reasons[0]
        return "below_relevance_threshold"
