from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pymilvus import (  # type: ignore[import-untyped]
    CollectionSchema,
    DataType,
    Function,
    FunctionType,
    MilvusClient,
    MilvusException,
)

_EXPECTED_WIKI_FIELDS = {
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
_OUTPUT_FIELDS = [
    "unit_id",
    "page_id",
    "page_version_id",
    "claim_id",
    "unit_type",
    "content",
]
_MISSING_ALIAS_MARKERS = (
    "alias not found",
    "can't find alias",
    "cannot find alias",
    "alias does not exist",
    "alias doesn't exist",
)
_EXPECTED_INDEXES = {
    "dense_vector": ("HNSW", "COSINE"),
    "sparse_vector": ("SPARSE_INVERTED_INDEX", "BM25"),
}


@dataclass(frozen=True, slots=True)
class WikiIndexedUnit:
    """写入独立 Wiki collection 的可重建检索投影。"""

    unit_id: UUID
    knowledge_base_id: UUID
    page_id: UUID
    page_version_id: UUID
    claim_id: UUID
    unit_type: str
    content: str
    dense_vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class WikiIndexHit:
    """保留单一召回通道原始分数的 Wiki 索引命中。"""

    unit_id: UUID
    page_id: UUID
    page_version_id: UUID
    claim_id: UUID
    unit_type: str
    content: str
    score: float
    channel: Literal["dense", "keyword"]


def build_wiki_milvus_schema(embedding_dimension: int) -> CollectionSchema:
    """构造 Wiki 独立索引 schema，并由 Milvus 从正文生成 BM25 向量。"""

    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    for field_name in (
        "unit_id",
        "knowledge_base_id",
        "page_id",
        "page_version_id",
        "claim_id",
    ):
        schema.add_field(
            field_name=field_name,
            datatype=DataType.VARCHAR,
            max_length=36,
            is_primary=field_name == "unit_id",
        )
    schema.add_field(field_name="unit_type", datatype=DataType.VARCHAR, max_length=32)
    schema.add_field(
        field_name="content",
        datatype=DataType.VARCHAR,
        max_length=65_535,
        enable_analyzer=True,
        enable_match=True,
    )
    schema.add_field(
        field_name="dense_vector",
        datatype=DataType.FLOAT_VECTOR,
        dim=embedding_dimension,
    )
    schema.add_field(field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR)
    schema.add_function(
        Function(
            name="content_bm25",
            function_type=FunctionType.BM25,
            input_field_names=["content"],
            output_field_names=["sparse_vector"],
        )
    )
    return schema


def build_wiki_milvus_indexes() -> object:
    """构造 Dense HNSW 与 BM25 倒排索引参数。"""

    indexes = MilvusClient.prepare_index_params()
    indexes.add_index(
        field_name="dense_vector",
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 200},
    )
    indexes.add_index(
        field_name="sparse_vector",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="BM25",
        params={"inverted_index_algo": "DAAT_MAXSCORE"},
    )
    return indexes


def validate_wiki_milvus_description(
    description: Mapping[str, object],
    *,
    expected_dimension: int,
) -> None:
    """只校验 Wiki collection 兼容性，不尝试删除或覆盖现有数据。"""

    raw_fields = description.get("fields", [])
    if not isinstance(raw_fields, list):
        raise RuntimeError("Wiki Milvus collection schema is incompatible")
    fields = {str(field.get("name")): field for field in raw_fields if isinstance(field, dict)}
    if not _EXPECTED_WIKI_FIELDS.issubset(fields):
        raise RuntimeError("Wiki Milvus collection schema is incompatible")
    if not bool(fields["unit_id"].get("is_primary")):
        raise RuntimeError("Wiki Milvus primary key is incompatible")
    vector_params = fields["dense_vector"].get("params", {})
    if not isinstance(vector_params, dict) or vector_params.get("dim") is None:
        raise RuntimeError("Wiki Milvus embedding dimension is missing")
    if int(vector_params["dim"]) != expected_dimension:
        raise RuntimeError("Wiki Milvus embedding dimension is incompatible")

    raw_functions = description.get("functions", [])
    has_bm25 = isinstance(raw_functions, list) and any(
        isinstance(function, dict)
        and function.get("input_field_names") == ["content"]
        and function.get("output_field_names") == ["sparse_vector"]
        for function in raw_functions
    )
    if not has_bm25:
        raise RuntimeError("Wiki Milvus BM25 function is missing")


def _nested_index_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def _index_value(description: Mapping[str, object], key: str) -> object | None:
    if description.get(key) is not None:
        return description[key]
    for nested_key in ("params", "index_params"):
        nested = _nested_index_mapping(description.get(nested_key))
        if nested is not None and nested.get(key) is not None:
            return nested[key]
    return None


def validate_wiki_milvus_indexes(client: MilvusClient, collection_name: str) -> None:
    """校验已有 collection 的 Dense/BM25 索引协议，不做隐式重建。"""

    try:
        index_names = client.list_indexes(collection_name=collection_name)
        if not isinstance(index_names, list):
            raise RuntimeError("Wiki Milvus index definition is incompatible")
        descriptions: dict[str, Mapping[str, object]] = {}
        for index_name in index_names:
            if not isinstance(index_name, str):
                raise RuntimeError("Wiki Milvus index definition is incompatible")
            description = client.describe_index(
                collection_name=collection_name,
                index_name=index_name,
            )
            if not isinstance(description, Mapping):
                raise RuntimeError("Wiki Milvus index definition is incompatible")
            field_name = _index_value(description, "field_name")
            if isinstance(field_name, str):
                descriptions[field_name] = description
    except MilvusException:
        # 不传播服务端文本，避免连接信息或内部名称进入启动错误。
        raise RuntimeError("Wiki Milvus index validation failed") from None

    for field_name, (expected_type, expected_metric) in _EXPECTED_INDEXES.items():
        description = descriptions.get(field_name)
        if description is None:
            raise RuntimeError("Wiki Milvus index definition is incompatible")
        index_type = _index_value(description, "index_type")
        metric_type = _index_value(description, "metric_type")
        if str(index_type).upper() != expected_type or str(metric_type).upper() != expected_metric:
            raise RuntimeError("Wiki Milvus index definition is incompatible")


def _listed_aliases(client: MilvusClient) -> set[str]:
    """兼容 PyMilvus 标注的 list 与 2.6 实际返回的字典形状。"""

    response = client.list_aliases(collection_name="")
    raw_aliases = response.get("aliases") if isinstance(response, Mapping) else response
    if not isinstance(raw_aliases, Sequence) or isinstance(raw_aliases, (str, bytes)):
        raise RuntimeError("Milvus alias listing is incompatible")
    if not all(isinstance(value, str) for value in raw_aliases):
        raise RuntimeError("Milvus alias listing is incompatible")
    return set(raw_aliases)


def describe_alias_target(client: MilvusClient, alias: str) -> str | None:
    """先读取 alias；失败时用全局 alias 列表稳定判定是否不存在。"""

    try:
        description = client.describe_alias(alias=alias)
    except MilvusException as error:
        message = str(error.message).lower()
        try:
            if alias not in _listed_aliases(client):
                return None
        except MilvusException:
            # 旧服务无法列举 alias 时，才退回兼容性的错误文本判断。
            if any(marker in message for marker in _MISSING_ALIAS_MARKERS):
                return None
            raise error from None
        if any(marker in message for marker in _MISSING_ALIAS_MARKERS):
            return None
        raise
    target = description.get("collection_name") or description.get("collection")
    if not isinstance(target, str) or not target:
        raise RuntimeError("Milvus alias target collection is missing")
    return target


def _canonical_uuid(value: UUID) -> UUID:
    """所有过滤条件都先经 UUID 解析，阻止自由文本进入 Milvus 表达式。"""

    return UUID(str(value))


def _page_version_filter(page_version_id: UUID) -> str:
    return f'page_version_id == "{_canonical_uuid(page_version_id)}"'


def _scope_filter(knowledge_base_ids: tuple[UUID, ...]) -> str:
    values = (_canonical_uuid(value) for value in knowledge_base_ids)
    return "knowledge_base_id in [" + ",".join(f'"{value}"' for value in values) + "]"


class MilvusWikiIndex:
    """分离物理写入 collection 与 alias 搜索 collection 的 Wiki 索引适配器。"""

    def __init__(
        self,
        client: MilvusClient,
        *,
        search_collection: str,
        write_collection: str,
    ) -> None:
        self._client = client
        self._search_collection = search_collection
        self._write_collection = write_collection

    async def replace_page_version(
        self,
        page_version_id: UUID,
        units: Sequence[WikiIndexedUnit],
    ) -> None:
        await asyncio.to_thread(self._replace_page_version_sync, page_version_id, units)

    def _replace_page_version_sync(
        self,
        page_version_id: UUID,
        units: Sequence[WikiIndexedUnit],
    ) -> None:
        version_id = _canonical_uuid(page_version_id)
        records = [self._unit_record(unit, version_id) for unit in units]
        self._client.delete(
            collection_name=self._write_collection,
            filter=_page_version_filter(version_id),
        )
        if records:
            self._client.insert(collection_name=self._write_collection, data=records)
        # 数量校验和 alias 切换前都必须看见本次完整替换结果。
        self._client.flush(collection_name=self._write_collection)

    @staticmethod
    def _unit_record(unit: WikiIndexedUnit, page_version_id: UUID) -> dict[str, object]:
        unit_version_id = _canonical_uuid(unit.page_version_id)
        if unit_version_id != page_version_id:
            raise ValueError("wiki_unit_page_version_mismatch")
        return {
            "unit_id": str(_canonical_uuid(unit.unit_id)),
            "knowledge_base_id": str(_canonical_uuid(unit.knowledge_base_id)),
            "page_id": str(_canonical_uuid(unit.page_id)),
            "page_version_id": str(unit_version_id),
            "claim_id": str(_canonical_uuid(unit.claim_id)),
            "unit_type": unit.unit_type,
            "content": unit.content,
            "dense_vector": list(unit.dense_vector),
        }

    async def count_page_version(self, page_version_id: UUID) -> int:
        return await asyncio.to_thread(self._count_page_version_sync, page_version_id)

    def _count_page_version_sync(self, page_version_id: UUID) -> int:
        rows = self._client.query(
            collection_name=self._write_collection,
            filter=_page_version_filter(page_version_id),
            output_fields=["count(*)"],
        )
        if not rows:
            return 0
        return int(rows[0].get("count(*)", 0))

    async def delete_page_version(self, page_version_id: UUID) -> None:
        await asyncio.to_thread(self._delete_page_version_sync, page_version_id)

    def _delete_page_version_sync(self, page_version_id: UUID) -> None:
        self._client.delete(
            collection_name=self._write_collection,
            filter=_page_version_filter(page_version_id),
        )
        self._client.flush(collection_name=self._write_collection)

    async def search_dense(
        self,
        vector: list[float],
        knowledge_base_ids: tuple[UUID, ...],
        limit: int,
    ) -> list[WikiIndexHit]:
        if not knowledge_base_ids:
            return []
        return await asyncio.to_thread(
            self._search_sync,
            [vector],
            "dense_vector",
            {"metric_type": "COSINE", "params": {"ef": 64}},
            knowledge_base_ids,
            limit,
            "dense",
        )

    async def search_keyword(
        self,
        query: str,
        knowledge_base_ids: tuple[UUID, ...],
        limit: int,
    ) -> list[WikiIndexHit]:
        if not knowledge_base_ids:
            return []
        return await asyncio.to_thread(
            self._search_sync,
            [query],
            "sparse_vector",
            {"metric_type": "BM25", "params": {}},
            knowledge_base_ids,
            limit,
            "keyword",
        )

    def _search_sync(
        self,
        data: list[object],
        anns_field: str,
        search_params: dict[str, object],
        knowledge_base_ids: tuple[UUID, ...],
        limit: int,
        channel: Literal["dense", "keyword"],
    ) -> list[WikiIndexHit]:
        result = self._client.search(
            collection_name=self._search_collection,
            data=data,
            anns_field=anns_field,
            search_params=search_params,
            filter=_scope_filter(knowledge_base_ids),
            limit=limit,
            output_fields=_OUTPUT_FIELDS,
        )
        hits: list[WikiIndexHit] = []
        for row in result[0] if result else []:
            entity = row.get("entity") or {}
            raw_unit_id = entity.get("unit_id") or row.get("unit_id") or row.get("id")
            hits.append(
                WikiIndexHit(
                    unit_id=UUID(str(raw_unit_id)),
                    page_id=UUID(str(entity["page_id"])),
                    page_version_id=UUID(str(entity["page_version_id"])),
                    claim_id=UUID(str(entity["claim_id"])),
                    unit_type=str(entity["unit_type"]),
                    content=str(entity["content"]),
                    score=float(row.get("distance", 0.0)),
                    channel=channel,
                )
            )
        return hits

    async def switch_alias(self, physical_collection: str, alias: str) -> None:
        await asyncio.to_thread(self._switch_alias_sync, physical_collection, alias)

    def _switch_alias_sync(self, physical_collection: str, alias: str) -> None:
        current_target = describe_alias_target(self._client, alias)
        if current_target is None:
            self._client.create_alias(collection_name=physical_collection, alias=alias)
            return
        self._client.alter_alias(collection_name=physical_collection, alias=alias)
