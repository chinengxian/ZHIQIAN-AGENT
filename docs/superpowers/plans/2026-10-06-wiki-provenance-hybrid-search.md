# Wiki 来源驱动混合检索 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Wiki 改造成来源驱动的混合检索：通过现有原文 Dense + BM25 召回反向定位 Wiki 页面和 claim，按相关性而非固定顺序输出，并让新生成的摘要与主题逐条关联 chunk。

**Architecture:** 新增 `WikiRetrievalService`，将 PostgreSQL 页面元数据候选与 `KnowledgeRetrievalService` 的原文 chunk 候选通过 RRF 融合，再对页面和 claim 重排、MMR 去重并按预算输出。生成侧把 `WikiGeneration` 改为带稳定 `source_indices` 的结构化 claim/topic，数据库新增别名、claim 类型、展示顺序和反向检索索引；路线 C 的独立 Wiki 向量索引不在本计划实现。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL `pg_trgm`、Milvus Dense/BM25、LangChain、pytest、Vue 3、TypeScript、Vitest、Playwright。

---

## 文件结构与职责

### 新建

- `migrations/versions/20261006_0003_wiki_hybrid_search.py`：`pg_trgm`、页面别名、claim 类型/顺序及反向索引迁移。
- `src/agent_api/knowledge/wiki/ranking.py`：RRF、简单分词、Jaccard 和 MMR；纯函数、无数据库依赖。
- `src/agent_api/knowledge/wiki/retrieval.py`：页面元数据召回、chunk 反向映射、批量来源校验、融合、重排和预算。
- `tests/test_wiki_ranking.py`：排序算法单元测试。
- `tests/test_retrieval_service.py`：原文候选池接口单元测试，使用 fake index/embedder。
- `tests/test_wiki_retrieval.py`：检索服务单元测试，使用 fake source retrieval/reranker。
- `tests/eval/wiki_cases.json`：机器可读 Wiki 固定评测集。
- `tests/eval/test_wiki_retrieval_quality.py`：真实 PostgreSQL/Milvus Wiki 质量评测。
- `web/tests/knowledge/wikiPanel.spec.ts`：Wiki 搜索界面组件测试。

### 修改

- `src/agent_api/knowledge/infrastructure/database/models.py`：`WikiPageAlias`、`WikiClaim.position`、`WikiClaim.claim_type` 和索引声明。
- `src/agent_api/knowledge/infrastructure/startup.py`：数据库期望 revision 更新为 `20261006_0003`。
- `src/agent_api/knowledge/application/retrieval.py`：新增供 Wiki 使用的扩大候选池接口，不改变现有 `search()` 默认契约。
- `src/agent_api/knowledge/wiki/generator.py`：结构化 summary claim/topic 及全局 chunk 编号。
- `src/agent_api/knowledge/wiki/processor.py`：保存带来源 claim、别名和主题相关 claim，确定性组装摘要正文。
- `src/agent_api/knowledge/wiki/service.py`：批量来源读取、按 position 分页读取；删除 Agent 搜索中的旧 `ILIKE + [:8]` 路径。
- `src/agent_api/knowledge/wiki/api.py`：新搜索响应和 query/mode/limit 参数。
- `src/agent_api/knowledge/tools/readonly.py`：Agent `wiki_search` 使用新检索服务，`wiki_read_page` 支持 query/cursor/limit。
- `src/agent_api/core/lifespan.py`：构造并发布 `WikiRetrievalService`。
- `tests/test_knowledge_models.py`、`tests/integration/test_wiki_flow.py`、`tests/test_knowledge_tools.py`、`tests/test_lifespan.py`：迁移、生成、Agent 和生命周期回归。
- `web/src/types/knowledge.ts`：搜索结果与 claim 分页类型。
- `web/src/services/knowledgeApi.ts`：Wiki 搜索 API。
- `web/src/views/knowledge/WikiPanel.vue`：搜索、降级状态、命中 claim 和定位。
- `web/tests/knowledge/knowledgeApi.spec.ts`、`web/tests/e2e/wiki.spec.ts`：前端契约与浏览器回归。
- `docs/verification/VERIFY-005.md`：更新路线 B 验证证据；不得覆盖尚未运行的真实质量结论。

所有新增或修改的代码注释使用中文；所有 Git 提交信息使用中文。

## 工作包 1：数据契约与可逆迁移

### Task 1: 先用模型测试冻结新表和字段

**Files:**
- Modify: `tests/test_knowledge_models.py`
- Modify: `src/agent_api/knowledge/infrastructure/database/models.py`

- [ ] **Step 1: 写失败的模型测试**

在 `tests/test_knowledge_models.py` 增加：

```python
def test_wiki_search_schema_declares_aliases_claim_metadata_and_reverse_indexes() -> None:
    aliases = Base.metadata.tables["wiki_page_aliases"]
    claims = Base.metadata.tables["wiki_claims"]
    sources = Base.metadata.tables["wiki_claim_sources"]

    assert {"page_id", "alias", "normalized_alias"} <= set(aliases.c.keys())
    assert claims.c.position.nullable is False
    assert claims.c.claim_type.nullable is False
    assert any(index.name == "ix_wiki_aliases_normalized" for index in aliases.indexes)
    assert any(index.name == "ix_wiki_claim_sources_chunk" for index in sources.indexes)
    assert any(
        index.name == "ix_wiki_claim_sources_locator" for index in sources.indexes
    )
```

同时把 `wiki_page_aliases` 加入 `test_knowledge_schema_contains_all_source_of_truth_tables()` 的期望集合。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_models.py::test_wiki_search_schema_declares_aliases_claim_metadata_and_reverse_indexes -q
```

Expected: FAIL，错误包含 `KeyError: 'wiki_page_aliases'`。

- [ ] **Step 3: 最小实现 SQLAlchemy 模型**

在 `models.py` 的 `WikiPage` 与 `WikiPageVersion` 之间新增：

```python
class WikiPageAlias(Base):
    """Wiki 页面的可检索别名；规范化值用于精确归并。"""

    __tablename__ = "wiki_page_aliases"
    __table_args__ = (
        UniqueConstraint("page_id", "normalized_alias", name="uq_wiki_page_alias_normalized"),
        Index("ix_wiki_aliases_normalized", "normalized_alias"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    page_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(Text, nullable=False)
```

在 `WikiClaim` 增加：

```python
position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
claim_type: Mapped[str] = mapped_column(String(24), nullable=False, default="source_excerpt")
```

同时在 `WikiClaim.__table_args__` 声明 `CheckConstraint`，允许值严格限定为
`summary_fact/topic_fact/source_excerpt/manual`；模型测试同时断言约束名为
`ck_wiki_claim_type`，保证 ORM metadata 与迁移一致。

在 `WikiClaimSource.__table_args__` 增加：

```python
Index("ix_wiki_claim_sources_chunk", "chunk_id"),
Index(
    "ix_wiki_claim_sources_locator",
    "document_version_id",
    "chunk_kind",
    "chunk_ordinal",
),
```

- [ ] **Step 4: 运行模型测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_models.py -q
```

Expected: 除 Alembic head 断言外通过；head 仍为旧 revision 是下一任务的预期失败。

- [ ] **Step 5: 提交模型契约**

```powershell
git add src/agent_api/knowledge/infrastructure/database/models.py tests/test_knowledge_models.py
git commit -m "测试：冻结Wiki混合检索数据契约"
```

### Task 2: 增加 pg_trgm 与可逆迁移

**Files:**
- Create: `migrations/versions/20261006_0003_wiki_hybrid_search.py`
- Modify: `src/agent_api/knowledge/infrastructure/startup.py`
- Test: `tests/test_knowledge_models.py`

- [ ] **Step 1: 新建迁移并写完整 upgrade/downgrade**

迁移必须包含以下核心内容：

```python
"""增加 Wiki 来源驱动混合检索所需结构。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261006_0003"
down_revision: str | None = "20260922_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "wiki_page_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["page_id"], ["wiki_pages.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "page_id", "normalized_alias", name="uq_wiki_page_alias_normalized"
        ),
    )
    op.create_index(
        "ix_wiki_aliases_normalized", "wiki_page_aliases", ["normalized_alias"]
    )
    op.add_column(
        "wiki_claims",
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "wiki_claims",
        sa.Column(
            "claim_type", sa.String(24), server_default="source_excerpt", nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_wiki_claim_type",
        "wiki_claims",
        "claim_type IN ('summary_fact', 'topic_fact', 'source_excerpt', 'manual')",
    )
    op.create_index("ix_wiki_claim_sources_chunk", "wiki_claim_sources", ["chunk_id"])
    op.create_index(
        "ix_wiki_claim_sources_locator",
        "wiki_claim_sources",
        ["document_version_id", "chunk_kind", "chunk_ordinal"],
    )
    op.execute(
        "CREATE INDEX ix_wiki_pages_title_trgm ON wiki_pages "
        "USING gin (lower(title) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_wiki_versions_summary_trgm ON wiki_page_versions "
        "USING gin (lower(summary) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_wiki_aliases_value_trgm ON wiki_page_aliases "
        "USING gin (lower(alias) gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_wiki_aliases_value_trgm")
    op.execute("DROP INDEX IF EXISTS ix_wiki_versions_summary_trgm")
    op.execute("DROP INDEX IF EXISTS ix_wiki_pages_title_trgm")
    op.drop_index("ix_wiki_claim_sources_locator", table_name="wiki_claim_sources")
    op.drop_index("ix_wiki_claim_sources_chunk", table_name="wiki_claim_sources")
    op.drop_constraint("ck_wiki_claim_type", "wiki_claims", type_="check")
    op.drop_column("wiki_claims", "claim_type")
    op.drop_column("wiki_claims", "position")
    op.drop_table("wiki_page_aliases")
    # pg_trgm 可能被其他模块使用，回滚时不删除扩展。
```

- [ ] **Step 2: 更新期望 revision**

在 `startup.py` 修改：

```python
EXPECTED_DATABASE_REVISION = "20261006_0003"
```

- [ ] **Step 3: 验证迁移链**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_models.py -q
```

Expected: PASS。

若配置了独立测试数据库，再运行：

```powershell
$env:AGENT_DATABASE_URL=$env:WIKI_TEST_DATABASE_URL
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe downgrade 20260922_0002
.\.venv\Scripts\alembic.exe upgrade head
```

Expected: 三个命令 exit 0；无权限创建 `pg_trgm` 时必须停止并记录明确错误，不能修改应用代码绕过迁移。

- [ ] **Step 4: 提交迁移**

```powershell
git add migrations/versions/20261006_0003_wiki_hybrid_search.py src/agent_api/knowledge/infrastructure/startup.py
git commit -m "数据库：增加Wiki混合检索结构"
```

## 工作包 2：带 chunk 引用的摘要和主题生成

### Task 3: 以测试冻结结构化生成协议

**Files:**
- Modify: `src/agent_api/knowledge/wiki/generator.py`
- Create or Modify: `tests/test_wiki_generator.py`
- Modify: `tests/integration/test_wiki_flow.py`
- Modify: `tests/integration/test_wiki_celery.py`

- [ ] **Step 1: 写来源编号和越界拒绝测试**

新增测试，直接使用 LangChain 提供的 `FakeListChatModel`，避免计划依赖未定义的测试替身：

```python
from langchain_core.language_models.fake_chat_models import FakeListChatModel


async def test_generator_keeps_global_chunk_sources_across_batches() -> None:
    model = FakeListChatModel(
        responses=[
            '{"summary_claims":[{"text":"首批事实","source_indices":[1]}],'
            '"topics":[{"name":"身份认证","aliases":["MFA"],"source_indices":[1]}]}',
            '{"summary_claims":[{"text":"尾部事实","source_indices":[13]}],'
            '"topics":[{"name":"身份认证","aliases":["多因素认证"],'
            '"source_indices":[13]}]}',
        ]
    )
    result = await ChatWikiGenerator(model, "fake").generate(
        "规范", [f"分块 {index}" for index in range(1, 14)]
    )

    assert [claim.source_indices for claim in result.summary_claims] == [(1,), (13,)]
    assert result.topics[0].aliases == ("MFA", "多因素认证")
    assert result.topics[0].source_indices == (1, 13)
```

再增加 `source_indices=[99]` 时抛出 `ValueError("wiki_generation_invalid_source")` 的测试。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_wiki_generator.py -q
```

Expected: FAIL，缺少 `summary_claims`、`WikiGeneratedClaim` 或新 JSON 解析。

- [ ] **Step 3: 实现新数据类型和批次全局编号**

在 `generator.py` 定义：

```python
@dataclass(frozen=True, slots=True)
class WikiGeneratedClaim:
    text: str
    source_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class WikiGeneratedTopic:
    name: str
    aliases: tuple[str, ...]
    source_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class WikiGeneration:
    summary_claims: tuple[WikiGeneratedClaim, ...]
    topics: tuple[WikiGeneratedTopic, ...]
    measured_tokens: int | None

    @property
    def summary(self) -> str:
        return "\n".join(claim.text for claim in self.summary_claims)
```

`generate()` 必须用全局编号构造批次：

```python
indexed = list(enumerate(excerpts, 1))
batches = [indexed[index : index + 12] for index in range(0, len(indexed), 12)]
partials = [await self._generate_once(title, batch, len(excerpts)) for batch in batches]
```

合并规则必须确定性去重：claim 按规范化文本去重；同名主题合并 aliases 和 source indices，均保持首次出现顺序。模型 JSON 中每个来源必须满足 `1 <= index <= len(excerpts)`。

- [ ] **Step 4: 更新提示词和 fake generators**

模型输出契约改为：

```text
仅输出 JSON：
{"summary_claims":[{"text":"原子事实","source_indices":[1]}],
 "topics":[{"name":"主题","aliases":["别名"],"source_indices":[1]}]}。
source_indices 必须使用输入 [chunk:N] 的 N；每条事实必须至少有一个来源。
```

`tests/integration/test_wiki_flow.py` 和 `test_wiki_celery.py` 的 fake 生成器改为返回新结构，不保留兼容旧 tuple 的分支。

- [ ] **Step 5: 运行生成器和 Wiki 集成单测**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_wiki_generator.py tests/integration/test_wiki_flow.py tests/integration/test_wiki_celery.py -q
```

Expected: 单元测试 PASS；需要外部服务的测试按既有 marker SKIP，而非 collection error。

- [ ] **Step 6: 提交生成契约**

```powershell
git add src/agent_api/knowledge/wiki/generator.py tests/test_wiki_generator.py tests/integration/test_wiki_flow.py tests/integration/test_wiki_celery.py
git commit -m "功能：生成带分块引用的Wiki摘要"
```

### Task 4: 发布 grounded claims、别名和主题相关 claim

**Files:**
- Modify: `src/agent_api/knowledge/wiki/processor.py`
- Modify: `src/agent_api/knowledge/wiki/service.py`
- Modify: `tests/integration/test_wiki_flow.py`

- [ ] **Step 1: 写失败的发布断言**

在真实 PostgreSQL Wiki flow 中断言：

```python
detail = await service.get_page(knowledge_base_id, summary["id"])
assert [claim["claim_type"] for claim in detail["claims"]] == [
    "summary_fact",
    "summary_fact",
]
assert [claim["position"] for claim in detail["claims"]] == [0, 1]
assert {source["chunk_id"] for source in detail["claims"][1]["sources"]} == {
    expected_tail_chunk_id
}
assert "文档尾部证据" in detail["content"]
assert "只存在于未被引用分块的前缀" not in detail["content"]
```

测试夹具给一个未被任何 `source_indices` 引用、且包含“只存在于未被引用分块的前缀”的
chunk。主题页断言只包含被该主题 `source_indices` 指向的 claim，并且
`WikiPageAlias` 包含规范化后的别名。

- [ ] **Step 2: 运行集成测试并确认失败**

Run:

```powershell
$env:RUN_KNOWLEDGE_INTEGRATION='1'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_wiki_flow.py -q
```

Expected: FAIL，旧 claim 缺少 `claim_type/position`，正文仍是 chunk 截断列表。

- [ ] **Step 3: 修改生成检查点**

`WikiProcessor._settle()` 保存 JSON 可序列化结构：

```python
"generation": {
    "summary_claims": [
        {"text": claim.text, "source_indices": list(claim.source_indices)}
        for claim in result.summary_claims
    ],
    "topics": [
        {
            "name": topic.name,
            "aliases": list(topic.aliases),
            "source_indices": list(topic.source_indices),
        }
        for topic in result.topics
    ],
}
```

- [ ] **Step 4: 修改发布逻辑**

`_publish()` 按 `source_indices` 映射 `chunks[index - 1]`，每条摘要事实创建一个 `WikiClaim`：

```python
claim = WikiClaim(
    page_version_id=version.id,
    claim_key=f"summary-{position + 1}",
    text=text,
    trust_state="verified",
    position=position,
    claim_type="summary_fact",
)
```

为其每个来源创建 `WikiClaimSource`。摘要正文使用确定性 Markdown：标题、摘要事实列表和每条事实的 chunk 脚注，不再追加每个 chunk 的前 350 字。

主题 metadata 保存 `name`、`aliases` 和真实 `source_chunk_ids`。`_rebuild_topic_pages()` 只复制来源集合与主题 `source_chunk_ids` 相交的 verified claim，并把复制后的类型设为 `topic_fact`。

- [ ] **Step 5: 保存页面别名**

增加 `_replace_aliases(session, page_id, aliases)`：删除该页旧别名，使用 NFKC + casefold + 连续空白折叠生成 `normalized_alias`，插入去重后的别名。只为 topic 页写模型别名；summary 页标题不重复写为别名。

- [ ] **Step 6: 更新响应字段与排序**

`_claims_response()` 使用：

```python
.order_by(WikiClaim.position, WikiClaim.id)
```

响应增加 `position` 和 `claim_type`。人工编辑 claim 写入 `claim_type="manual"`；旧复制/回滚保留原类型与 position。

- [ ] **Step 7: 运行 Wiki 测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_wiki_flow.py tests/test_knowledge_tools.py -q
```

Expected: PASS 或仅既有外部服务 marker SKIP。

- [ ] **Step 8: 提交来源化发布**

```powershell
git add src/agent_api/knowledge/wiki/processor.py src/agent_api/knowledge/wiki/service.py tests/integration/test_wiki_flow.py
git commit -m "功能：发布可追溯的Wiki结论"
```

## 工作包 3：来源驱动混合检索

### Task 5: 先实现可独立验证的 RRF 与 MMR

**Files:**
- Create: `src/agent_api/knowledge/wiki/ranking.py`
- Create: `tests/test_wiki_ranking.py`

- [ ] **Step 1: 写失败测试**

```python
from agent_api.knowledge.wiki.ranking import reciprocal_rank_fusion, select_mmr


def test_rrf_fuses_channels_without_comparing_raw_scores() -> None:
    fused = reciprocal_rank_fusion(
        [["page-a", "page-b"], ["page-b", "page-c"]], k=60
    )
    assert list(fused) == ["page-b", "page-a", "page-c"]
    assert fused["page-b"] > fused["page-a"]


def test_mmr_keeps_relevant_but_non_duplicate_claims() -> None:
    selected = select_mmr(
        [
            ("a", 1.0, "MFA 要求两个独立因素"),
            ("b", 0.98, "多因素认证要求两个独立因素"),
            ("c", 0.8, "VPN 过渡方案最长九十天"),
        ],
        limit=2,
        lambda_=0.7,
    )
    assert [item[0] for item in selected] == ["a", "c"]
```

- [ ] **Step 2: 运行并确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_wiki_ranking.py -q
```

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现纯函数**

`reciprocal_rank_fusion()` 对每个通道内排名从 1 开始累加 `1 / (k + rank)`，同分按首次出现顺序稳定排序并返回有序 dict。`select_mmr()` 用中文单字/英文单词 token 集合计算 Jaccard，迭代最大化：

```python
lambda_ * relevance - (1 - lambda_) * max_redundancy
```

空输入和 `limit <= 0` 返回空列表，`lambda_` 必须在 `[0, 1]`。

- [ ] **Step 4: 运行测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_wiki_ranking.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交排序原语**

```powershell
git add src/agent_api/knowledge/wiki/ranking.py tests/test_wiki_ranking.py
git commit -m "功能：增加Wiki检索融合与去重算法"
```

### Task 6: 为 Wiki 暴露扩大后的原文候选池

**Files:**
- Modify: `src/agent_api/knowledge/application/retrieval.py`
- Create: `tests/test_retrieval_service.py`
- Modify: `tests/test_hybrid_retrieval.py`（只保留真实 Milvus 回归，不在此文件伪造单元环境）

- [ ] **Step 1: 写候选池测试**

在 `tests/test_retrieval_service.py` 定义记录 `hybrid_search()` 调用参数的
`RecordingChunkIndex`、返回固定向量的 `StubEmbedder`，并断言：

```python
sources, degraded = await service.search_candidates(
    "身份认证", (base_id,), mode="hybrid", limit=40
)
assert len(sources) == 40
assert fake_index.last_limit == 80
assert degraded is False
```

并断言公共 `search(..., limit=9)` 仍抛出 `invalid_search_options`，保持旧工具最多 8 条的契约。

- [ ] **Step 2: 运行并确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_retrieval_service.py -q
```

Expected: FAIL，缺少 `search_candidates`。

- [ ] **Step 3: 提取共享内部实现**

新增：

```python
async def search_candidates(
    self,
    query: str,
    scope_ids: tuple[UUID, ...],
    *,
    mode: str = "hybrid",
    limit: int = 40,
) -> tuple[list[Source], bool]:
    if not 1 <= limit <= 100:
        raise ValueError("invalid_candidate_limit")
    return await self._search_sources(
        query,
        scope_ids,
        mode=mode,
        output_limit=limit,
        index_limit=min(200, max(limit * 2, 40)),
        deduplicate_parents=False,
    )
```

现有 `search()` 调用同一 `_search_sources()`，继续使用 `output_limit=limit`、`index_limit=20`、`deduplicate_parents=True`。Wiki 候选必须保留实际命中的 child `chunk_id`，不能用 parent ID 替换反向映射键。

- [ ] **Step 4: 运行检索回归**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_retrieval_service.py tests/test_hybrid_retrieval.py tests/eval/test_retrieval_quality.py -q
```

Expected: 未启用集成环境时 SKIP；单元测试 PASS，无 collection error。

- [ ] **Step 5: 提交候选接口**

```powershell
git add src/agent_api/knowledge/application/retrieval.py tests/test_retrieval_service.py tests/test_hybrid_retrieval.py
git commit -m "重构：开放Wiki原文检索候选池"
```

### Task 7: 实现 WikiRetrievalService

**Files:**
- Create: `src/agent_api/knowledge/wiki/retrieval.py`
- Create: `tests/test_wiki_retrieval.py`

- [ ] **Step 1: 写跨知识库全局排序与第九条命中测试**

构造 fake source retrieval，使相关 chunk 映射到第二个知识库页面且对应 `position=9`：

```python
async def test_search_ranks_globally_and_returns_relevant_claim_after_eighth() -> None:
    result = await retrieval.search(
        "双因素校验要求",
        (kb_a, kb_b),
        mode="hybrid",
        page_limit=5,
        claim_limit=12,
    )

    assert result.pages[0].knowledge_base_id == kb_b
    assert result.pages[0].matched_claims[0].position == 9
    assert result.pages[0].matched_fields == ("source_chunk",)
    assert result.degraded is False
```

增加以下独立测试：

- reranker 抛异常时保留 RRF 顺序且 `degradation_reason="reranker_unavailable"`；
- source retrieval 抛异常时仍返回 pg_trgm 元数据候选且 `degradation_reason="source_retrieval_unavailable"`；
- PostgreSQL 页面候选/归属查询失败时返回稳定 `wiki_retrieval_unavailable`，不得只返回向量结果；
- `needs_review`、旧 page version、失效 document version 均不返回；
- 空白/超长查询、特殊字符、slug 前缀、标题/别名精确命中与跨知识库页面均覆盖；
- 多个重复 claim 经 MMR 后让位给不同事实；
- 总字符预算产生 `omitted_claim_count` 和 `truncation_reason="character_budget"`。

- [ ] **Step 2: 运行并确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_wiki_retrieval.py -q
```

Expected: FAIL，模块不存在。

- [ ] **Step 3: 定义检索结果类型**

在 `retrieval.py` 定义冻结 dataclass：

```python
@dataclass(frozen=True, slots=True)
class WikiMatchedClaim:
    id: UUID
    text: str
    position: int
    claim_type: str
    score: float
    sources: tuple[Source, ...]


@dataclass(frozen=True, slots=True)
class WikiMatchedPage:
    id: UUID
    knowledge_base_id: UUID
    slug: str
    title: str
    page_type: str
    score: float
    matched_fields: tuple[str, ...]
    matched_claims: tuple[WikiMatchedClaim, ...]
    omitted_claim_count: int


@dataclass(frozen=True, slots=True)
class WikiSearchResult:
    pages: tuple[WikiMatchedPage, ...]
    degraded: bool
    degradation_reason: str | None
    truncation_reason: str | None
```

- [ ] **Step 4: 实现 pg_trgm 元数据候选**

使用 SQLAlchemy `func.similarity(func.lower(...), normalized_query)`，连接当前版本和别名。候选只允许：Wiki 开启、知识库启用、页面未删除、当前版本存在、非 index 页面。每页合并 title/slug/summary/alias 信号，限制 40 个元数据候选。

- [ ] **Step 5: 实现 chunk 反向映射和批量来源校验**

一次查询连接：

```text
WikiClaimSource
→ WikiClaim
→ WikiPageVersion
→ WikiPage(current_version_id == WikiPageVersion.id)
→ Document(active_version_id == source.document_version_id, status == ready)
→ Chunk(version/kind/ordinal)
```

查询只针对候选 `chunk_id`，批量计算内容 SHA-256，过滤哈希不匹配、`needs_review` 和非当前页面版本。把命中的 child source 转成 `Source`，保留原文 citation 注册所需字段。

- [ ] **Step 6: 实现融合、重排和预算**

- 元数据页序列与 chunk 反向页序列进入 `reciprocal_rank_fusion()`。
- reranker passage 为 `标题 + 别名 + 摘要 + 最相关 claim`；返回顺序必须是输入索引的完整排列，否则按失败降级。
- claim passage 为 `claim.text + 命中父块/来源 excerpt`。
- MMR 后按 `page_limit`、`claim_limit`、`character_budget=12_000` 截断。
- 每页通常最多 4 条；只有一个命中页面时可使用剩余 claim 预算。
- 同一服务实现 `read_page(knowledge_base_id, page_id, query, cursor, limit)`：无 query
  时按 `position, id` 分页；有 query 时只对该页 verified claim 调用共享的
  `rank_page_claims()`。返回 `next_cursor` 与 `omitted_claim_count`，并复用同一批量来源校验，
  不让 `WikiService` 反向依赖检索服务。

为每次搜索记录结构化日志：查询模式、知识库数量、不可逆查询指纹、各通道候选数与耗时、
失效/待核实/MMR/预算过滤数、最终页面/claim 数、降级原因和总耗时。日志测试使用
`caplog` 断言不出现完整查询、chunk 正文、密钥或数据库 URL。

- [ ] **Step 7: 运行检索测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_wiki_ranking.py tests/test_wiki_retrieval.py -q
```

Expected: PASS。

- [ ] **Step 8: 提交检索服务**

```powershell
git add src/agent_api/knowledge/wiki/retrieval.py tests/test_wiki_retrieval.py
git commit -m "功能：实现Wiki来源驱动混合检索"
```

## 工作包 4：API、Agent 与生命周期

### Task 8: 接入生命周期和 HTTP API

**Files:**
- Modify: `src/agent_api/core/lifespan.py`
- Modify: `src/agent_api/knowledge/wiki/api.py`
- Modify: `tests/test_lifespan.py`
- Modify: `tests/test_knowledge_api.py`

- [ ] **Step 1: 写失败的 API 契约测试**

```python
response = await client.get(
    f"/api/v1/knowledge-bases/{base_id}/wiki/search",
    params={"q": "双因素校验", "mode": "hybrid", "limit": 5, "claim_limit": 12},
)
assert response.status_code == 200
payload = response.json()
assert payload["pages"][0]["matched_fields"] == ["alias", "source_chunk"]
assert payload["pages"][0]["matched_claims"][0]["sources"][0]["chunk_id"]
assert payload["degraded"] is False
```

增加非法 mode、空查询、page limit > 20、claim limit > 50 的 422 测试。

- [ ] **Step 2: 写生命周期失败测试**

断言知识功能启用时 `app.state.wiki_retrieval` 存在，关闭时不存在；关闭 lifespan 后属性被删除，Milvus 只关闭一次。

- [ ] **Step 3: 运行并确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_lifespan.py tests/test_knowledge_api.py -q
```

Expected: FAIL，缺少 state 或响应 schema 不匹配。

- [ ] **Step 4: 构造并发布服务**

`create_lifespan()` 在 knowledge enabled 时始终构造 `KnowledgeRetrievalService`，再构造：

```python
wiki_retrieval = WikiRetrievalService(
    knowledge_database.session_factory,
    knowledge_retrieval,
    reranker,
)
```

只有 `LangChainAgentStream` 才注册工具，但 HTTP Wiki 搜索不依赖 Agent 类型。关闭时先删除 `app.state.wiki_retrieval`，再释放 Milvus/数据库。

- [ ] **Step 5: 修改 API dependency 和 schema**

新增 `get_wiki_retrieval()`，搜索路由改用它。Pydantic schema必须包含 page score、matched fields、matched claims、sources、degraded 和 truncation fields；`mode` 使用 `Literal["hybrid", "semantic", "keyword"]`。

- [ ] **Step 6: 运行后端 API 测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_lifespan.py tests/test_knowledge_api.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交 API 接入**

```powershell
git add src/agent_api/core/lifespan.py src/agent_api/knowledge/wiki/api.py tests/test_lifespan.py tests/test_knowledge_api.py
git commit -m "功能：接入Wiki混合检索接口"
```

### Task 9: 替换 Agent 的前八条逻辑并支持查询式读页

**Files:**
- Modify: `src/agent_api/knowledge/tools/readonly.py`
- Modify: `src/agent_api/knowledge/wiki/service.py`
- Modify: `tests/test_knowledge_tools.py`

- [ ] **Step 1: 写 Agent 工具失败测试**

```python
found = json.loads(
    await tools["wiki_search"].ainvoke(
        {"query": "双因素校验", "knowledge_base_ids": [str(BASE_ID)]}
    )
)
assert found["status"] == "ok"
assert found["pages"][0]["claims"][0]["text"] == "第九条但最相关的结论"
assert found["degraded"] is False
assert found["pages"][0]["claims"][0]["citations"] == ["[1]"]
```

`wiki_read_page` 增加测试：无 query 按 position 分页；有 query 时返回相关 claim；无效 cursor 返回稳定输入错误。

- [ ] **Step 2: 运行并确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_tools.py -q
```

Expected: FAIL，fake 调用仍走 `agent_pages()` 或 schema 不接受新参数。

- [ ] **Step 3: 修改工具构造签名**

```python
def create_knowledge_tools(
    service: KnowledgeRetrievalService,
    wiki_service: WikiService | None = None,
    wiki_retrieval: WikiRetrievalService | None = None,
) -> list[BaseTool]:
```

只有两个 Wiki dependency 都存在时注册 Wiki 工具。`wiki_search` 一次性把整个 scope tuple 传给新服务，不再逐库循环，不再执行 `pages[:8]`。

- [ ] **Step 4: 注册原文 citations**

每个 `WikiMatchedClaim.sources` 通过当前 turn citation registry 注册；输出同时包含 `page_id`、title、score、matched_fields、claims、omitted count 和 degraded 信息。只有成功注册至少一个有效 source 的 claim 才能输出。

- [ ] **Step 5: 实现读页分页**

`WikiReadInput` 增加可选 `query`、`cursor` 和 `limit`，工具调用
`WikiRetrievalService.read_page()`：

```python
async def read_page(
    self,
    knowledge_base_id: UUID,
    page_id: UUID,
    *,
    query: str = "",
    cursor: int = 0,
    limit: int = 20,
) -> dict[str, Any] | None:
```

无 query 时按 `position, id` 分页；有 query 时由
`WikiRetrievalService.rank_page_claims()` 排序。响应返回 `next_cursor` 和
`omitted_claim_count`，不再固定截断 8 条。`WikiService` 仅保留页面写入、版本管理和
普通详情读取职责。

- [ ] **Step 6: 删除旧 Agent 搜索路径**

移除 `WikiService.agent_pages()`，或只保留非 Agent 私有兼容包装并标注弃用；生产工具代码不得再引用它。使用 `rg -n "trusted\[:8\]|pages\[:8\]|agent_pages" src` 验证旧路径已消失。

- [ ] **Step 7: 运行工具测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_tools.py tests/test_chat_knowledge_stream.py -q
```

Expected: PASS。

- [ ] **Step 8: 提交 Agent 改造**

```powershell
git add src/agent_api/knowledge/tools/readonly.py src/agent_api/knowledge/wiki/service.py tests/test_knowledge_tools.py
git commit -m "功能：按相关性返回Wiki可信结论"
```

## 工作包 5：前端 Wiki 搜索

### Task 10: 冻结 TypeScript API 契约

**Files:**
- Modify: `web/src/types/knowledge.ts`
- Modify: `web/src/services/knowledgeApi.ts`
- Modify: `web/tests/knowledge/knowledgeApi.spec.ts`

- [ ] **Step 1: 写 URL 编码失败测试**

```typescript
it('searches wiki with encoded hybrid options', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ pages: [], degraded: false, degradation_reason: null, truncation_reason: null }), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)

  await knowledgeApi.searchWiki('base', 'MFA + 双因素', { mode: 'hybrid', limit: 5, claimLimit: 12 })

  expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/knowledge-bases/base/wiki/search?q=MFA%20%2B%20%E5%8F%8C%E5%9B%A0%E7%B4%A0&mode=hybrid&limit=5&claim_limit=12')
})
```

- [ ] **Step 2: 运行并确认失败**

Run:

```powershell
Set-Location web
npm test -- --run tests/knowledge/knowledgeApi.spec.ts
```

Expected: FAIL，`searchWiki` 不存在。

- [ ] **Step 3: 增加完整类型**

新增 `WikiSearchSource`、`WikiSearchClaim`、`WikiSearchPage`、`WikiSearchResult` 接口；字段与后端 snake_case 响应完全一致，不在 API 层做隐式重命名。

- [ ] **Step 4: 实现 API 方法**

使用 `URLSearchParams` 构造 `q`、`mode`、`limit`、`claim_limit`，返回 `Promise<WikiSearchResult>`。

- [ ] **Step 5: 运行 API 测试**

Run:

```powershell
npm test -- --run tests/knowledge/knowledgeApi.spec.ts
```

Expected: PASS。

- [ ] **Step 6: 提交前端契约**

```powershell
Set-Location ..
git add web/src/types/knowledge.ts web/src/services/knowledgeApi.ts web/tests/knowledge/knowledgeApi.spec.ts
git commit -m "功能：增加Wiki搜索前端契约"
```

### Task 11: 增加 Wiki 搜索界面和命中定位

**Files:**
- Modify: `web/src/views/knowledge/WikiPanel.vue`
- Create: `web/tests/knowledge/wikiPanel.spec.ts`
- Modify: `web/tests/e2e/wiki.spec.ts`

- [ ] **Step 1: 写组件失败测试**

挂载 `WikiPanel`，mock API 后断言：

```typescript
await wrapper.get('input[aria-label="搜索 Wiki"]').setValue('双因素校验')
await wrapper.get('form[aria-label="Wiki 搜索"]').trigger('submit')
await flushPromises()

expect(searchWiki).toHaveBeenCalledWith('base', '双因素校验', { mode: 'hybrid', limit: 5, claimLimit: 12 })
expect(wrapper.text()).toContain('身份认证')
expect(wrapper.text()).toContain('普通员工需要两个独立认证因素')
expect(wrapper.text()).toContain('语义来源命中')
```

增加 degraded 提示、空结果、请求失败、清空搜索恢复目录四个测试。

- [ ] **Step 2: 运行并确认失败**

Run:

```powershell
Set-Location web
npm test -- --run tests/knowledge/wikiPanel.spec.ts
```

Expected: FAIL，搜索输入不存在。

- [ ] **Step 3: 实现搜索状态和竞态保护**

增加 `searchQuery`、`searching`、`searchResult`、`searchError`、递增 request token。知识库切换、组件卸载或后发请求完成时，旧响应不得覆盖当前页面。

- [ ] **Step 4: 实现可访问 UI**

- 工具栏增加带 label 的搜索表单和清除按钮。
- 结果显示页面类型、score、命中字段中文标签、命中 claim 和遗漏数量。
- degraded 时显示“语义检索暂不可用，当前结果已降级”。
- 点击结果复用 `openPage(page.id)`，打开后滚动到第一个命中 claim，并添加短暂高亮 class。
- 390px 宽度下搜索框、按钮和结果卡不能产生横向滚动。

- [ ] **Step 5: 更新 E2E mock 和断言**

`wiki.spec.ts` mock `/wiki/search`，执行搜索、打开结果、断言命中 claim 和来源仍可查看；桌面与移动项目都运行。

- [ ] **Step 6: 运行前端门**

Run:

```powershell
npm test -- --run tests/knowledge/wikiPanel.spec.ts tests/knowledge/knowledgeApi.spec.ts
npm run type-check
npm run lint
```

Expected: 全部 exit 0。

- [ ] **Step 7: 提交 Wiki 搜索 UI**

```powershell
Set-Location ..
git add web/src/views/knowledge/WikiPanel.vue web/tests/knowledge/wikiPanel.spec.ts web/tests/e2e/wiki.spec.ts
git commit -m "功能：增加Wiki混合搜索界面"
```

## 工作包 6：固定评测、真实集成和收口

### Task 12: 把 16 道 Wiki 问题转成机器可读评测

**Files:**
- Create: `tests/eval/wiki_cases.json`
- Create: `tests/eval/test_wiki_retrieval_quality.py`
- Modify: `tests/fixtures/wiki/enterprise-it-operations/TEST-QUESTIONS.md`

- [ ] **Step 1: 建立 JSON schema 和至少首批代表案例**

每个案例使用完整字段：

```json
{
  "id": "Q04",
  "queries": [
    "MFA、多因素认证和双因素校验是否是同一控制主题？",
    "双因素校验与 MFA 在这些制度里是什么关系？",
    "查找要求两个独立认证因素的控制"
  ],
  "expected_documents": ["02-identity-access-control.md", "03-remote-access-vpn-guide.md", "08-2026-security-baseline-change.md"],
  "expected_terms": ["两个独立认证因素", "抗钓鱼"],
  "forbidden_terms": ["短信验证码永久首选"],
  "answerable": true
}
```

Q16 设置 `answerable=false`、`expected_documents=[]`。一次提交必须覆盖原 16 题，每题至少 3 个 query，不允许只加入示例子集。

- [ ] **Step 2: 写评测测试**

测试导入 8 篇源语料、生成 Wiki、对所有 query 执行 `WikiRetrievalService.search()`，计算：

- page recall@5；
- 目标 claim recall@8；
- 目标文档 citation precision；
- synonym result consistency；
- 无答案可信误命中；
- `needs_review`/失效来源泄漏次数。

输出单行 `WIKI_EVAL {json}`，并断言已批准设计中的门槛：Page Recall@5 ≥ 0.90、
目标 claim Recall@8 ≥ 0.85、有效引用精确率 ≥ 0.95、同义改写结果一致率 ≥ 0.85、
无答案可信误命中率 ≤ 0.05，且 `needs_review`/失效来源泄漏数必须为 0。

- [ ] **Step 3: 先运行基线并记录失败**

在切换新服务前或用保留的旧实现 fixture 运行一次，把指标写入 pytest 输出和本地验证记录；旧实现预期至少在同义改写和第九条 claim 场景失败。不得为了让旧实现通过而降低门槛。

- [ ] **Step 4: 运行新实现真实评测**

Run:

```powershell
$env:RUN_KNOWLEDGE_INTEGRATION='1'
.\.venv\Scripts\python.exe -m pytest tests/eval/test_wiki_retrieval_quality.py -q -s
```

Expected: exit 0，并打印满足门槛的 `WIKI_EVAL`。若真实模型凭据未授权，生成阶段使用确定性 grounded generator，但必须明确记录这不是生成质量验收。

- [ ] **Step 5: 提交固定评测**

```powershell
git add tests/eval/wiki_cases.json tests/eval/test_wiki_retrieval_quality.py tests/fixtures/wiki/enterprise-it-operations/TEST-QUESTIONS.md
git commit -m "测试：增加Wiki混合检索固定评测"
```

### Task 13: 完整验证和文档收口

**Files:**
- Modify: `docs/verification/VERIFY-005.md`
- Modify: `docs/development/README.md`：增加 Wiki 固定评测命令和所需环境说明

- [ ] **Step 1: 后端快速门**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_wiki_ranking.py tests/test_retrieval_service.py tests/test_wiki_retrieval.py tests/test_wiki_generator.py tests/test_knowledge_tools.py tests/test_knowledge_models.py tests/test_lifespan.py tests/test_knowledge_api.py -q
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe src
```

Expected: 全部 exit 0。

- [ ] **Step 2: 后端完整门**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: exit 0；外部基础设施测试只能按既有 marker SKIP，不能出现新的未知 skip 或 collection error。

- [ ] **Step 3: 前端完整门**

Run:

```powershell
Set-Location web
npm test -- --run
npm run lint
npm run build
```

Expected: 全部 exit 0。

- [ ] **Step 4: 真实集成和浏览器门**

Run:

```powershell
Set-Location ..
$env:RUN_KNOWLEDGE_INTEGRATION='1'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_wiki_flow.py tests/eval/test_wiki_retrieval_quality.py -q -s
.\.venv\Scripts\python.exe -m tests.integration.run_browser_e2e
```

Expected: Wiki 集成、固定评测和桌面/移动浏览器全部通过；测试创建的数据被清理。

- [ ] **Step 5: 代码与安全检查**

Run:

```powershell
rg -n "trusted\[:8\]|pages\[:8\]|agent_pages|chunk\.content\[:350\]" src
git diff --check
git status --short
```

Expected: `rg` 无生产代码命中；`git diff --check` exit 0；`git status` 只包含本任务改动和开始前已存在的用户改动。确认日志不包含完整正文、查询文本、模型密钥或数据库 URL。

- [ ] **Step 6: 更新 VERIFY-005**

只记录实际运行过的命令、日期、指标和环境。若真实付费模型未运行，继续保留“生成质量未验收”；不得把确定性 generator 的检索通过写成真实模型生成质量通过。增加路线 C 状态：`planned / not implemented`，并引用设计规格的触发条件。

- [ ] **Step 7: 最终提交**

```powershell
git add docs/verification/VERIFY-005.md docs/development/README.md
git commit -m "文档：记录Wiki混合检索验证结果"
```

- [ ] **Step 8: 最终审查**

Run:

```powershell
git log --oneline --decorate -15
git diff cd860e3..HEAD --stat
```

Expected: 提交按“契约 → 迁移 → 生成 → 发布 → 排序 → 候选 → 检索 → API → Agent → 前端 → 评测 → 验证”顺序清晰；没有把路线 C 的独立 Wiki 向量索引混入本轮。

## 路线 C 后续入口

路线 B 完成后只创建后续需求，不直接编码。触发条件取自已批准设计：Page Recall@5 未达门槛、主题综合语义无法由来源 chunk 召回，或反向连接无法满足延迟目标。路线 C 的第一个计划应从“Wiki 搜索单元 schema + 独立 Milvus collection 生命周期 + 当前页面版本原子可见性”开始，而不是先做 GraphRAG。
