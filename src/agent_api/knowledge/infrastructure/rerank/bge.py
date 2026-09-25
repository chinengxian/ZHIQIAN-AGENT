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

    async def rerank(self, query: str, texts: list[str]) -> list[int]:
        return await asyncio.to_thread(self._rerank_sync, query, texts)

    def _rerank_sync(self, query: str, texts: list[str]) -> list[int]:
        if self._model is None:
            from sentence_transformers import CrossEncoder  # type: ignore[import-not-found]

            self._model = CrossEncoder(self._model_name, device=self._device)
        scores = self._model.predict([(query, text) for text in texts], batch_size=self._batch_size)
        return sorted(range(len(texts)), key=lambda index: float(scores[index]), reverse=True)
