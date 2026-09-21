from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from pymilvus import MilvusClient  # type: ignore[import-untyped]


@dataclass(frozen=True, slots=True)
class IndexedChild:
    chunk_id: UUID
    document_version_id: UUID
    knowledge_base_id: UUID
    content: str
    dense_vector: tuple[float, ...]


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
