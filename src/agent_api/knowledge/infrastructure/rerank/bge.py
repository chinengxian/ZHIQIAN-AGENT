from __future__ import annotations

import asyncio
from typing import Any


class LocalBGEReranker:
    """按需加载本地 Cross-Encoder；不可用时由检索层回退 RRF。"""

    def __init__(self, model_name: str, device: str, batch_size: int) -> None:
        self._model_name = model_name
        self._device = device
        self._batch_size = batch_size
        self._model: Any = None

    async def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        return await asyncio.to_thread(self._score_sync, query, texts)

    async def rerank(self, query: str, texts: list[str]) -> list[int]:
        scores = await self.score(query, texts)
        return sorted(range(len(texts)), key=lambda index: scores[index], reverse=True)

    def _score_sync(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name, device=self._device)
        scores = self._model.predict([(query, text) for text in texts], batch_size=self._batch_size)
        try:
            result = [float(score) for score in scores]
        except (TypeError, ValueError):
            raise ValueError("invalid_rerank_score_count") from None
        if len(result) != len(texts):
            raise ValueError("invalid_rerank_score_count")
        return result
