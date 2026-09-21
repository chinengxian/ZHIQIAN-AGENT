from uuid import UUID

from agent_api.knowledge.infrastructure.milvus.index import IndexedChild, MilvusChunkIndex

VERSION_ID = UUID("70000000-0000-0000-0000-000000000001")
KB_ID = UUID("70000000-0000-0000-0000-000000000002")
CHUNK_ID = UUID("70000000-0000-0000-0000-000000000003")


class FakeMilvusClient:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []
        self.inserted: list[tuple[str, list[dict[str, object]]]] = []
        self.flushed: list[str] = []

    def delete(self, collection_name: str, filter: str) -> None:
        self.deleted.append((collection_name, filter))

    def insert(self, collection_name: str, data: list[dict[str, object]]) -> None:
        self.inserted.append((collection_name, data))

    def flush(self, collection_name: str) -> None:
        self.flushed.append(collection_name)

    def query(
        self,
        collection_name: str,
        filter: str,
        output_fields: list[str],
    ) -> list[dict[str, object]]:
        return [{"count(*)": 1}]


async def test_index_replaces_one_version_with_child_records_only() -> None:
    client = FakeMilvusClient()
    index = MilvusChunkIndex(client, "knowledge_chunks")  # type: ignore[arg-type]
    child = IndexedChild(
        chunk_id=CHUNK_ID,
        document_version_id=VERSION_ID,
        knowledge_base_id=KB_ID,
        content="子块正文",
        dense_vector=(0.1, 0.2),
    )

    await index.replace_version(VERSION_ID, [child])

    assert client.deleted == [("knowledge_chunks", f'document_version_id == "{VERSION_ID}"')]
    assert client.inserted == [
        (
            "knowledge_chunks",
            [
                {
                    "chunk_id": str(CHUNK_ID),
                    "document_version_id": str(VERSION_ID),
                    "knowledge_base_id": str(KB_ID),
                    "content": "子块正文",
                    "dense_vector": [0.1, 0.2],
                }
            ],
        )
    ]
    assert client.flushed == ["knowledge_chunks"]
    assert await index.count_version(VERSION_ID) == 1
