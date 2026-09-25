from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from pymilvus import AnnSearchRequest, MilvusClient, RRFRanker  # type: ignore[import-untyped]


@dataclass(frozen=True, slots=True)
class IndexedChild:
    chunk_id: UUID
    document_version_id: UUID
    knowledge_base_id: UUID
    content: str
    dense_vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: UUID
    document_version_id: UUID
    knowledge_base_id: UUID
    score: float


class MilvusChunkIndex:
    """只索引子块；父块和权威正文始终留在 PostgreSQL。"""

    def __init__(self, client: MilvusClient, collection_name: str) -> None:
        self._client = client
        self._collection_name = collection_name

    async def replace_version(
        self,
        version_id: UUID,
        children: list[IndexedChild],
    ) -> None:
        await asyncio.to_thread(self._replace_version_sync, version_id, children)

    def _replace_version_sync(
        self,
        version_id: UUID,
        children: list[IndexedChild],
    ) -> None:
        version_filter = f'document_version_id == "{version_id}"'
        self._client.delete(
            collection_name=self._collection_name,
            filter=version_filter,
        )
        if children:
            self._client.insert(
                collection_name=self._collection_name,
                data=[
                    {
                        "chunk_id": str(child.chunk_id),
                        "document_version_id": str(child.document_version_id),
                        "knowledge_base_id": str(child.knowledge_base_id),
                        "content": child.content,
                        "dense_vector": list(child.dense_vector),
                    }
                    for child in children
                ],
            )
        # Milvus 默认最终一致；完整性计数和版本切换前必须显式 flush。
        self._client.flush(collection_name=self._collection_name)

    async def count_version(self, version_id: UUID) -> int:
        return await asyncio.to_thread(self._count_version_sync, version_id)

    def _count_version_sync(self, version_id: UUID) -> int:
        rows = self._client.query(
            collection_name=self._collection_name,
            filter=f'document_version_id == "{version_id}"',
            output_fields=["count(*)"],
        )
        if not rows:
            return 0
        return int(rows[0].get("count(*)", 0))

    async def delete_version(self, version_id: UUID) -> None:
        await asyncio.to_thread(self._delete_version_sync, version_id)

    def _delete_version_sync(self, version_id: UUID) -> None:
        self._client.delete(
            collection_name=self._collection_name,
            filter=f'document_version_id == "{version_id}"',
        )
        self._client.flush(collection_name=self._collection_name)

    async def search(
        self,
        query: str,
        *,
        vector: list[float] | None,
        knowledge_base_ids: tuple[UUID, ...],
        mode: str,
        limit: int = 20,
    ) -> list[SearchHit]:
        if not knowledge_base_ids:
            return []
        return await asyncio.to_thread(
            self._search_sync, query, vector, knowledge_base_ids, mode, limit
        )

    def _search_sync(
        self,
        query: str,
        vector: list[float] | None,
        knowledge_base_ids: tuple[UUID, ...],
        mode: str,
        limit: int,
    ) -> list[SearchHit]:
        # UUID 均由 Pydantic/数据库产生，不接受模型拼接的 Milvus 表达式。
        scope_filter = (
            "knowledge_base_id in [" + ",".join(f'"{value}"' for value in knowledge_base_ids) + "]"
        )
        fields = ["document_version_id", "knowledge_base_id"]
        dense_params = {"metric_type": "COSINE", "params": {"ef": 64}}
        sparse_params = {"metric_type": "BM25", "params": {}}
        if mode == "hybrid":
            if vector is None:
                raise ValueError("dense_vector_required")
            requests = [
                AnnSearchRequest(
                    data=[vector],
                    anns_field="dense_vector",
                    param=dense_params,
                    limit=30,
                    expr=scope_filter,
                ),
                AnnSearchRequest(
                    data=[query],
                    anns_field="sparse_vector",
                    param=sparse_params,
                    limit=30,
                    expr=scope_filter,
                ),
            ]
            result = self._client.hybrid_search(
                collection_name=self._collection_name,
                reqs=requests,
                ranker=RRFRanker(k=60),
                limit=limit,
                output_fields=fields,
            )
        elif mode in {"semantic", "keyword"}:
            if mode == "semantic" and vector is None:
                raise ValueError("dense_vector_required")
            result = self._client.search(
                collection_name=self._collection_name,
                data=[vector if mode == "semantic" else query],
                anns_field="dense_vector" if mode == "semantic" else "sparse_vector",
                search_params=dense_params if mode == "semantic" else sparse_params,
                filter=scope_filter,
                limit=limit,
                output_fields=fields,
            )
        else:
            raise ValueError("invalid_search_mode")
        hits: list[SearchHit] = []
        for row in result[0] if result else []:
            entity = row.get("entity") or {}
            hits.append(
                SearchHit(
                    chunk_id=UUID(str(row["chunk_id"])),
                    document_version_id=UUID(str(entity["document_version_id"])),
                    knowledge_base_id=UUID(str(entity["knowledge_base_id"])),
                    score=float(row.get("distance", 0.0)),
                )
            )
        return hits
