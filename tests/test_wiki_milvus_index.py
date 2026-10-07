from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from uuid import UUID

import pytest
from pymilvus import MilvusException  # type: ignore[import-untyped]

from agent_api.knowledge.infrastructure.milvus.wiki_index import (
    MilvusWikiIndex,
    WikiIndexedUnit,
    WikiIndexHit,
    build_wiki_milvus_indexes,
    build_wiki_milvus_schema,
)

UNIT_ID = UUID("80000000-0000-0000-0000-000000000001")
KB_ID = UUID("80000000-0000-0000-0000-000000000002")
PAGE_ID = UUID("80000000-0000-0000-0000-000000000003")
VERSION_ID = UUID("80000000-0000-0000-0000-000000000004")
CLAIM_ID = UUID("80000000-0000-0000-0000-000000000005")


def indexed_unit() -> WikiIndexedUnit:
    return WikiIndexedUnit(
        unit_id=UNIT_ID,
        knowledge_base_id=KB_ID,
        page_id=PAGE_ID,
        page_version_id=VERSION_ID,
        claim_id=CLAIM_ID,
        unit_type="summary_fact",
        content="每名使用者必须使用唯一身份。",
        dense_vector=(0.1, 0.2, 0.3),
    )


class FakeMilvusClient:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []
        self.inserted: list[tuple[str, list[dict[str, object]]]] = []
        self.flushed: list[str] = []
        self.queries: list[tuple[str, str]] = []
        self.searches: list[dict[str, object]] = []
        self.aliases: dict[str, str] = {}
        self.alias_events: list[tuple[str, str, str]] = []
        self.operations: list[str] = []

    def delete(self, collection_name: str, filter: str) -> None:
        self.deleted.append((collection_name, filter))
        self.operations.append("delete")

    def insert(self, collection_name: str, data: list[dict[str, object]]) -> None:
        self.inserted.append((collection_name, data))
        self.operations.append("insert")

    def flush(self, collection_name: str) -> None:
        self.flushed.append(collection_name)
        self.operations.append("flush")

    def query(
        self,
        collection_name: str,
        filter: str,
        output_fields: list[str],
    ) -> list[dict[str, object]]:
        self.queries.append((collection_name, filter))
        assert output_fields == ["count(*)"]
        return [{"count(*)": 1}]

    def search(self, **kwargs: object) -> list[list[dict[str, object]]]:
        self.searches.append(kwargs)
        return [
            [
                {
                    "id": str(UNIT_ID),
                    "distance": 0.82,
                    "entity": {
                        "unit_id": str(UNIT_ID),
                        "page_id": str(PAGE_ID),
                        "page_version_id": str(VERSION_ID),
                        "claim_id": str(CLAIM_ID),
                        "unit_type": "summary_fact",
                        "content": "每名使用者必须使用唯一身份。",
                    },
                }
            ]
        ]

    def describe_alias(self, alias: str) -> dict[str, str]:
        self.alias_events.append(("describe", alias, ""))
        if alias not in self.aliases:
            raise MilvusException(message="alias not found")
        return {"alias": alias, "collection_name": self.aliases[alias]}

    def create_alias(self, collection_name: str, alias: str) -> None:
        self.aliases[alias] = collection_name
        self.alias_events.append(("create", alias, collection_name))

    def alter_alias(self, collection_name: str, alias: str) -> None:
        self.aliases[alias] = collection_name
        self.alias_events.append(("alter", alias, collection_name))


def test_wiki_schema_contains_identity_content_dense_and_bm25_fields() -> None:
    schema = build_wiki_milvus_schema(1024).to_dict()
    fields = {field["name"]: field for field in schema["fields"]}

    assert set(fields) == {
        "unit_id",
        "knowledge_base_id",
        "page_id",
        "page_version_id",
        "claim_id",
        "unit_type",
        "content",
        "dense_vector",
        "sparse_vector",
    }
    assert fields["unit_id"]["is_primary"] is True
    assert fields["dense_vector"]["params"]["dim"] == 1024
    assert schema["functions"][0]["input_field_names"] == ["content"]
    assert schema["functions"][0]["output_field_names"] == ["sparse_vector"]

    indexes = build_wiki_milvus_indexes()
    definitions = {item.field_name: item.to_dict() for item in indexes}  # type: ignore[attr-defined]
    assert definitions["dense_vector"]["index_type"] == "HNSW"
    assert definitions["sparse_vector"]["index_type"] == "SPARSE_INVERTED_INDEX"


def test_wiki_index_records_are_frozen_slotted_contracts() -> None:
    assert WikiIndexedUnit.__slots__ == (
        "unit_id",
        "knowledge_base_id",
        "page_id",
        "page_version_id",
        "claim_id",
        "unit_type",
        "content",
        "dense_vector",
    )
    assert tuple(field.name for field in fields(WikiIndexHit)) == (
        "unit_id",
        "page_id",
        "page_version_id",
        "claim_id",
        "unit_type",
        "content",
        "score",
        "channel",
    )
    with pytest.raises(FrozenInstanceError):
        indexed_unit().content = "不可修改"  # type: ignore[misc]


async def test_replace_page_version_flushes_and_counts_units() -> None:
    client = FakeMilvusClient()
    index = MilvusWikiIndex(
        client,  # type: ignore[arg-type]
        search_collection="wiki_units",
        write_collection="wiki_units_v1",
    )

    await index.replace_page_version(VERSION_ID, [indexed_unit()])

    expected_filter = f'page_version_id == "{VERSION_ID}"'
    assert client.deleted == [("wiki_units_v1", expected_filter)]
    assert client.inserted == [
        (
            "wiki_units_v1",
            [
                {
                    "unit_id": str(UNIT_ID),
                    "knowledge_base_id": str(KB_ID),
                    "page_id": str(PAGE_ID),
                    "page_version_id": str(VERSION_ID),
                    "claim_id": str(CLAIM_ID),
                    "unit_type": "summary_fact",
                    "content": "每名使用者必须使用唯一身份。",
                    "dense_vector": [0.1, 0.2, 0.3],
                }
            ],
        )
    ]
    assert client.flushed == ["wiki_units_v1"]
    assert client.operations == ["delete", "insert", "flush"]
    assert await index.count_page_version(VERSION_ID) == 1
    assert client.queries == [("wiki_units_v1", expected_filter)]


async def test_delete_page_version_only_mutates_write_collection() -> None:
    client = FakeMilvusClient()
    index = MilvusWikiIndex(
        client,  # type: ignore[arg-type]
        search_collection="wiki_units",
        write_collection="wiki_units_v2",
    )

    await index.delete_page_version(VERSION_ID)

    assert client.deleted == [
        ("wiki_units_v2", f'page_version_id == "{VERSION_ID}"')
    ]
    assert client.flushed == ["wiki_units_v2"]


@pytest.mark.parametrize(
    ("method_name", "expected_field", "expected_data", "expected_channel"),
    [
        ("search_dense", "dense_vector", [[0.1, 0.2, 0.3]], "dense"),
        ("search_keyword", "sparse_vector", ["身份认证"], "keyword"),
    ],
)
async def test_search_channels_read_alias_and_keep_raw_scores(
    method_name: str,
    expected_field: str,
    expected_data: list[object],
    expected_channel: str,
) -> None:
    client = FakeMilvusClient()
    index = MilvusWikiIndex(
        client,  # type: ignore[arg-type]
        search_collection="wiki_units",
        write_collection="wiki_units_v2",
    )

    if method_name == "search_dense":
        hits = await index.search_dense([0.1, 0.2, 0.3], (KB_ID,), 8)
    else:
        hits = await index.search_keyword("身份认证", (KB_ID,), 8)

    assert client.searches[0]["collection_name"] == "wiki_units"
    assert client.searches[0]["anns_field"] == expected_field
    assert client.searches[0]["data"] == expected_data
    assert client.searches[0]["filter"] == f'knowledge_base_id in ["{KB_ID}"]'
    assert hits[0].unit_id == UNIT_ID
    assert hits[0].score == 0.82
    assert hits[0].channel == expected_channel


async def test_uuid_filters_reject_unparsed_values_and_never_include_free_text() -> None:
    client = FakeMilvusClient()
    index = MilvusWikiIndex(
        client,  # type: ignore[arg-type]
        search_collection="wiki_units",
        write_collection="wiki_units_v1",
    )

    with pytest.raises(ValueError):
        await index.delete_page_version('bad" or true',)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await index.search_dense([0.1], ('bad" or true',), 8)  # type: ignore[arg-type]

    query = '身份认证" or knowledge_base_id != "'
    await index.search_keyword(query, (KB_ID,), 8)

    assert client.deleted == []
    assert query not in str(client.searches[0]["filter"])
    assert client.searches[0]["data"] == [query]


async def test_empty_scope_returns_without_querying_milvus() -> None:
    client = FakeMilvusClient()
    index = MilvusWikiIndex(
        client,  # type: ignore[arg-type]
        search_collection="wiki_units",
        write_collection="wiki_units_v1",
    )

    assert await index.search_dense([0.1], (), 8) == []
    assert await index.search_keyword("身份认证", (), 8) == []
    assert client.searches == []


async def test_switch_alias_creates_missing_alias_after_describe() -> None:
    client = FakeMilvusClient()
    index = MilvusWikiIndex(
        client,  # type: ignore[arg-type]
        search_collection="wiki_units",
        write_collection="wiki_units_v1",
    )

    await index.switch_alias("wiki_units_v1", "wiki_units")

    assert client.alias_events == [
        ("describe", "wiki_units", ""),
        ("create", "wiki_units", "wiki_units_v1"),
    ]


async def test_switch_alias_alters_existing_alias_after_describe() -> None:
    client = FakeMilvusClient()
    client.aliases["wiki_units"] = "wiki_units_v1"
    index = MilvusWikiIndex(
        client,  # type: ignore[arg-type]
        search_collection="wiki_units",
        write_collection="wiki_units_v2",
    )

    await index.switch_alias("wiki_units_v2", "wiki_units")

    assert client.alias_events == [
        ("describe", "wiki_units", ""),
        ("alter", "wiki_units", "wiki_units_v2"),
    ]
