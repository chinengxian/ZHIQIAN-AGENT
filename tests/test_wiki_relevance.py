from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from agent_api.knowledge.infrastructure.rerank.bge import LocalBGEReranker
from agent_api.knowledge.wiki.ranking import WikiScoredCandidate
from agent_api.knowledge.wiki.relevance import WikiRelevancePolicy, fact_fingerprint

UNIT_ID = uuid4()


def candidate(
    *, dense: float | None, keyword: float, rerank: float | None, exact: bool
) -> WikiScoredCandidate:
    return WikiScoredCandidate(
        unit_id=UNIT_ID,
        page_id=uuid4(),
        claim_id=uuid4(),
        rrf_score=0.1,
        dense_score=dense,
        keyword_score=keyword,
        keyword_coverage=keyword,
        rerank_score=rerank,
        exact_metadata_match=exact,
    )


def test_policy_rejects_rrf_candidate_without_relevance_evidence() -> None:
    policy = WikiRelevancePolicy(
        dense_threshold=0.55, rerank_threshold=0.50, keyword_threshold=0.50
    )

    rejected = candidate(dense=0.20, keyword=0.0, rerank=None, exact=False)

    assert policy.accept(rejected) is False
    assert policy.reject_reason(rejected) == "below_relevance_threshold"


def test_exact_alias_is_accepted_without_dense_score() -> None:
    policy = WikiRelevancePolicy(
        dense_threshold=0.55, rerank_threshold=0.50, keyword_threshold=0.50
    )

    assert policy.accept(candidate(dense=None, keyword=0.0, rerank=None, exact=True)) is True


@pytest.mark.parametrize(
    ("candidate_value", "reason"),
    [
        (candidate(dense=0.70, keyword=0.0, rerank=0.49, exact=False), "below_rerank_threshold"),
        (candidate(dense=0.20, keyword=0.60, rerank=None, exact=False), "below_dense_threshold"),
        (candidate(dense=0.60, keyword=0.20, rerank=None, exact=False), "below_keyword_coverage"),
    ],
)
def test_policy_returns_stable_signal_reasons(
    candidate_value: WikiScoredCandidate, reason: str
) -> None:
    policy = WikiRelevancePolicy(
        dense_threshold=0.55, rerank_threshold=0.50, keyword_threshold=0.50
    )

    assert reason in policy.rejection_reasons(candidate_value)


def test_fact_fingerprint_normalizes_text_and_sorts_sources() -> None:
    chunk_a = UUID("00000000-0000-0000-0000-000000000001")
    chunk_b = UUID("00000000-0000-0000-0000-000000000002")
    version_a = UUID("10000000-0000-0000-0000-000000000001")
    version_b = UUID("10000000-0000-0000-0000-000000000002")

    first = fact_fingerprint(
        " ＭＦＡ   MUST\tBE enabled ",
        source_chunk_ids=(chunk_b, chunk_a),
        active_document_version_ids=(version_b, version_a),
    )
    second = fact_fingerprint(
        "mfa must be ENABLED",
        source_chunk_ids=(chunk_a, chunk_b),
        active_document_version_ids=(version_a, version_b),
    )
    changed_version = fact_fingerprint(
        "mfa must be enabled",
        source_chunk_ids=(chunk_a, chunk_b),
        active_document_version_ids=(version_a,),
    )

    assert first == second
    assert first != changed_version
    assert len(first) == 64


async def test_local_bge_score_returns_float_scores_and_rerank_keeps_compatibility() -> None:
    class FakeCrossEncoder:
        def predict(self, pairs: list[tuple[str, str]], *, batch_size: int) -> list[float]:
            assert pairs == [("query", "low"), ("query", "high")]
            assert batch_size == 8
            return [0.2, 0.9]

    reranker = LocalBGEReranker("model", "cpu", 8)
    reranker._model = FakeCrossEncoder()

    assert await reranker.score("query", ["low", "high"]) == [0.2, 0.9]
    assert await reranker.rerank("query", ["low", "high"]) == [1, 0]
