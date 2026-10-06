from __future__ import annotations

import pytest

from agent_api.knowledge.wiki.ranking import reciprocal_rank_fusion, select_mmr


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
