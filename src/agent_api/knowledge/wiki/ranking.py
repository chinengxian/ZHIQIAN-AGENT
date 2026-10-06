from __future__ import annotations

import re
from collections.abc import Hashable, Sequence
from typing import TypeVar

RankKey = TypeVar("RankKey", bound=Hashable)
MmrKey = TypeVar("MmrKey")


def reciprocal_rank_fusion(
    channels: Sequence[Sequence[RankKey]],
    *,
    k: int = 60,
) -> dict[RankKey, float]:
    """融合多个排名通道，避免直接比较不可比的原始分数。"""

    if k < 0:
        raise ValueError("invalid_rrf_k")
    scores: dict[RankKey, float] = {}
    first_seen: dict[RankKey, int] = {}
    sequence = 0
    for channel in channels:
        seen: set[RankKey] = set()
        rank = 0
        for item in channel:
            if item in seen:
                continue
            seen.add(item)
            rank += 1
            if item not in first_seen:
                first_seen[item] = sequence
                sequence += 1
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return dict(
        sorted(scores.items(), key=lambda item: (-item[1], first_seen[item[0]]))
    )


def _tokens(text: str) -> set[str]:
    """英文按词、中文按单字切分，提供确定且无外部依赖的去重信号。"""

    normalized = text.casefold()
    english = re.findall(r"[a-z0-9]+", normalized)
    chinese = re.findall(r"[\u3400-\u9fff]", normalized)
    return {*english, *chinese}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def select_mmr(
    candidates: Sequence[tuple[MmrKey, float, str]],
    *,
    limit: int,
    lambda_: float = 0.7,
) -> list[tuple[MmrKey, float, str]]:
    """在相关性和内容差异之间选择稳定的 claim 子集。"""

    if not 0 <= lambda_ <= 1:
        raise ValueError("invalid_mmr_lambda")
    if limit <= 0 or not candidates:
        return []
    remaining = list(enumerate(candidates))
    token_sets = {index: _tokens(candidate[2]) for index, candidate in remaining}
    selected: list[tuple[int, tuple[MmrKey, float, str]]] = []
    while remaining and len(selected) < limit:
        if not selected:
            best = max(remaining, key=lambda item: (item[1][1], -item[0]))
        else:
            selected_token_sets = tuple(token_sets[item[0]] for item in selected)

            def score(
                item: tuple[int, tuple[MmrKey, float, str]],
                selected_sets: tuple[set[str], ...] = selected_token_sets,
            ) -> tuple[float, int]:
                index, candidate = item
                redundancy = max(
                    _jaccard(token_sets[index], selected_set)
                    for selected_set in selected_sets
                )
                value = lambda_ * candidate[1] - (1 - lambda_) * redundancy
                return value, -index

            best = max(remaining, key=score)
        selected.append(best)
        remaining.remove(best)
    return [candidate for _, candidate in selected]
