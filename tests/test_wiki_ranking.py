from __future__ import annotations

from uuid import UUID

import pytest

from agent_api.knowledge.wiki.ranking import (
    WikiScoredCandidate,
    deduplicate_scored_candidates,
    reciprocal_rank_fusion,
    select_mmr,
)


def test_rrf_fuses_channels_without_comparing_raw_scores() -> None:
    fused = reciprocal_rank_fusion(
        [["page-a", "page-b"], ["page-b", "page-c"]],
        k=60,
    )

    assert list(fused) == ["page-b", "page-a", "page-c"]
    assert fused["page-b"] > fused["page-a"]


def test_rrf_ignores_duplicates_within_one_channel() -> None:
    fused = reciprocal_rank_fusion([["page-a", "page-a", "page-b"]], k=60)

    assert list(fused) == ["page-a", "page-b"]
    assert fused["page-a"] == pytest.approx(1 / 61)


def test_mmr_keeps_relevant_but_non_duplicate_claims() -> None:
    selected = select_mmr(
        [
            ("a", 1.0, "MFA 要求两个独立因素"),
            ("b", 0.98, "多因素认证要求两个独立因素"),
            ("c", 0.8, "VPN 过渡方案最长九十天"),
        ],
        limit=2,
        lambda_=0.7,
    )

    assert [item[0] for item in selected] == ["a", "c"]


def test_mmr_handles_empty_limit_and_invalid_lambda() -> None:
    assert select_mmr([], limit=3) == []
    assert select_mmr([("a", 1.0, "事实")], limit=0) == []
    with pytest.raises(ValueError, match="invalid_mmr_lambda"):
        select_mmr([("a", 1.0, "事实")], limit=1, lambda_=1.1)


def test_deduplicates_scored_candidates_by_fact_fingerprint() -> None:
    first_unit = UUID("00000000-0000-0000-0000-000000000001")
    second_unit = UUID("00000000-0000-0000-0000-000000000002")
    low = WikiScoredCandidate(
        unit_id=first_unit,
        page_id=UUID("10000000-0000-0000-0000-000000000001"),
        claim_id=UUID("20000000-0000-0000-0000-000000000001"),
        rrf_score=0.1,
        dense_score=0.6,
        keyword_score=0.1,
        keyword_coverage=0.1,
        rerank_score=None,
        exact_metadata_match=False,
    )
    high_duplicate = WikiScoredCandidate(
        unit_id=second_unit,
        page_id=UUID("10000000-0000-0000-0000-000000000002"),
        claim_id=UUID("20000000-0000-0000-0000-000000000002"),
        rrf_score=0.2,
        dense_score=0.9,
        keyword_score=0.1,
        keyword_coverage=0.1,
        rerank_score=None,
        exact_metadata_match=False,
    )

    deduplicated = deduplicate_scored_candidates(
        [low, high_duplicate],
        fact_fingerprints={
            low.unit_id: "same-fact",
            high_duplicate.unit_id: "same-fact",
        },
    )

    assert deduplicated == [high_duplicate]


def test_deduplicate_tie_break_uses_stable_unit_id_not_input_order() -> None:
    small_unit = UUID("00000000-0000-0000-0000-000000000001")
    large_unit = UUID("00000000-0000-0000-0000-000000000002")
    small = WikiScoredCandidate(
        unit_id=small_unit,
        page_id=UUID("10000000-0000-0000-0000-000000000001"),
        claim_id=UUID("20000000-0000-0000-0000-000000000001"),
        rrf_score=0.7,
        dense_score=0.7,
        keyword_score=0.7,
        keyword_coverage=0.7,
        rerank_score=None,
        exact_metadata_match=False,
    )
    large = WikiScoredCandidate(
        unit_id=large_unit,
        page_id=UUID("10000000-0000-0000-0000-000000000002"),
        claim_id=UUID("20000000-0000-0000-0000-000000000002"),
        rrf_score=0.7,
        dense_score=0.7,
        keyword_score=0.7,
        keyword_coverage=0.7,
        rerank_score=None,
        exact_metadata_match=False,
    )
    fingerprints = {
        small.unit_id: "same-fact",
        large.unit_id: "same-fact",
    }

    forward = deduplicate_scored_candidates([small, large], fact_fingerprints=fingerprints)
    reversed_order = deduplicate_scored_candidates([large, small], fact_fingerprints=fingerprints)

    assert forward == [small]
    assert reversed_order == [small]
