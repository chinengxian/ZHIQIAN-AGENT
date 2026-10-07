import asyncio
from collections.abc import Callable, Sequence
from typing import Protocol

from pymilvus import (  # type: ignore[import-untyped]
    CollectionSchema,
    DataType,
    Function,
    FunctionType,
    MilvusClient,
)
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agent_api.core.config import Settings
from agent_api.knowledge.infrastructure.milvus.wiki_index import (
    build_wiki_milvus_indexes,
    build_wiki_milvus_schema,
    describe_alias_target,
    validate_wiki_milvus_description,
)
from agent_api.knowledge.infrastructure.storage.local import LocalFileStorage

EXPECTED_DATABASE_REVISION = "20261007_0005"
_EXPECTED_MILVUS_FIELDS = {
    "chunk_id",
    "document_version_id",
    "knowledge_base_id",
    "dense_vector",
    "sparse_vector",
    "content",
}


def build_milvus_schema(embedding_dimension: int) -> CollectionSchema:
    """构造子块检索 schema：Dense 向量、BM25 稀疏向量和范围过滤字段。"""

    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(
        field_name="chunk_id",
        datatype=DataType.VARCHAR,
        is_primary=True,
        max_length=36,
    )
    schema.add_field(
        field_name="document_version_id",
        datatype=DataType.VARCHAR,
        max_length=36,
    )
    schema.add_field(
        field_name="knowledge_base_id",
        datatype=DataType.VARCHAR,
        max_length=36,
    )
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


def validate_milvus_description(
    description: dict[str, object],
    *,
    expected_dimension: int,
) -> None:
    """只校验已有 collection，不做破坏性修复或隐式维度迁移。"""

    raw_fields = description.get("fields", [])
    if not isinstance(raw_fields, list):
        raise RuntimeError("Milvus collection schema is incompatible")
    fields = {str(field.get("name")): field for field in raw_fields if isinstance(field, dict)}
    if not _EXPECTED_MILVUS_FIELDS.issubset(fields):
        raise RuntimeError("Milvus collection schema is incompatible")
    vector_params = fields["dense_vector"].get("params", {})
    if not isinstance(vector_params, dict) or vector_params.get("dim") is None:
        raise RuntimeError("Milvus embedding dimension is missing")
    if int(vector_params["dim"]) != expected_dimension:
        raise RuntimeError("Milvus embedding dimension is incompatible")

    raw_functions = description.get("functions", [])
    has_bm25 = isinstance(raw_functions, list) and any(
        isinstance(function, dict)
        and function.get("input_field_names") == ["content"]
        and function.get("output_field_names") == ["sparse_vector"]
        for function in raw_functions
    )
    if not has_bm25:
        raise RuntimeError("Milvus BM25 function is missing")


def build_milvus_indexes() -> object:
    """创建 Dense HNSW 与 BM25 sparse 两路召回所需索引参数。"""

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


class StartupCheck(Protocol):
    """统一的启动检查协议，便于测试替身和后续替换基础设施。"""

    name: str

    async def check(self) -> None: ...

    async def close(self) -> None: ...


class StartupCheckError(RuntimeError):
    """对外只携带检查名称，不传播可能包含凭据的底层异常文本。"""

    def __init__(self, check_name: str) -> None:
        self.check_name = check_name
        super().__init__(f"knowledge startup check failed: {check_name}")


class KnowledgeStartup:
    """顺序执行知识基础设施检查，并在失败或停机时逆序释放资源。"""

    def __init__(self, storage: LocalFileStorage, checks: Sequence[StartupCheck]) -> None:
        self.storage = storage
        self.checks = tuple(checks)
        self._initialized: list[StartupCheck] = []

    async def start(self) -> None:
        # 文件目录先验证，因为它不需要网络且能快速发现危险/只读路径。
        self.storage.ensure_ready()
        for check in self.checks:
            try:
                await check.check()
            except Exception:
                # 当前检查失败时，只关闭此前已经成功初始化的资源。
                await self._close_initialized()
                raise StartupCheckError(check.name) from None
            self._initialized.append(check)

    async def close(self) -> None:
        await self._close_initialized()

    async def _close_initialized(self) -> None:
        while self._initialized:
            await self._initialized.pop().close()


class DatabaseStartupCheck:
    """确认 PostgreSQL 可连接，并且 schema 已升级到应用期望版本。"""

    name = "database"

    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)

    async def check(self) -> None:
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != EXPECTED_DATABASE_REVISION:
            raise RuntimeError("database migration revision does not match the application")

    async def close(self) -> None:
        await self._engine.dispose()


class RedisStartupCheck:
    """确认异步任务依赖的 Redis 可以响应。"""

    name = "redis"

    def __init__(self, redis_url: str) -> None:
        self._client: Redis = Redis.from_url(redis_url, decode_responses=False)

    async def check(self) -> None:
        if not await self._client.ping():
            raise RuntimeError("Redis ping failed")

    async def close(self) -> None:
        await self._client.aclose()


class MilvusStartupCheck:
    """创建首个 collection，或校验已有 collection 与当前配置兼容。"""

    name = "milvus"

    def __init__(
        self,
        uri: str,
        token: str | None,
        collection: str,
        embedding_dimension: int,
    ) -> None:
        self._uri = uri
        self._token = token
        self._collection = collection
        self._embedding_dimension = embedding_dimension
        self._client: MilvusClient | None = None

    async def check(self) -> None:
        # PyMilvus 是同步客户端，放入线程避免阻塞 FastAPI 事件循环。
        await asyncio.to_thread(self._check_sync)

    def _check_sync(self) -> None:
        kwargs: dict[str, str] = {"uri": self._uri}
        if self._token:
            kwargs["token"] = self._token
        client = MilvusClient(**kwargs)
        self._client = client
        if not client.has_collection(collection_name=self._collection):
            # 仅首次缺失时创建；已有 collection 永远不会被静默删除或覆盖。
            client.create_collection(
                collection_name=self._collection,
                schema=build_milvus_schema(self._embedding_dimension),
                index_params=build_milvus_indexes(),
            )

        description = client.describe_collection(collection_name=self._collection)
        validate_milvus_description(
            description,
            expected_dimension=self._embedding_dimension,
        )

    async def close(self) -> None:
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
            self._client = None


class WikiMilvusStartupCheck:
    """创建或校验 Wiki 物理 collection，并安全初始化搜索 alias。"""

    name = "wiki_milvus"

    def __init__(
        self,
        uri: str,
        token: str | None,
        physical_collection: str,
        alias: str,
        embedding_dimension: int,
    ) -> None:
        self._uri = uri
        self._token = token
        self._physical_collection = physical_collection
        self._alias = alias
        self._embedding_dimension = embedding_dimension
        self._client: MilvusClient | None = None

    async def check(self) -> None:
        # PyMilvus 是同步客户端，启动检查放入线程避免阻塞事件循环。
        await asyncio.to_thread(self._check_sync)

    def _check_sync(self) -> None:
        kwargs: dict[str, str] = {"uri": self._uri}
        if self._token:
            kwargs["token"] = self._token
        client = MilvusClient(**kwargs)
        self._client = client

        if not client.has_collection(collection_name=self._physical_collection):
            # 只创建缺失的版本化 collection，绝不删除或覆盖已有 collection。
            client.create_collection(
                collection_name=self._physical_collection,
                schema=build_wiki_milvus_schema(self._embedding_dimension),
                index_params=build_wiki_milvus_indexes(),
            )
        physical_description = client.describe_collection(
            collection_name=self._physical_collection
        )
        validate_wiki_milvus_description(
            physical_description,
            expected_dimension=self._embedding_dimension,
        )

        target_collection = describe_alias_target(client, self._alias)
        if target_collection is None:
            # 首次启动可以建立 alias；已有 alias 的切换必须走显式质量门流程。
            client.create_alias(
                collection_name=self._physical_collection,
                alias=self._alias,
            )
            return
        if not client.has_collection(collection_name=target_collection):
            raise RuntimeError("Wiki Milvus alias target collection does not exist")
        target_description = client.describe_collection(collection_name=target_collection)
        validate_wiki_milvus_description(
            target_description,
            expected_dimension=self._embedding_dimension,
        )

    async def close(self) -> None:
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
            self._client = None


KnowledgeStartupFactory = Callable[[Settings], KnowledgeStartup]


def create_knowledge_startup(settings: Settings) -> KnowledgeStartup:
    """装配知识基础设施检查；Wiki 索引开启时追加独立 Milvus 检查。"""

    token = settings.milvus_token.get_secret_value() if settings.milvus_token else None
    checks: list[StartupCheck] = [
        DatabaseStartupCheck(settings.database_url.get_secret_value()),
        RedisStartupCheck(settings.redis_url.get_secret_value()),
        MilvusStartupCheck(
            uri=settings.milvus_uri,
            token=token,
            collection=settings.milvus_collection,
            embedding_dimension=settings.embedding_dimension,
        ),
    ]
    if settings.wiki_index_enabled:
        checks.append(
            WikiMilvusStartupCheck(
                uri=settings.milvus_uri,
                token=token,
                physical_collection=settings.wiki_milvus_collection_version,
                alias=settings.wiki_milvus_collection_alias,
                embedding_dimension=settings.embedding_dimension,
            )
        )
    return KnowledgeStartup(
        LocalFileStorage(settings.storage_root),
        checks,
    )
