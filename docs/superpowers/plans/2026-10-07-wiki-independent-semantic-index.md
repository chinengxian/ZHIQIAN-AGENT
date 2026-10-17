# Wiki 独立语义索引 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Wiki 建立独立 Milvus Dense/BM25 索引、可信来源复核、可校准拒答和稳定分页，使摘要段落与事实能够被准确检索，并让无关问题返回明确无答案。

**Architecture:** PostgreSQL 继续作为页面、版本、Claim、来源和索引状态的唯一事实源；独立 Milvus collection 只保存可重建的 Wiki 检索投影。页面发布事务写入 Outbox，Worker 完成向量化、替换、flush 与计数校验后把版本标记为 `ready`；搜索并行融合 Dense、BM25 与标题/别名通道，并在返回前执行 PostgreSQL 当前版本、可信 Claim、活动文档版本和内容哈希复核。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 2、Alembic、Celery/Redis、PostgreSQL、PyMilvus 2.6、Pydantic v2、Vue 3、TypeScript、Vitest、Playwright、pytest。

---

## 实施前提与范围边界

- 设计依据：`docs/superpowers/specs/2026-10-07-wiki-independent-semantic-index-design.md`。
- 从当前 `main` 和迁移 `20261007_0004` 开始；执行前运行 `git status --short`，只保留已知工作区改动。
- 现有原文 `knowledge_chunks` collection 不改 schema；Wiki 使用独立物理 collection `wiki_units_v1` 和逻辑 alias `wiki_units`。
- 首次上线默认 `wiki_index_enabled=false`、`wiki_search_backend=legacy`；回填后先切 `shadow`，质量门通过后才切 `independent`。
- 历史回填只读取现有 PostgreSQL Claim 和 chunk，不调用聊天模型，不增加 Wiki token。
- 人工 `manual`、`needs_review`、失效来源、非当前页面版本和目录页不进入索引。
- 所有新增代码注释使用中文；每个工作包使用中文 Git 提交说明。

## 文件结构与职责

| 文件 | 操作 | 单一职责 |
| --- | --- | --- |
| `migrations/versions/20261007_0005_wiki_independent_index.py` | 新建 | 增加页面版本索引状态和 Wiki 索引任务字段 |
| `src/agent_api/core/config.py` | 修改 | Wiki 索引、后端选择、阈值和 collection alias 配置 |
| `src/agent_api/knowledge/infrastructure/database/models.py` | 修改 | 映射迁移后的字段和约束 |
| `src/agent_api/knowledge/infrastructure/startup.py` | 修改 | 检查数据库迁移并创建/校验 Wiki collection 与 alias |
| `src/agent_api/knowledge/infrastructure/milvus/wiki_index.py` | 新建 | Wiki collection schema、写入、计数、删除、Dense/BM25 查询、alias 切换 |
| `src/agent_api/knowledge/wiki/indexing.py` | 新建 | 构造知识单元、Embedding 指纹和页面版本索引状态机 |
| `src/agent_api/knowledge/wiki/index_jobs.py` | 新建 | 创建索引任务、Outbox、回填、重试和 advisory lock |
| `src/agent_api/knowledge/wiki/read_model.py` | 新建 | 批量复核当前版本/来源并生成原文摘录与高亮坐标 |
| `src/agent_api/knowledge/wiki/relevance.py` | 新建 | 相关性阈值、无答案判定和 Cross-Encoder 分数协议 |
| `src/agent_api/knowledge/wiki/cursor.py` | 新建 | 编解码并校验不透明搜索游标 |
| `src/agent_api/knowledge/wiki/independent_retrieval.py` | 新建 | 三路召回、RRF、可信复核、去重、分页和响应组装 |
| `src/agent_api/knowledge/wiki/retrieval.py` | 修改 | 保留 legacy 实现并增加 legacy/shadow/independent 路由门面 |
| `src/agent_api/knowledge/wiki/processor.py` | 修改 | 自动发布、主题重建和目录重建后调度索引 |
| `src/agent_api/knowledge/wiki/service.py` | 修改 | 审核、回滚、索引重试和知识库回填入口 |
| `src/agent_api/knowledge/wiki/api.py` | 修改 | 搜索、来源摘录、索引状态、重试和回填 API 契约 |
| `src/agent_api/knowledge/domain/events.py` | 修改 | 新增 Wiki 页面索引与旧版本清理 Outbox 事件 |
| `src/agent_api/knowledge/worker/celery_app.py` | 修改 | 消费索引事件并恢复超时索引任务 |
| `src/agent_api/knowledge/worker/celery_factory.py` | 修改 | 注册索引恢复周期任务 |
| `src/agent_api/core/lifespan.py` | 修改 | 装配独立索引、独立检索与后端路由 |
| `src/agent_api/knowledge/tools/readonly.py` | 修改 | Agent 识别无答案、降级和分页后的响应 |
| `web/src/types/knowledge.ts` | 修改 | 新搜索、索引状态、来源摘录与游标类型 |
| `web/src/services/knowledgeApi.ts` | 修改 | 搜索分页、索引重试和回填请求 |
| `web/src/views/knowledge/WikiPanel.vue` | 修改 | 无答案、加载更多、命中原因、索引状态和精确来源展示 |
| `web/src/components/knowledge/documentPreview.ts` | 修改 | 安全拆分高亮文本 |
| `web/src/components/knowledge/DocumentDetailPanel.vue` | 修改 | 使用 `<mark>` 渲染并滚动到目标父块 |
| `tests/`、`web/tests/` | 修改/新建 | 单元、真实集成、固定评测和浏览器闭环 |

## Task 1：数据库索引状态与任务契约

**Files:**
- Create: `migrations/versions/20261007_0005_wiki_independent_index.py`
- Modify: `src/agent_api/knowledge/infrastructure/database/models.py`
- Modify: `src/agent_api/knowledge/infrastructure/startup.py`
- Test: `tests/test_knowledge_models.py`
- Test: `tests/test_knowledge_startup.py`

- [ ] **Step 1: 写失败测试，固定字段、约束和期望迁移版本**

```python
def test_wiki_page_version_exposes_index_lifecycle_fields() -> None:
    columns = WikiPageVersion.__table__.columns
    assert {"index_state", "embedding_fingerprint", "indexed_at", "index_error_code"} <= set(columns)
    assert columns["index_state"].default.arg == "not_required"


def test_wiki_job_can_target_one_page_version() -> None:
    columns = WikiJob.__table__.columns
    assert {"page_version_id", "embedding_fingerprint", "error_reference"} <= set(columns)
```

同时把 `tests/test_knowledge_startup.py` 中期望 revision 改为 `20261007_0005`。

- [ ] **Step 2: 运行测试并确认按预期失败**

Run: `uv run pytest tests/test_knowledge_models.py tests/test_knowledge_startup.py -q`

Expected: FAIL，提示缺少 `index_state` 或期望 revision 仍为 `20261007_0004`。

- [ ] **Step 3: 新增 Alembic 迁移**

迁移必须使用以下字段和回填规则：

```python
revision = "20261007_0005"
down_revision = "20261007_0004"


def upgrade() -> None:
    op.add_column(
        "wiki_page_versions",
        sa.Column("index_state", sa.String(length=16), server_default="not_required", nullable=False),
    )
    op.add_column("wiki_page_versions", sa.Column("embedding_fingerprint", sa.Text(), nullable=True))
    op.add_column("wiki_page_versions", sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("wiki_page_versions", sa.Column("index_error_code", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_wiki_page_version_index_state",
        "wiki_page_versions",
        "index_state IN ('not_required', 'pending', 'indexing', 'ready', 'failed')",
    )
    op.add_column(
        "wiki_jobs",
        sa.Column(
            "page_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wiki_page_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("wiki_jobs", sa.Column("embedding_fingerprint", sa.Text(), nullable=True))
    op.add_column("wiki_jobs", sa.Column("error_reference", sa.Text(), nullable=True))
    op.create_index(
        "ix_wiki_jobs_page_version_status",
        "wiki_jobs",
        ["page_version_id", "status"],
    )
```

迁移不把历史版本直接置为 `pending`，避免功能开关仍关闭时产生永不消费的假积压；Task 5 的显式无模型回填负责把符合条件的历史当前版本转为 `pending` 并创建任务。`downgrade()` 按反序删除索引、外键字段、状态约束和版本字段；不得删除页面、Claim 或来源数据。

- [ ] **Step 4: 映射模型并更新启动 revision**

在 `WikiPageVersion` 中增加上述四个字段，在 `WikiJob` 中增加上述三个字段；每个字段写中文注释。把 `EXPECTED_DATABASE_REVISION` 更新为 `20261007_0005`。

- [ ] **Step 5: 验证迁移与模型测试**

Run: `uv run pytest tests/test_knowledge_models.py tests/test_knowledge_startup.py -q`

Expected: PASS。

Run: `uv run alembic upgrade head`

Expected: 数据库 revision 为 `20261007_0005`，现有 Wiki 页面、版本和 Claim 数量不变。

- [ ] **Step 6: 提交**

```powershell
git add migrations/versions/20261007_0005_wiki_independent_index.py src/agent_api/knowledge/infrastructure/database/models.py src/agent_api/knowledge/infrastructure/startup.py tests/test_knowledge_models.py tests/test_knowledge_startup.py
git commit -m "数据库：增加Wiki索引状态与任务字段"
```

## Task 2：配置、指纹与安全默认值

**Files:**
- Modify: `src/agent_api/core/config.py`
- Modify: `.env.example`
- Create: `src/agent_api/knowledge/wiki/indexing.py`
- Test: `tests/test_knowledge_config.py`
- Test: `tests/test_config.py`
- Create (test): `tests/test_wiki_indexing.py`

- [ ] **Step 1: 写失败测试，固定默认后端和阈值校验**

```python
def test_wiki_index_defaults_to_disabled_legacy_backend(tmp_path: Path) -> None:
    settings = knowledge_settings(tmp_path)
    assert settings.wiki_index_enabled is False
    assert settings.wiki_search_backend.value == "legacy"
    assert settings.wiki_milvus_collection_alias == "wiki_units"


@pytest.mark.parametrize(
    "field,value",
    [
        ("wiki_dense_threshold", -0.01),
        ("wiki_rerank_threshold", 1.01),
        ("wiki_keyword_coverage_threshold", 1.5),
    ],
)
def test_wiki_thresholds_must_be_probabilities(
    tmp_path: Path, field: str, value: float
) -> None:
    with pytest.raises(ValidationError):
        knowledge_settings(tmp_path, **{field: value})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_knowledge_config.py tests/test_config.py -q`

Expected: FAIL，Settings 尚无 Wiki 独立索引字段。

- [ ] **Step 3: 增加配置枚举与字段**

```python
class WikiSearchBackend(StrEnum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    INDEPENDENT = "independent"


wiki_index_enabled: bool = False
wiki_search_backend: WikiSearchBackend = WikiSearchBackend.LEGACY
wiki_milvus_collection_alias: str = "wiki_units"
wiki_milvus_collection_version: str = "wiki_units_v1"
wiki_dense_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
wiki_rerank_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
wiki_keyword_coverage_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
wiki_index_protocol_version: str = "wiki-index-v1"
wiki_relevance_profile: str = "wiki-relevance-v1"
```

知识库启用且 `wiki_index_enabled=true` 时，collection alias、物理 collection 和协议版本不得为空；后端为 `shadow/independent` 时必须同时启用 `wiki_index_enabled`。`.env.example` 增加同名 `AGENT_` 环境变量，并用中文说明 `legacy → shadow → independent` 顺序。

- [ ] **Step 4: 增加稳定 Embedding 指纹测试与实现**

在 `src/agent_api/knowledge/wiki/indexing.py` 先增加纯函数：

```python
def embedding_fingerprint(settings: Settings) -> str:
    payload = "|".join(
        (
            settings.embedding_provider.value,
            settings.embedding_model or "",
            str(settings.embedding_dimension),
            settings.wiki_index_protocol_version,
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()
```

测试相同配置得到相同指纹，模型、维度或协议版本任一变化都会改变指纹。相关性策略另生成包含 Embedding 指纹、Reranker provider/model、三个阈值和 `wiki_relevance_profile` 的策略指纹；固定评测报告必须记录该值，模型或 profile 变化后必须重新过质量门。

- [ ] **Step 5: 运行配置测试并提交**

Run: `uv run pytest tests/test_knowledge_config.py tests/test_config.py tests/test_wiki_indexing.py -q`

Expected: PASS。

```powershell
git add .env.example src/agent_api/core/config.py src/agent_api/knowledge/wiki/indexing.py tests/test_config.py tests/test_knowledge_config.py tests/test_wiki_indexing.py
git commit -m "配置：增加Wiki独立索引开关与指纹"
```

## Task 3：独立 Milvus Wiki collection 与 alias

**Files:**
- Create: `src/agent_api/knowledge/infrastructure/milvus/wiki_index.py`
- Modify: `src/agent_api/knowledge/infrastructure/milvus/__init__.py`
- Modify: `src/agent_api/knowledge/infrastructure/startup.py`
- Create (test): `tests/test_wiki_milvus_index.py`
- Test: `tests/test_knowledge_startup.py`

- [ ] **Step 1: 写 schema 和适配器失败测试**

```python
def test_wiki_schema_contains_identity_content_dense_and_bm25_fields() -> None:
    schema = build_wiki_milvus_schema(1024).to_dict()
    fields = {field["name"] for field in schema["fields"]}
    assert fields == {
        "unit_id", "knowledge_base_id", "page_id", "page_version_id",
        "claim_id", "unit_type", "content", "dense_vector", "sparse_vector",
    }


async def test_replace_page_version_flushes_and_counts_units() -> None:
    client = FakeMilvusClient()
    index = MilvusWikiIndex(client, "wiki_units")
    await index.replace_page_version(VERSION_ID, [indexed_unit()])
    assert client.deleted_filters == [f'page_version_id == "{VERSION_ID}"']
    assert client.flush_calls == ["wiki_units"]
    assert await index.count_page_version(VERSION_ID) == 1
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_wiki_milvus_index.py tests/test_knowledge_startup.py -q`

Expected: FAIL，`wiki_index` 模块尚不存在。

- [ ] **Step 3: 实现 schema、数据结构和索引参数**

```python
@dataclass(frozen=True, slots=True)
class WikiIndexedUnit:
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
    unit_id: UUID
    page_id: UUID
    page_version_id: UUID
    claim_id: UUID
    unit_type: str
    content: str
    score: float
    channel: Literal["dense", "keyword"]
```

`build_wiki_milvus_schema()` 使用 `unit_id` 主键、`content` BM25 Function、Dense HNSW 和 Sparse inverted index。所有 UUID 先由 Python `UUID` 解析，再拼接 Milvus filter；任何自由文本不得进入 filter。

- [ ] **Step 4: 实现写入和两路独立查询**

`MilvusWikiIndex` 提供以下完整接口：

```python
class MilvusWikiIndex:
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
        self, page_version_id: UUID, units: Sequence[WikiIndexedUnit]
    ) -> None: ...

    async def count_page_version(self, page_version_id: UUID) -> int: ...

    async def delete_page_version(self, page_version_id: UUID) -> None: ...

    async def search_dense(
        self, vector: list[float], knowledge_base_ids: tuple[UUID, ...], limit: int
    ) -> list[WikiIndexHit]: ...

    async def search_keyword(
        self, query: str, knowledge_base_ids: tuple[UUID, ...], limit: int
    ) -> list[WikiIndexHit]: ...

    async def switch_alias(self, physical_collection: str, alias: str) -> None: ...
```

实现中 `replace_page_version/count/delete` 只操作 `write_collection`，搜索只读 `search_collection` alias。`replace_page_version` 必须先删当前版本、再插入、再 flush；Dense 和 BM25 分开查询以保留通道原始分数。`switch_alias` 先 `describe_alias(alias=alias)`，不存在时 `create_alias(collection_name=physical_collection, alias=alias)`，存在时才显式调用 `alter_alias(collection_name=physical_collection, alias=alias)`。

- [ ] **Step 5: 把 Wiki collection 接入启动检查**

`create_knowledge_startup()` 在 `wiki_index_enabled=true` 时增加第二个 Milvus 检查：创建或校验 `wiki_milvus_collection_version`。alias 不存在时可以首次创建并指向该物理 collection；alias 已存在时只校验其目标 collection 存在且 schema/维度兼容，普通启动不得调用 `alter_alias`。原文 collection 检查保持不变，alias 切换只允许在 Task 11 质量门通过后显式执行。

- [ ] **Step 6: 运行测试并提交**

Run: `uv run pytest tests/test_wiki_milvus_index.py tests/test_knowledge_startup.py -q`

Expected: PASS，且测试证明启动检查不会删除或覆盖已有 collection。

```powershell
git add src/agent_api/knowledge/infrastructure/milvus src/agent_api/knowledge/infrastructure/startup.py tests/test_wiki_milvus_index.py tests/test_knowledge_startup.py
git commit -m "索引：增加Wiki独立Milvus集合"
```

## Task 4：知识单元投影与索引状态机

**Files:**
- Modify: `src/agent_api/knowledge/wiki/indexing.py`
- Test: `tests/test_wiki_indexing.py`

- [ ] **Step 1: 写知识单元构造失败测试**

```python
def test_build_units_indexes_only_verified_supported_claims() -> None:
    units = build_index_units(
        page=page(title="身份认证", page_type="summary"),
        version=version(),
        aliases=("MFA", "双因素校验"),
        claims=(
            claim("概述", "summary_section", "verified"),
            claim("必须启用 MFA", "summary_fact", "verified"),
            claim("人工补充", "manual", "verified"),
            claim("旧事实", "summary_fact", "needs_review"),
        ),
        source_paths={CLAIM_ID: ("身份规范 / 认证",)},
    )
    assert [unit.unit_type for unit in units] == ["summary_section", "summary_fact"]
    assert "MFA" in units[0].content
    assert "人工补充" not in "\n".join(unit.content for unit in units)
```

- [ ] **Step 2: 写状态竞争与完整性失败测试**

覆盖以下结果：

1. `pending → indexing → ready` 保存指纹和 `indexed_at`；
2. Milvus 数量与预期不一致时进入 `failed/index_count_mismatch`；
3. Worker 写入后发现 `page.current_version_id` 已变化，删除刚写入单元且不标记 `ready`；
4. 相同 `wiki-index:{page_version_id}:{fingerprint}` 重复执行不产生重复单元；
5. 人工版本和待审核版本保持 `not_required`；
6. 没有可索引可信 Claim 的版本回到 `not_required`，任务以 `no_indexable_units` 成功结束。

- [ ] **Step 3: 运行测试并确认失败**

Run: `uv run pytest tests/test_wiki_indexing.py -q`

Expected: FAIL，索引状态机尚未实现。

- [ ] **Step 4: 实现确定性的索引文本协议**

```python
def build_unit_content(
    *, page_title: str, page_type: str, aliases: Sequence[str],
    section_title: str | None, claim_text: str, source_paths: Sequence[str]
) -> str:
    parts = [
        page_title.strip(),
        f"页面类型：{page_type}",
        f"主题别名：{'、'.join(alias.strip() for alias in aliases if alias.strip())}",
        f"段落标题：{section_title.strip()}" if section_title else "",
        claim_text.strip(),
        f"来源路径：{'；'.join(path.strip() for path in source_paths if path.strip())}",
    ]
    return "\n".join(part for part in parts if part)
```

`build_index_units()` 只接受 `summary_section / summary_fact / topic_fact` 且 `trust_state=verified` 的 Claim。输入按 Claim `position, id` 排序，输出 unit ID 等于 Claim ID。

- [ ] **Step 5: 实现 `WikiIndexService.run(job_id)`**

状态机严格按以下顺序执行：

```text
锁定 job 和 page_version
校验 job.status 可执行、page.current_version_id 等于目标版本、version.index_state 为 pending/failed
写入 job=running、stage=loading、progress=10、version=indexing、heartbeat_at=now
批量读取 aliases、可信 claims、claim sources、chunks
构造文本，stage=embedding、progress=35，批量调用 embed_documents
stage=writing、progress=70，replace_page_version → flush
stage=verifying、progress=90，count_page_version
重新锁定并复核当前版本与 Claim/来源有效性
成功：version=ready、job=succeeded、progress=100
失败：version=failed、job=retry_wait 或 failed、保存稳定 error_code/error_reference
版本过期：delete_page_version、job=succeeded/stage=superseded，版本不得 ready
```

日志仅记录 job/page/version ID、计数、阶段和查询指纹，不记录 Claim 正文或完整问题。

- [ ] **Step 6: 运行测试并提交**

Run: `uv run pytest tests/test_wiki_indexing.py -q`

Expected: PASS。

```powershell
git add src/agent_api/knowledge/wiki/indexing.py tests/test_wiki_indexing.py
git commit -m "索引：实现Wiki知识单元与状态机"
```

## Task 5：发布触发、Outbox、重试与无模型回填

**Files:**
- Create: `src/agent_api/knowledge/wiki/index_jobs.py`
- Modify: `src/agent_api/knowledge/domain/events.py`
- Modify: `src/agent_api/knowledge/wiki/processor.py`
- Modify: `src/agent_api/knowledge/wiki/service.py`
- Modify: `src/agent_api/knowledge/wiki/api.py`
- Modify: `src/agent_api/knowledge/worker/celery_app.py`
- Modify: `src/agent_api/knowledge/worker/celery_factory.py`
- Test: `tests/test_outbox_event_types.py`
- Test: `tests/test_wiki_processor.py`
- Test: `tests/test_wiki_api.py`
- Create (test): `tests/test_wiki_index_jobs.py`

- [ ] **Step 1: 写发布、审核、回滚和回填失败测试**

断言以下行为：

```python
assert published_pipeline_version_with_index_enabled.index_state == "pending"
assert published_pipeline_version_with_index_disabled.index_state == "not_required"
assert pending_review_version.index_state == "not_required"
assert published_user_version.index_state == "not_required"
assert approved_pipeline_version.index_state == "pending"
assert reverted_grounded_version.index_state == "pending"
assert outbox.event_type == OutboxEventType.WIKI_PAGE_INDEX_REQUESTED
assert outbox.payload == {"job_id": str(index_job.id)}
```

回填测试创建两个当前可信版本和一个人工版本，断言只创建两个幂等任务，重复执行返回 `created=0, existing=2`，Wiki token 计数不变。

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_outbox_event_types.py tests/test_wiki_processor.py tests/test_wiki_api.py tests/test_wiki_index_jobs.py -q`

Expected: FAIL，缺少 `WIKI_PAGE_INDEX_REQUESTED` 和索引任务入口。

- [ ] **Step 3: 实现事务内调度函数**

```python
async def schedule_page_index(
    session: AsyncSession,
    *,
    knowledge_base_id: UUID,
    page_version_id: UUID,
    fingerprint: str,
) -> UUID | None:
    key = f"wiki-index:{page_version_id}:{fingerprint}"
    job_id = uuid4()
    inserted = await session.scalar(
        pg_insert(WikiJob)
        .values(
            id=job_id,
            knowledge_base_id=knowledge_base_id,
            page_version_id=page_version_id,
            job_type="page_index",
            status="pending",
            stage="pending",
            progress=0,
            attempt_count=0,
            max_attempts=5,
            reserved_tokens=0,
            embedding_fingerprint=fingerprint,
            idempotency_key=key,
        )
        .on_conflict_do_nothing(index_elements=[WikiJob.idempotency_key])
        .returning(WikiJob.id)
    )
    if inserted is None:
        return None
    session.add(
        OutboxEvent(
            event_key=key,
            event_type=OutboxEventType.WIKI_PAGE_INDEX_REQUESTED,
            aggregate_id=inserted,
            payload={"job_id": str(inserted)},
        )
    )
    return inserted
```

页面发布事务只有在版本成为当前发布版、含可信来源且 `wiki_index_enabled=true` 时调用该函数。`WikiService` 与 `WikiProcessor` 构造函数接收 `index_enabled` 和 `embedding_fingerprint`，由 API lifespan 与 Wiki 生成 Worker 从同一份 `Settings` 注入，禁止在业务层重新读取环境变量。

同时增加 `WIKI_PAGE_INDEX_DELETE_REQUESTED`。新版本成功标记 `ready` 后，在同一 PostgreSQL 事务为该页面所有非当前 `ready/failed` 旧版本写入清理事件；清理事件只携带 `page_version_id`，Worker 调用 `delete_page_version()`，成功后不删除 PostgreSQL 历史版本。

- [ ] **Step 4: 接入 Worker、退避和超时恢复**

`_process_event()` 为 `WIKI_PAGE_INDEX_REQUESTED` 创建 `DatabaseRuntime`、`MilvusClient`、`MilvusWikiIndex`、Embedding adapter 和 `WikiIndexService`；为 `WIKI_PAGE_INDEX_DELETE_REQUESTED` 只创建数据库与 Wiki index，删除目标页面版本单元。索引失败时使用 `min(300, 2 ** min(attempt_count, 8))` 秒退避；达到 5 次后状态为 `failed`。Beat 每 60 秒恢复心跳超过 5 分钟的 `page_index` 任务。

- [ ] **Step 5: 增加单页重试和知识库回填 API**

新增接口：

```text
POST /api/v1/knowledge-bases/{kb}/wiki/pages/{page}/index/retry
POST /api/v1/knowledge-bases/{kb}/wiki/index/backfill
```

回填在事务中执行 `pg_try_advisory_xact_lock(hashtextextended('wiki-index:' || :kb, 0))`；拿不到锁返回 HTTP 409 `wiki_index_rebuild_in_progress`。响应包含 `created_jobs`、`existing_jobs`、`skipped_versions`，不得返回正文。

单页重试只接受当前版本且状态为 `failed` 的页面；其他状态返回 HTTP 409 `wiki_index_retry_not_allowed`。`WikiPageSummary` 增加当前版本的 `index_state/index_error_code`，`WikiJobResponse` 增加 `page_version_id/error_reference/embedding_fingerprint`，`list_pages()` 使用当前版本联接一次返回，不能逐页查询。

- [ ] **Step 6: 运行测试并提交**

Run: `uv run pytest tests/test_outbox_event_types.py tests/test_wiki_processor.py tests/test_wiki_api.py tests/test_wiki_index_jobs.py tests/test_knowledge_worker.py -q`

Expected: PASS。

```powershell
git add src/agent_api/knowledge/domain/events.py src/agent_api/knowledge/wiki/index_jobs.py src/agent_api/knowledge/wiki/processor.py src/agent_api/knowledge/wiki/service.py src/agent_api/knowledge/wiki/api.py src/agent_api/knowledge/worker/celery_app.py src/agent_api/knowledge/worker/celery_factory.py tests/test_outbox_event_types.py tests/test_wiki_processor.py tests/test_wiki_api.py tests/test_wiki_index_jobs.py tests/test_knowledge_worker.py
git commit -m "任务：接通Wiki索引发布重试与回填"
```

## Task 6：可信读取模型、精确摘录与批量来源复核

**Files:**
- Create: `src/agent_api/knowledge/wiki/read_model.py`
- Modify: `src/agent_api/knowledge/wiki/service.py`
- Modify: `src/agent_api/knowledge/wiki/retrieval.py`
- Create (test): `tests/test_wiki_read_model.py`
- Test: `tests/test_wiki_presentation.py`

- [ ] **Step 1: 写来源有效性和摘录失败测试**

```python
def test_build_source_excerpt_uses_parent_and_child_offsets() -> None:
    parent = chunk(id=PARENT_ID, content="前文。必须启用两个独立认证因素。后文。")
    child = chunk(
        id=CHILD_ID,
        parent_chunk_id=PARENT_ID,
        content="必须启用两个独立认证因素。",
    )
    excerpt = build_source_excerpt(parent, child)
    assert excerpt.parent_chunk_id == PARENT_ID
    assert parent.content[excerpt.focus_start:excerpt.focus_end] == child.content
    assert child.content in excerpt.excerpt
```

数据库测试必须证明旧页面版本、`needs_review`、非活动文档版本、chunk kind/ordinal/hash 不一致均被过滤，且 20 个 Claim 的来源读取使用固定批次数而不是逐条查询。

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_wiki_read_model.py tests/test_wiki_presentation.py -q`

Expected: FAIL，`read_model` 尚不存在。

- [ ] **Step 3: 实现批量可信复核结果**

```python
@dataclass(frozen=True, slots=True)
class VerifiedWikiSource:
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID | None
    parent_chunk_id: UUID | None
    document_title: str
    filename: str
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    excerpt: str
    focus_start: int | None
    focus_end: int | None
    valid: bool
    invalid_reason: str | None


@dataclass(frozen=True, slots=True)
class WikiMetadataHit:
    page_id: UUID
    exact: bool
    matched_field: Literal["title", "alias", "slug"]


@dataclass(frozen=True, slots=True)
class VerifiedWikiClaim:
    claim_id: UUID
    page_id: UUID
    page_version_id: UUID
    page_title: str
    page_slug: str
    page_type: str
    claim_type: str
    text: str
    position: int
    sources: tuple[VerifiedWikiSource, ...]
```

`WikiReadModel.load_verified_claims(claim_ids, scope_ids, require_index_ready)` 使用一次主查询和一次父块查询完成读取。校验顺序固定为：页面当前版本、按参数选择是否要求 `index_state=ready`、Claim verified、文档 ready/未删除、活动版本一致、chunk kind、ordinal、SHA-256。独立检索传 `require_index_ready=True`；legacy 检索和页面展示传 `False`，保证安全默认值下现有 Wiki 仍可用。搜索只返回 `valid=True`；页面展示可返回失效来源及稳定 `invalid_reason`。

`WikiReadModel.search_metadata(query, scope_ids, limit)` 返回 `WikiMetadataHit`，只允许规范化标题/别名精确匹配和 slug/标题/别名前缀匹配，并按 exact、字段优先级、页面 ID 稳定排序；不得读取摘要相似度作为元数据候选。

- [ ] **Step 4: 实现摘录与高亮坐标**

若来源 chunk 是子块，优先使用已经持久化的 `char_start/char_end`；只有坐标越界或对应正文不一致时才在父块正文中查找子块正文，仍找不到则退回子块自身。`excerpt` 取命中前后各 180 个字符；`focus_start/focus_end` 始终相对于 `parent_chunk_id` 指向的完整正文，不能相对于截断摘录。

- [ ] **Step 5: 替换页面详情和 legacy 搜索中的 N+1 读取**

`WikiService.get_page()` 和 legacy `_hydrate_pages()` 改为调用 `WikiReadModel(require_index_ready=False)`。保持现有页面内容和 Claim 顺序不变，同时让 `WikiSourceResponse` 与 `WikiSearchSourceResponse` 获得 `parent_chunk_id/excerpt/focus_start/focus_end/invalid_reason`。

- [ ] **Step 6: 运行测试并提交**

Run: `uv run pytest tests/test_wiki_read_model.py tests/test_wiki_presentation.py tests/test_wiki_retrieval.py tests/test_wiki_api.py -q`

Expected: PASS，旧搜索行为除来源字段增强外保持不变。

```powershell
git add src/agent_api/knowledge/wiki/read_model.py src/agent_api/knowledge/wiki/service.py src/agent_api/knowledge/wiki/retrieval.py tests/test_wiki_read_model.py tests/test_wiki_presentation.py tests/test_wiki_retrieval.py tests/test_wiki_api.py
git commit -m "来源：批量复核Wiki引用并生成精确摘录"
```

## Task 7：相关性策略、去重和稳定游标

**Files:**
- Create: `src/agent_api/knowledge/wiki/relevance.py`
- Create: `src/agent_api/knowledge/wiki/cursor.py`
- Modify: `src/agent_api/knowledge/wiki/ranking.py`
- Modify: `src/agent_api/knowledge/infrastructure/rerank/bge.py`
- Create (test): `tests/test_wiki_relevance.py`
- Create (test): `tests/test_wiki_cursor.py`
- Test: `tests/test_wiki_ranking.py`

- [ ] **Step 1: 写评分、拒答、去重和游标失败测试**

```python
UNIT_ID = uuid4()


def candidate(
    *, dense: float | None, keyword: float, rerank: float | None, exact: bool
) -> WikiScoredCandidate:
    return WikiScoredCandidate(
        unit_id=UNIT_ID,
        page_id=uuid4(),
        claim_id=uuid4(),
        rrf_score=0.1,
        dense_score=dense,
        keyword_score=keyword,
        keyword_coverage=keyword,
        rerank_score=rerank,
        exact_metadata_match=exact,
    )


def test_policy_rejects_rrf_candidate_without_relevance_evidence() -> None:
    policy = WikiRelevancePolicy(dense_threshold=0.55, rerank_threshold=0.50, keyword_threshold=0.50)
    assert policy.accept(candidate(dense=0.20, keyword=0.0, rerank=None, exact=False)) is False


def test_exact_alias_is_accepted_without_dense_score() -> None:
    policy = WikiRelevancePolicy(dense_threshold=0.55, rerank_threshold=0.50, keyword_threshold=0.50)
    assert policy.accept(candidate(dense=None, keyword=0.0, rerank=None, exact=True)) is True


def test_cursor_rejects_query_or_collection_change() -> None:
    token = encode_cursor(SearchCursor("query-a", "wiki_units_v1", 0.8, UNIT_ID))
    with pytest.raises(WikiCursorError, match="wiki_search_cursor_expired"):
        decode_cursor(token, query_fingerprint="query-b", collection_version="wiki_units_v1")
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_wiki_relevance.py tests/test_wiki_cursor.py tests/test_wiki_ranking.py -q`

Expected: FAIL，新模块尚不存在。

- [ ] **Step 3: 增加实际分数协议**

```python
@dataclass(frozen=True, slots=True)
class WikiScoredCandidate:
    unit_id: UUID
    page_id: UUID
    claim_id: UUID
    rrf_score: float
    dense_score: float | None
    keyword_score: float | None
    keyword_coverage: float
    rerank_score: float | None
    exact_metadata_match: bool


@dataclass(frozen=True, slots=True)
class SearchCursor:
    query_fingerprint: str
    collection_version: str
    last_score: float
    last_unit_id: UUID


@dataclass(frozen=True, slots=True)
class PageClaimCursor:
    page_version_id: UUID
    last_position: int
    last_claim_id: UUID


class WikiCursorError(ValueError):
    """Wiki 游标与当前查询或页面版本不兼容。"""


class WikiScorer(Protocol):
    async def score(self, query: str, texts: list[str]) -> list[float]: ...
```

`LocalBGEReranker.score()` 返回与输入等长的 `float` 列表；现有 `rerank()` 调用 `score()` 后返回索引顺序，保持原文 RAG 兼容。

- [ ] **Step 4: 实现 `WikiRelevancePolicy`**

```python
def accept(self, candidate: WikiScoredCandidate) -> bool:
    if candidate.exact_metadata_match:
        return True
    if candidate.rerank_score is not None:
        return candidate.rerank_score >= self.rerank_threshold
    dense_ok = candidate.dense_score is not None and candidate.dense_score >= self.dense_threshold
    keyword_ok = candidate.keyword_coverage >= self.keyword_threshold
    return dense_ok or keyword_ok
```

策略同时返回稳定拒绝原因：`below_dense_threshold`、`below_rerank_threshold`、`below_keyword_coverage` 或 `below_relevance_threshold`。

- [ ] **Step 5: 实现事实指纹与 cursor**

事实指纹输入为 NFKC/casefold/空白归一文本、排序后的来源 chunk ID、排序后的活动文档版本 ID。Cursor 使用 URL-safe base64 编码 JSON：`query_fingerprint`、`collection_version`、`last_score`、`last_unit_id`；解码时限制 2KB、校验类型、查询和 collection，一项不符就抛 `wiki_search_cursor_expired`。

- [ ] **Step 6: 运行测试并提交**

Run: `uv run pytest tests/test_wiki_relevance.py tests/test_wiki_cursor.py tests/test_wiki_ranking.py tests/test_hybrid_retrieval.py -q`

Expected: PASS，现有原文 rerank 测试保持通过。

```powershell
git add src/agent_api/knowledge/wiki/relevance.py src/agent_api/knowledge/wiki/cursor.py src/agent_api/knowledge/wiki/ranking.py src/agent_api/knowledge/infrastructure/rerank/bge.py tests/test_wiki_relevance.py tests/test_wiki_cursor.py tests/test_wiki_ranking.py tests/test_hybrid_retrieval.py
git commit -m "检索：增加Wiki相关性拒答与稳定游标"
```

## Task 8：独立检索、Shadow 对比与 API/Agent 契约

**Files:**
- Create: `src/agent_api/knowledge/wiki/independent_retrieval.py`
- Modify: `src/agent_api/knowledge/wiki/retrieval.py`
- Modify: `src/agent_api/knowledge/wiki/api.py`
- Modify: `src/agent_api/knowledge/tools/readonly.py`
- Modify: `src/agent_api/core/lifespan.py`
- Modify: `src/agent_api/knowledge/infrastructure/observability/events.py`
- Create (test): `tests/test_wiki_independent_retrieval.py`
- Test: `tests/test_wiki_retrieval.py`
- Test: `tests/test_wiki_api.py`
- Test: `tests/test_knowledge_tools.py`
- Test: `tests/test_lifespan.py`

- [ ] **Step 1: 写三路融合、拒答、分页和 Shadow 失败测试**

测试必须覆盖：

```python
assert result.no_answer is True
assert result.no_answer_reason == "below_relevance_threshold"
assert result.pages == ()

assert accepted.matched_fields == ("dense", "keyword", "alias")
assert first_page.next_cursor is not None
assert second_page.pages[0].id not in {page.id for page in first_page.pages}
assert shadow_result == legacy_result
assert shadow_metrics.independent_page_ids != shadow_metrics.legacy_page_ids
```

另测 Milvus 返回旧版本、`index_state != ready`、失效来源和哈希不符时结果为空。

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_wiki_independent_retrieval.py tests/test_wiki_retrieval.py tests/test_wiki_api.py tests/test_knowledge_tools.py tests/test_lifespan.py -q`

Expected: FAIL，独立检索和新响应字段尚不存在。

- [ ] **Step 3: 实现并行候选召回与 RRF**

`WikiIndependentRetrievalService.search()` 使用 `asyncio.gather` 并行执行；元数据通道只做规范化标题/别名精确或前缀匹配，不再用摘要 trigram 相似度制造弱候选：

```python
dense_hits, keyword_hits, metadata_hits = await asyncio.gather(
    self._index.search_dense(vector, scope_ids, candidate_limit),
    self._index.search_keyword(normalized_query, scope_ids, candidate_limit),
    self._read_model.search_metadata(normalized_query, scope_ids, candidate_limit),
)
```

默认 `candidate_limit=max(page_limit * 10, 50)`，按 unit ID 执行 RRF，保留 Dense/BM25 原始分数和通道。metadata page 命中展开为该页当前 `ready` 版本中最相关的可信知识单元。

- [ ] **Step 4: 复核、评分、去重并分页**

批量回查 PostgreSQL 后：

1. 丢弃所有不可信命中；
2. 可用 scorer 时批量评分，失败则 `degraded=true/reranker_unavailable`；
3. 调用 `WikiRelevancePolicy`；
4. 无候选通过时返回 `no_answer=true`；
5. 使用事实指纹去重，并填充 `also_in_pages`；
6. 主页面按精确标题/别名、多来源主题页、单来源摘要页、分数、稳定 ID 排序；
7. 使用 cursor seek 分页，不使用 offset。

- [ ] **Step 5: 实现后端路由门面**

保留现有实现并改名为 `LegacyWikiRetrievalService`。新的 `WikiRetrievalService` 接收 `legacy`、`independent`、`backend`：

```python
if backend is WikiSearchBackend.LEGACY:
    if cursor is not None:
        raise WikiCursorError("wiki_search_cursor_expired")
    return await legacy.search(
        query,
        scope_ids,
        mode=mode,
        page_limit=page_limit,
        claim_limit=claim_limit,
    )
if backend is WikiSearchBackend.INDEPENDENT:
    return await independent.search(
        query,
        scope_ids,
        mode=mode,
        page_limit=page_limit,
        claim_limit=claim_limit,
        cursor=cursor,
    )
legacy_result, independent_result = await asyncio.gather(
    legacy.search(
        query,
        scope_ids,
        mode=mode,
        page_limit=page_limit,
        claim_limit=claim_limit,
    ),
    independent.search(
        query,
        scope_ids,
        mode=mode,
        page_limit=page_limit,
        claim_limit=claim_limit,
        cursor=None,
    ),
    return_exceptions=True,
)
self._log_shadow_diff(legacy_result, independent_result)
return legacy_result
```

Shadow 日志只记录查询指纹、页面/Claim ID、耗时、拒答和降级原因，不记录查询原文或正文。同步扩展 `src/agent_api/knowledge/infrastructure/observability/events.py`，记录搜索延迟、候选量、无答案率、阈值过滤量、Dense/BM25/别名命中数、Reranker 降级、索引积压和 cursor 过期计数。

- [ ] **Step 6: 扩展 API 与 Agent 响应**

`WikiSearchResponse` 增加 `no_answer`、`no_answer_reason`、`next_cursor`、`collection_version`。请求增加 `cursor`，游标失效映射为 HTTP 409 和 `wiki_search_cursor_expired`。独立检索基础设施失败映射为 HTTP 503，不能伪装成 `no_answer`，也不能在 `independent` 模式静默调用 legacy。`WikiSearchInput` 增加可选 `cursor`，工具响应保留 `next_cursor`，Agent 需要更多证据时可以继续分页。Agent 工具遇到 `no_answer=true` 时返回明确“Wiki 中没有足够相关且可核验的知识”，不得把低分候选拼入回答。

去重后的 `WikiMatchedClaimResponse` 增加 `also_in_pages: list[WikiPageSummary]`；同一事实只出现在主页面，其他命中页面作为跳转信息返回。

页面详情增加事实分页：`GET /wiki/pages/{page_id}?claim_cursor=...&claim_limit=20`。`WikiPageResponse` 增加 `claims_next_cursor`；游标绑定 `page_version_id`、最后 `position` 和 `claim_id`，页面版本变化时返回 `wiki_page_cursor_expired`。摘要段落和元数据仍一次返回，原子事实按 `position, claim_id` 稳定加载。

- [ ] **Step 7: 装配生命周期并运行测试**

`lifespan` 在 Wiki 索引启用时复用同一个 `MilvusClient`，装配 `MilvusWikiIndex`、`WikiReadModel`、independent service 和路由门面；关闭时仍只关闭一次客户端。

Run: `uv run pytest tests/test_wiki_independent_retrieval.py tests/test_wiki_retrieval.py tests/test_wiki_api.py tests/test_knowledge_tools.py tests/test_lifespan.py -q`

Expected: PASS。

- [ ] **Step 8: 提交**

```powershell
git add src/agent_api/knowledge/wiki/independent_retrieval.py src/agent_api/knowledge/wiki/retrieval.py src/agent_api/knowledge/wiki/api.py src/agent_api/knowledge/tools/readonly.py src/agent_api/core/lifespan.py src/agent_api/knowledge/infrastructure/observability/events.py tests/test_wiki_independent_retrieval.py tests/test_wiki_retrieval.py tests/test_wiki_api.py tests/test_knowledge_tools.py tests/test_lifespan.py
git commit -m "检索：接入Wiki独立召回与灰度路由"
```

## Task 9：前端搜索结果、拒答、分页与索引运维

**Files:**
- Modify: `web/src/types/knowledge.ts`
- Modify: `web/src/services/knowledgeApi.ts`
- Modify: `web/src/views/knowledge/WikiPanel.vue`
- Test: `web/tests/knowledge/wikiSearchApi.spec.ts`
- Test: `web/tests/knowledge/wikiPanel.spec.ts`

- [ ] **Step 1: 写失败测试**

增加以下断言：

```typescript
expect(wrapper.text()).toContain('没有找到足够相关且可核验的 Wiki 内容')
expect(wrapper.text()).not.toContain('VPN 账号安全')
expect(api.searchWiki).toHaveBeenLastCalledWith('base', '认证', expect.objectContaining({ cursor: 'cursor-2' }))
expect(api.getWikiPage).toHaveBeenLastCalledWith('base', 'page-auth', expect.objectContaining({ claimCursor: 'claim-cursor-2' }))
expect(wrapper.text()).toContain('语义命中')
expect(wrapper.text()).toContain('关键词命中')
expect(wrapper.text()).toContain('索引失败')
expect(api.retryWikiIndex).toHaveBeenCalledWith('base', 'page-auth')
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `npm --prefix web run test -- --run tests/knowledge/wikiSearchApi.spec.ts tests/knowledge/wikiPanel.spec.ts`

Expected: FAIL，新字段和交互尚不存在。

- [ ] **Step 3: 更新 TypeScript 契约与 API**

```typescript
export interface WikiSearchResult {
  pages: WikiSearchPage[]
  no_answer: boolean
  no_answer_reason: string | null
  degraded: boolean
  degradation_reason: string | null
  truncation_reason: string | null
  next_cursor: string | null
  collection_version: string | null
}
```

`WikiPageSummary` 增加 `index_state` 和 `index_error_code`；`WikiPage` 增加 `claims_next_cursor`；`WikiSearchClaim` 增加 `also_in_pages: WikiPageSummary[]`。`searchWiki` 接收 `cursor`，`getWikiPage` 接收 `claimCursor/claimLimit`；新增 `retryWikiIndex(baseId, pageId)` 与 `backfillWikiIndex(baseId)`。

- [ ] **Step 4: 实现结果状态和追加分页**

`WikiPanel.vue` 保持首屏 5 页/12 条单元。首次搜索替换结果，点击“加载更多”携带 `next_cursor` 并追加去重。查询变化时清空 cursor 和旧结果。分别显示正常、无答案、降级、失败四种状态，不把无答案渲染成网络错误。

打开页面后先显示 20 条原子事实；`claims_next_cursor` 非空时显示“加载更多事实”，追加时按 Claim ID 去重。页面版本变化或游标过期时重新加载第一页，不把旧版事实拼到新版本。

- [ ] **Step 5: 实现索引状态与运维按钮**

页面列表显示 `pending/indexing/failed` 中文状态；只有 `failed` 显示“重试索引”。知识库设置区增加“回填 Wiki 索引”，操作中禁用按钮，409 时提示已有重建任务运行。

- [ ] **Step 6: 运行测试并提交**

Run: `npm --prefix web run test -- --run tests/knowledge/wikiSearchApi.spec.ts tests/knowledge/wikiPanel.spec.ts`

Expected: PASS。

```powershell
git add web/src/types/knowledge.ts web/src/services/knowledgeApi.ts web/src/views/knowledge/WikiPanel.vue web/tests/knowledge/wikiSearchApi.spec.ts web/tests/knowledge/wikiPanel.spec.ts
git commit -m "前端：完善Wiki拒答分页与索引状态"
```

## Task 10：来源摘录与正文安全高亮闭环

**Files:**
- Modify: `web/src/types/knowledge.ts`
- Modify: `web/src/views/knowledge/WikiPanel.vue`
- Modify: `web/src/components/knowledge/documentPreview.ts`
- Modify: `web/src/components/knowledge/DocumentDetailPanel.vue`
- Test: `web/tests/knowledge/wikiPanel.spec.ts`
- Test: `web/tests/knowledge/documentPreview.spec.ts`
- Create (test): `web/tests/knowledge/documentDetailPanel.spec.ts`

- [ ] **Step 1: 写摘录展示和纯文本高亮失败测试**

```typescript
it('按安全文本片段渲染高亮，不注入 HTML', () => {
  const parts = splitFocusedText('<img src=x onerror=alert(1)>认证要求', 0, 28)
  expect(parts.before).toBe('')
  expect(parts.focus).toContain('<img')
  expect(parts.after).toBe('')
})
```

组件测试断言来源展开后显示 `excerpt`，链接继续使用原始子块 `chunk_id`；后端据此加载 `parent_chunk_id` 并返回相对父块的 `focus_start/focus_end`。正文目标块出现 `<mark>`，不存在任何 `[innerHTML]` 或 `v-html`。

- [ ] **Step 2: 运行测试并确认失败**

Run: `npm --prefix web run test -- --run tests/knowledge/wikiPanel.spec.ts tests/knowledge/documentPreview.spec.ts tests/knowledge/documentDetailPanel.spec.ts`

Expected: FAIL，前端尚未消费摘录和坐标。

- [ ] **Step 3: 实现安全拆分函数**

```typescript
export function splitFocusedText(content: string, start: number | null, end: number | null) {
  if (start === null || end === null || start < 0 || end <= start || end > content.length) {
    return { before: content, focus: '', after: '' }
  }
  return {
    before: content.slice(0, start),
    focus: content.slice(start, end),
    after: content.slice(end),
  }
}
```

- [ ] **Step 4: 接入 Wiki 来源和文档详情**

Wiki 来源卡显示标题路径、页码、`excerpt`、有效状态和失效原因。深链 query 使用 `version` 与 `chunk=chunk_id`，让现有预览服务按子块定位父块并生成相对坐标。文档详情对目标父块调用 `splitFocusedText`，用三个文本插值节点和 `<mark>` 渲染，并在加载完成后 `scrollIntoView({ block: 'center' })`。

- [ ] **Step 5: 运行测试并提交**

Run: `npm --prefix web run test -- --run tests/knowledge/wikiPanel.spec.ts tests/knowledge/documentPreview.spec.ts tests/knowledge/documentDetailPanel.spec.ts`

Expected: PASS。

```powershell
git add web/src/types/knowledge.ts web/src/views/knowledge/WikiPanel.vue web/src/components/knowledge/documentPreview.ts web/src/components/knowledge/DocumentDetailPanel.vue web/tests/knowledge/wikiPanel.spec.ts web/tests/knowledge/documentPreview.spec.ts web/tests/knowledge/documentDetailPanel.spec.ts
git commit -m "前端：打通Wiki来源摘录与正文高亮"
```

## Task 11：真实集成、固定质量门与性能证据

**Files:**
- Modify: `tests/eval/test_wiki_retrieval_quality.py`
- Modify: `tests/eval/wiki_cases.json`
- Create: `tests/integration/test_wiki_independent_index.py`
- Modify: `tests/integration/test_wiki_celery.py`
- Modify: `web/tests/e2e/wiki.spec.ts`
- Create: `docs/verification/2026-10-07-wiki-independent-index.md`

- [ ] **Step 1: 先把固定评测切到独立 collection**

评测必须创建唯一原文 collection 和唯一 Wiki 物理 collection，运行页面生成后执行无模型 Wiki 回填，再用 `WikiSearchBackend.INDEPENDENT` 搜索。为不可回答集合至少保留三种问题：不存在的内部政策、宇宙密码、网络安全保险保费。

- [ ] **Step 2: 写真实状态转换集成测试**

覆盖：发布→Outbox→索引→ready、数量校验失败、Worker 重启恢复、文档更新后旧来源立即不可见、人工版本不索引、审核通过后索引、回滚、并发回填锁、指纹变化写入新物理 collection、alias 切换与回滚。

- [ ] **Step 3: 运行集成测试并修正实现缺口**

Run:

```powershell
$testDatabaseUrl='postgresql+asyncpg://agent:agent@127.0.0.1:5432/agent_wiki_test'
docker compose exec -T postgres sh -lc 'createdb -U agent agent_wiki_test 2>/dev/null || true'
$env:AGENT_DATABASE_URL=$testDatabaseUrl
uv run alembic upgrade head
$env:RUN_KNOWLEDGE_INTEGRATION='1'
$env:AGENT_WIKI_TEST_DATABASE_URL=$testDatabaseUrl
uv run pytest tests/integration/test_wiki_independent_index.py tests/integration/test_wiki_celery.py -q
```

Expected: PASS；测试结束后删除唯一测试 collection 和知识库数据，不影响 `knowledge_chunks`、`wiki_units` alias 或用户数据。

- [ ] **Step 4: 运行固定质量门**

Run: `uv run pytest tests/eval/test_wiki_retrieval_quality.py -q -s`

Expected 输出一个 `WIKI_EVAL` JSON，且满足：

```text
Page Recall@5 / page_recall_at_5 >= 0.90
Claim Recall@8 / claim_recall_at_8 >= 0.85
Citation Precision / citation_precision >= 0.95
同义查询一致性 / synonym_result_consistency >= 0.85
无答案误命中率 / no_answer_false_match_rate <= 0.05
needs_review_leaks == 0
invalid_source_leaks == 0
old_page_version_leaks == 0
```

- [ ] **Step 5: 记录性能与收敛证据**

用 1 万个确定性 Wiki 单元运行 100 次预热后搜索，记录 P50/P95；关闭 Cross-Encoder 时 P95 ≤ 800ms，启用时 P95 ≤ 1.5s。记录页面发布到 `ready` 的 P95，要求 ≤ 5 分钟。文档记录 Embedding 指纹、相关性策略指纹、聚合指标、collection 名、commit 和命令，不记录查询正文、凭据或文档全文。

- [ ] **Step 6: 完成浏览器 E2E**

E2E 覆盖同义查询、无答案、加载更多、来源展开、正文高亮、索引中、失败重试和移动端无横向溢出。

Run: `npm --prefix web run test:e2e -- --config playwright.wiki.config.ts tests/e2e/wiki.spec.ts`

Expected: Chromium 桌面和移动项目全部 PASS。

- [ ] **Step 7: 提交**

```powershell
git add tests/eval/test_wiki_retrieval_quality.py tests/eval/wiki_cases.json tests/integration/test_wiki_independent_index.py tests/integration/test_wiki_celery.py web/tests/e2e/wiki.spec.ts docs/verification/2026-10-07-wiki-independent-index.md
git commit -m "测试：建立Wiki独立检索质量门"
```

## Task 12：全量回归、Shadow 上线与回滚演练

**Files:**
- Modify: `README.md`
- Modify: `docs/verification/2026-10-07-wiki-independent-index.md`
- Modify: `.env.example`

- [ ] **Step 1: 运行后端静态检查和全量单元测试**

Run:

```powershell
uv run ruff check src tests
uv run mypy src
uv run pytest -q
```

Expected: 三条命令退出码均为 0。

- [ ] **Step 2: 运行前端全量检查**

Run:

```powershell
npm --prefix web run lint
npm --prefix web run type-check
npm --prefix web run test -- --run
npm --prefix web run build
```

Expected: lint、类型检查、Vitest 和生产构建全部成功。

- [ ] **Step 3: 执行历史无模型回填并核对数量**

在本地 API 调用知识库回填接口，等待 Worker 完成。核对每个当前 `summary/topic` 页面版本均为 `ready`，Milvus `page_version_id` 计数等于该版本可索引可信 Claim 数，`tokens_charged` 不变。

- [ ] **Step 4: 执行 Shadow 观察**

把 `AGENT_WIKI_SEARCH_BACKEND=shadow`，重启 API，执行固定评测和真实页面搜索。验证用户仍看到 legacy 响应，日志有查询指纹、两套页面 ID、拒答差异和耗时，日志没有完整问题或正文。

- [ ] **Step 5: 切到 independent 并演练回滚**

质量门通过后把本地后端切为 `independent`，验证“宇宙密码”和保险保费问题返回 `no_answer=true`。随后切回 `legacy` 并确认无需修改 PostgreSQL 页面数据即可恢复，再切回 `independent`。

- [ ] **Step 6: 更新运行手册**

README 写明迁移、回填、Shadow、独立切换、单页重试、全库重建、alias 切换和回滚命令。`.env.example` 保持安全默认值 `wiki_index_enabled=false`、`wiki_search_backend=legacy`。

- [ ] **Step 7: 检查 diff、安全与提交**

Run:

```powershell
git diff --check
git diff --stat
uv tool run pip-audit
npm --prefix web audit --audit-level=high
```

Expected: 无空白错误；diff 只包含本计划范围；Python 与 npm 依赖审计没有未处置的高危问题。

```powershell
git add README.md .env.example docs/verification/2026-10-07-wiki-independent-index.md
git commit -m "文档：补充Wiki独立索引上线与回滚手册"
```

## 完成定义

- 当前可信 `summary_section / summary_fact / topic_fact` 已进入独立 Wiki collection，人工和待核实内容零泄漏。
- 搜索不再依赖原文 chunk 反向映射作为主路径，legacy 只作为显式后端或回滚路径。
- 固定评测六项质量门全部通过，“不存在的宇宙密码”返回无答案。
- 来源展开显示动态原文摘录，正文页通过父块和坐标安全高亮。
- 搜索支持稳定加载更多，查询或 collection 变化时返回 `wiki_search_cursor_expired`。
- 索引失败可重试、超时可恢复、全库可幂等回填、Embedding 升级可通过 alias 原子切换和回滚。
- 后端、前端、真实集成、E2E、静态检查和依赖审计均有可复核证据。
