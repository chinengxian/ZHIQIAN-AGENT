# Wiki 可读摘要与可信事实分层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 让 Wiki 摘要页展示带来源的人读概述，同时让搜索、主题页和 Agent 继续只消费原子事实。

**Architecture:** 生成器先从原文 Chunk 抽取带来源编号的原子事实，再只基于这些已校验事实生成摘要分段。摘要分段和原子事实共用 WikiClaim 与 WikiClaimSource；API 分离返回，前端只把分段作为正文展示。Front Matter 仅以白名单方式生成 Wiki 标题和信息卡。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、SQLAlchemy/Alembic/PostgreSQL、LangChain、Vue 3、TypeScript、Vitest、pytest。

---

## 文件职责

| 文件 | 职责 |
| --- | --- |
| migrations/versions/20261007_0004_wiki_readable_summary.py | 为 summary_section 扩展可逆数据库约束。 |
| src/agent_api/knowledge/wiki/generator.py | 输出原子 Claim、主题和有来源的摘要分段。 |
| src/agent_api/knowledge/wiki/presentation.py | 安全解析标题和白名单元数据。 |
| src/agent_api/knowledge/wiki/processor.py | 校验、发布摘要分段、事实、标题与元数据。 |
| src/agent_api/knowledge/wiki/service.py | 将页面响应拆分为 summary_sections、metadata 和 claims。 |
| src/agent_api/knowledge/wiki/retrieval.py | 仅让原子事实进入检索和 Agent。 |
| src/agent_api/knowledge/wiki/api.py | 声明页面响应新增契约。 |
| web/src/types/knowledge.ts | 声明摘要分段、元数据和页面类型。 |
| web/src/views/knowledge/WikiPanel.vue | 展示信息卡、摘要段落、来源展开和旧页面兼容内容。 |

## Task 1: 建立摘要分段数据契约与迁移

**Files:**

- Create: migrations/versions/20261007_0004_wiki_readable_summary.py
- Modify: src/agent_api/knowledge/infrastructure/database/models.py:632-657
- Modify: tests/test_knowledge_models.py
- Modify: tests/test_wiki_processor.py

- [ ] **Step 1: 写失败测试**

在 tests/test_wiki_processor.py 新增：

~~~python
def test_generation_checkpoint_maps_summary_sections_to_exact_chunks() -> None:
    chunks = [_chunk(0, "开头"), _chunk(1, "中段"), _chunk(2, "尾部")]
    sections, claims, _ = parse_generation_checkpoint(
        {
            "summary_sections": [
                {
                    "title": "概述",
                    "text": "制度覆盖身份与访问控制。",
                    "source_indices": [1, 3],
                }
            ],
            "summary_claims": [
                {"text": "必须使用唯一身份。", "source_indices": [2]}
            ],
            "topics": [],
        },
        chunks,
    )

    assert sections[0].chunks == (chunks[0], chunks[2])
    assert claims[0].chunks == (chunks[1],)
~~~

在 tests/test_knowledge_models.py 加入 claim_type="summary_section" 的模型约束断言。

- [ ] **Step 2: 运行 RED**

Run: ./.venv/Scripts/python.exe -m pytest tests/test_wiki_processor.py tests/test_knowledge_models.py -q

Expected: 新测试因缺少 summary_sections 解析与 GroundedSection 而失败。

- [ ] **Step 3: 实现迁移与检查点结构**

创建迁移，revision 为 20261007_0004，down_revision 为 20261006_0003。升级时删除并重建 ck_wiki_claim_type；降级前拒绝遗留 summary_section 数据：

~~~python
def downgrade() -> None:
    count = op.get_bind().execute(
        sa.text("SELECT count(*) FROM wiki_claims WHERE claim_type = 'summary_section'")
    ).scalar_one()
    if count:
        raise RuntimeError("cannot_downgrade_wiki_readable_summary_with_sections")
    op.drop_constraint("ck_wiki_claim_type", "wiki_claims", type_="check")
    op.create_check_constraint(
        "ck_wiki_claim_type",
        "wiki_claims",
        "claim_type IN ('summary_fact', 'topic_fact', 'source_excerpt', 'manual')",
    )
~~~

把 SQLAlchemy 模型约束扩展为 summary_section、summary_fact、topic_fact、source_excerpt、manual。

在 processor.py 增加：

~~~python
@dataclass(frozen=True, slots=True)
class GroundedSection:
    title: str
    text: str
    chunks: tuple[Chunk, ...]
~~~

让 parse_generation_checkpoint() 返回 sections、claims、topics。新分段规则：

- 仅允许“概述”“核心要求”“例外与版本变化”。
- 1 至 3 段，标题不可重复。
- 文本去空白后为 1 至 2000 字符。
- source_indices 复用 _checkpoint_chunks() 校验。
- 缺失字段、重复标题、空文本报 wiki_generation_invalid_shape；空或越界来源报 wiki_generation_invalid_source。

为解析函数增加 `allow_legacy_sections: bool = False`：只有调用方明确传入 `True` 时，旧 `wiki-v1` 检查点缺少 `summary_sections` 才映射为空分段；其他缺失该字段的输入仍报 `wiki_generation_invalid_shape`。补充两条覆盖此分支的测试。

- [ ] **Step 4: 运行 GREEN**

Run: ./.venv/Scripts/python.exe -m pytest tests/test_wiki_processor.py tests/test_knowledge_models.py -q

Expected: PASS。

- [ ] **Step 5: 提交**

~~~bash
git add migrations/versions/20261007_0004_wiki_readable_summary.py src/agent_api/knowledge/infrastructure/database/models.py tests/test_knowledge_models.py tests/test_wiki_processor.py
git commit -m "数据库：支持Wiki可读摘要分段"
~~~

## Task 2: 生成两阶段可读摘要

**Files:**

- Modify: src/agent_api/knowledge/wiki/generator.py:13-214
- Modify: tests/test_wiki_generator.py

- [ ] **Step 1: 写失败测试**

把跨批次生成测试的 FakeListChatModel 响应改为三个：前两个为 Chunk 批次原子事实，第三个为摘要分段。加入断言：

~~~python
assert [(section.title, section.source_indices) for section in result.summary_sections] == [
    ("概述", (1, 13)),
    ("核心要求", (1,)),
]
assert result.summary == "制度概述\n关键控制"
~~~

增加参数化测试：摘要分段的 source_indices 为 []、[0]、[99]，或两个同名“概述”时，生成器抛出稳定 ValueError。

- [ ] **Step 2: 运行 RED**

Run: ./.venv/Scripts/python.exe -m pytest tests/test_wiki_generator.py -q

Expected: FAIL，因为 WikiGeneration 没有 summary_sections，且生成器没有第二阶段模型调用。

- [ ] **Step 3: 实现两阶段生成**

在 generator.py 新增：

~~~python
@dataclass(frozen=True, slots=True)
class WikiGeneratedSection:
    title: str
    text: str
    source_indices: tuple[int, ...]

@dataclass(frozen=True, slots=True)
class WikiGeneration:
    summary_sections: tuple[WikiGeneratedSection, ...]
    summary_claims: tuple[WikiGeneratedClaim, ...]
    topics: tuple[WikiGeneratedTopic, ...]
    measured_tokens: int | None

    @property
    def summary(self) -> str:
        return "\n".join(section.text for section in self.summary_sections)
~~~

保留 _generate_once() 负责每批最多 12 个 Chunk 的原子事实与主题抽取，但提示词必须要求不把 document_id、title、version、tags 等纯元数据列为 summary_claims。

在去重、合并原子 Claim 后，调用新增 _compose_sections(title, claims)。传入的事实格式为 [fact:N][sources:1,3] 文本。该方法要求模型只输出：

~~~json
{
  "summary_sections": [
    {"title": "概述", "text": "……", "source_indices": [1, 3]},
    {"title": "核心要求", "text": "……", "source_indices": [2]}
  ]
}
~~~

_parse_section() 必须复用 _source_indices()，并拒绝未知标题、重复标题、空文本和无来源。总 token 用量为所有批次及合成调用之和；任意调用没有 usage_metadata 时 measured_tokens 为 None。

- [ ] **Step 4: 运行 GREEN**

Run: ./.venv/Scripts/python.exe -m pytest tests/test_wiki_generator.py -q

Expected: PASS；全局 Chunk 编号在批次和最终摘要中保持一致。

- [ ] **Step 5: 提交**

~~~bash
git add src/agent_api/knowledge/wiki/generator.py tests/test_wiki_generator.py
git commit -m "功能：生成可读Wiki摘要分段"
~~~

## Task 3: 解析标题与白名单元数据

**Files:**

- Create: src/agent_api/knowledge/wiki/presentation.py
- Create: tests/test_wiki_presentation.py
- Modify: src/agent_api/knowledge/wiki/processor.py:382-477

- [ ] **Step 1: 写失败测试**

创建 tests/test_wiki_presentation.py：

~~~python
def test_extract_presentation_uses_front_matter_title_and_whitelist() -> None:
    presentation = extract_wiki_presentation(
        fallback_title="02-identity-access-control",
        chunks=[
            _chunk(
                0,
                "---\ntitle: 身份与访问控制规范\nversion: '3.0'\nsecret: 不展示\n---\n# 身份与访问控制规范",
            )
        ],
    )

    assert presentation.title == "身份与访问控制规范"
    assert [(item.key, item.value) for item in presentation.metadata] == [
        ("version", "3.0")
    ]

def test_extract_presentation_uses_h1_then_fallback_title() -> None:
    assert extract_wiki_presentation("file-name", [_chunk(0, "# 可读标题")]).title == "可读标题"
    assert extract_wiki_presentation("file-name", [_chunk(0, "没有标题")]).title == "file-name"
~~~

补充超长值、重复键、未知键和不完整 Front Matter 均不进入信息卡的测试。

- [ ] **Step 2: 运行 RED**

Run: ./.venv/Scripts/python.exe -m pytest tests/test_wiki_presentation.py -q

Expected: FAIL，模块尚不存在。

- [ ] **Step 3: 实现确定性解析**

创建 presentation.py：

~~~python
@dataclass(frozen=True, slots=True)
class WikiMetadataItem:
    key: str
    label: str
    value: str

@dataclass(frozen=True, slots=True)
class WikiPresentation:
    title: str
    metadata: tuple[WikiMetadataItem, ...]

def extract_wiki_presentation(
    fallback_title: str, chunks: Sequence[Chunk]
) -> WikiPresentation: ...
~~~

只读取首个非空 Chunk 开头的成对 --- 边界，并按单行 key: value 解析。只接受 document_id、title、version、effective_date、status、scope、owner、supersedes、tags。值去引号、去空白、上限 200 字符；tags 规范化成逗号分隔文本。不得使用 YAML 反序列化或执行输入。

标题依次使用 Front Matter title、首个非空 heading_path[0]、文本的首个 # 标题、fallback_title。处理器发布摘要页时以 presentation.title 替换 document.title，并在 generation_metadata 写入：

~~~python
"presentation_metadata": [
    {"key": item.key, "label": item.label, "value": item.value}
    for item in presentation.metadata
]
~~~

主题页不写入 presentation_metadata。

- [ ] **Step 4: 运行 GREEN**

Run: ./.venv/Scripts/python.exe -m pytest tests/test_wiki_presentation.py tests/test_wiki_processor.py -q

Expected: PASS。

- [ ] **Step 5: 提交**

~~~bash
git add src/agent_api/knowledge/wiki/presentation.py src/agent_api/knowledge/wiki/processor.py tests/test_wiki_presentation.py tests/test_wiki_processor.py
git commit -m "功能：提取Wiki页面标题与文档信息"
~~~

## Task 4: 发布、API 和检索隔离

**Files:**

- Modify: src/agent_api/knowledge/wiki/processor.py:98-157,382-777
- Modify: src/agent_api/knowledge/wiki/service.py:179-258,310-488
- Modify: src/agent_api/knowledge/wiki/retrieval.py:469-644
- Modify: src/agent_api/knowledge/wiki/api.py:36-75
- Modify: src/agent_api/knowledge/wiki/jobs.py:18
- Modify: tests/test_wiki_processor.py
- Modify: tests/test_wiki_retrieval.py
- Modify: tests/test_knowledge_tools.py
- Modify: tests/test_wiki_api.py
- Modify: tests/integration/test_wiki_flow.py

- [ ] **Step 1: 写失败测试**

在 tests/test_wiki_retrieval.py 构造同一页面的 summary_section 与 summary_fact，断言 rank_page_claims() 与 read_page() 仅返回 summary_fact。

在 tests/test_knowledge_tools.py 的 Fake Wiki 结果中加入 summary_section，断言 wiki_search 和 wiki_read_page 的 JSON 不包含该类型。

在 tests/test_wiki_api.py 的页面伪服务响应中加入：

~~~python
"summary_sections": [{
    "id": uuid4(),
    "title": "概述",
    "text": "规范定义身份控制要求。",
    "position": 0,
    "trust_state": "verified",
    "reason": None,
    "sources": [],
}],
"metadata": [{"key": "version", "label": "版本", "value": "3.0"}],
~~~

断言 API 接受新字段且 claims 不含 summary_section。

在 tests/integration/test_wiki_flow.py 断言自动摘要页标题来自 Front Matter、summary_sections 有来源、claims 不含 summary_section；来源失效后该段标记 needs_review，检索与 Agent 均不返回它。

- [ ] **Step 2: 运行 RED**

Run: ./.venv/Scripts/python.exe -m pytest tests/test_wiki_retrieval.py tests/test_knowledge_tools.py tests/test_wiki_api.py -q

Expected: FAIL，因为服务尚未拆分页面内容或排除摘要分段。

- [ ] **Step 3: 实现发布和响应契约**

在 processor.py：

1. 解包 sections、claims、topics。
2. render_summary_content() 仅拼接分段标题和文本，不输出 #、[^…] 或原文片段。
3. 每个分段写入 claim_key=section-{position + 1}、claim_type=summary_section 和对应 WikiClaimSource。
4. 原子事实保持 summary_fact，并使用稳定位置。
5. WikiPageVersion.summary 写入分段文本换行拼接；content 写入可读纯文本兼容正文。
6. 重建主题页时显式 where(WikiClaim.claim_type == "summary_fact")。

调用 `parse_generation_checkpoint()` 时根据任务幂等键仅为 `wiki-v1` 传入 `allow_legacy_sections=True`；`wiki-v2` 任务必须有摘要分段，避免新生成结果静默降级。

在 service.py 增加 _summary_sections_response()，沿用 _claims_response() 的来源有效性校验。_page_response() 返回：

~~~python
"summary_sections": summary_sections,
"metadata": list(version.generation_metadata.get("presentation_metadata", [])),
"claims": claims,
~~~

其中 claims 必须排除 summary_section。旧页面和人工页返回空 summary_sections、metadata。

在 retrieval.py 定义：

~~~python
RETRIEVABLE_CLAIM_TYPES = ("summary_fact", "topic_fact")
~~~

并在 _source_page_ids()、_hydrate_pages() 的 WikiClaim 查询中使用 claim_type.in_(RETRIEVABLE_CLAIM_TYPES)。这样 wiki_search、wiki_read_page、主题页和 Agent 不会读取摘要段落。

在 api.py 增加 WikiSummarySectionResponse 与 WikiMetadataResponse，并将其加入 WikiPageResponse。将 WIKI_PIPELINE_VERSION 改为 wiki-v2，避免新任务与旧检查点冲突；旧 wiki-v1 页面保持兼容读取。

- [ ] **Step 4: 运行 GREEN**

Run: ./.venv/Scripts/python.exe -m pytest tests/test_wiki_processor.py tests/test_wiki_retrieval.py tests/test_knowledge_tools.py tests/test_wiki_api.py -q

Expected: PASS；摘要段落可追溯但不参与主题、搜索和 Agent。

- [ ] **Step 5: 运行独立数据库集成测试（环境可用时）**

Run: $env:RUN_KNOWLEDGE_INTEGRATION='1'; $env:AGENT_WIKI_TEST_DATABASE_URL='<独立测试库 URL>'; ./.venv/Scripts/python.exe -m pytest tests/integration/test_wiki_flow.py -q

Expected: PASS。未设置独立数据库 URL 时记录 skipped；禁止使用共享或生产库。

- [ ] **Step 6: 提交**

~~~bash
git add src/agent_api/knowledge/wiki/processor.py src/agent_api/knowledge/wiki/service.py src/agent_api/knowledge/wiki/retrieval.py src/agent_api/knowledge/wiki/api.py src/agent_api/knowledge/wiki/jobs.py tests/test_wiki_processor.py tests/test_wiki_retrieval.py tests/test_knowledge_tools.py tests/test_wiki_api.py tests/integration/test_wiki_flow.py
git commit -m "功能：发布可读Wiki摘要并隔离检索事实"
~~~

## Task 5: 实现结构化前端摘要页

**Files:**

- Modify: web/src/types/knowledge.ts:95-143
- Modify: web/src/views/knowledge/WikiPanel.vue:1-482
- Modify: web/tests/knowledge/wikiPanel.spec.ts

- [ ] **Step 1: 写失败测试**

在 wikiPanel.spec.ts 的 getWikiPage mock 中加入：

~~~ts
summary_sections: [{
  id: 'section-overview',
  title: '概述',
  text: '本规范覆盖企业身份与访问控制。',
  position: 0,
  trust_state: 'verified',
  reason: null,
  sources: [{
    document_id: 'doc1',
    document_version_id: 'ver1',
    chunk_id: 'chunk1',
    document_title: '身份与访问控制规范',
    heading_path: ['身份分类'],
    page_start: null,
    page_end: null,
    valid: true,
  }],
}],
metadata: [{ key: 'version', label: '版本', value: '3.0' }],
~~~

新增断言：

~~~ts
expect(wrapper.text()).toContain('身份与访问控制规范')
expect(wrapper.text()).toContain('版本')
expect(wrapper.text()).toContain('3.0')
expect(wrapper.text()).toContain('本规范覆盖企业身份与访问控制。')
expect(wrapper.text()).toContain('查看 1 个来源')
expect(wrapper.text()).not.toContain('[^claim-')
~~~

触发来源开关后，断言文档标题、标题路径和 RouterLink 存在。再添加旧页面 mock：summary_sections 和 metadata 为空，content 为 # 旧标题\n- 旧事实 [^x]；断言显示“旧标题”和“• 旧事实”，不显示 Markdown 源码。

- [ ] **Step 2: 运行 RED**

Run: cd web; npm test -- --run tests/knowledge/wikiPanel.spec.ts

Expected: FAIL，因为前端类型和组件尚无新字段与结构化视图。

- [ ] **Step 3: 实现类型和组件**

在 knowledge.ts 新增 WikiPresentationSource、WikiSummarySection、WikiMetadataItem，并给 WikiPage 加入 summary_sections 与 metadata。

在 WikiPanel.vue：

1. 用 ref<Set<string>> 记录展开的段落来源，新增 toggleSectionSources(sectionId)。
2. 当 page.origin === 'pipeline' 且 summary_sections 非空时渲染信息卡、摘要分段和来源展开区。
3. 继续复用 sourceRoute()，使来源进入 REQ-007 文档预览深链。
4. 来源无效时显示“来源已失效 / 待核实”，段落保持可读。
5. 没有 summary_sections 时使用 legacyDisplayContent() 移除行首 #、列表 - 和脚注 [^…]，以普通文本展示；禁止 v-html。
6. 将现有 Claim 区标题改为“关键事实”，并添加移动端信息卡与来源区的换行样式。

- [ ] **Step 4: 运行 GREEN**

Run: cd web; npm test -- --run tests/knowledge/wikiPanel.spec.ts

Expected: PASS；结构化摘要可读、来源可展开、旧页面不暴露 Markdown 源码。

- [ ] **Step 5: 提交**

~~~bash
git add web/src/types/knowledge.ts web/src/views/knowledge/WikiPanel.vue web/tests/knowledge/wikiPanel.spec.ts
git commit -m "功能：展示Wiki可读摘要与来源"
~~~

## Task 6: 回归验证和交付证据

**Files:**

- Modify: docs/verification/VERIFY-005.md
- Create: docs/testing/wiki-readable-summary.tdd.md
- Modify: web/tests/e2e/wiki.spec.ts

- [ ] **Step 1: 写浏览器用例**

在 web/tests/e2e/wiki.spec.ts 添加 API 拦截场景，返回 summary_sections 和 metadata。断言标题、信息卡、摘要段落和“查看 1 个来源”可见；点击来源后 URL 包含 /knowledge/kb1/documents/doc1?version=ver1&chunk=chunk1。移动视口断言 document.documentElement.scrollWidth <= window.innerWidth。

- [ ] **Step 2: 运行聚焦质量门**

Run: ./.venv/Scripts/python.exe -m pytest tests/test_wiki_generator.py tests/test_wiki_presentation.py tests/test_wiki_processor.py tests/test_wiki_retrieval.py tests/test_wiki_api.py tests/test_knowledge_tools.py -q

Expected: PASS。

Run: cd web; npm test -- --run tests/knowledge/wikiPanel.spec.ts tests/knowledge/wikiSearchApi.spec.ts

Expected: PASS。

- [ ] **Step 3: 运行完整质量门**

Run: ./.venv/Scripts/python.exe -m pytest -q

Expected: PASS；准确记录 skipped 数量和原因。

Run: ./.venv/Scripts/python.exe -m ruff check src tests

Expected: All checks passed!

Run: ./.venv/Scripts/python.exe -m mypy src

Expected: Success: no issues found。

Run: cd web; npm test -- --run

Expected: PASS。

Run: cd web; npm run lint; npm run build

Expected: 两个命令均退出 0；必须串行执行，避免 Vitest 临时文件被 ESLint 扫描。

- [ ] **Step 4: 浏览器验证（Node 20+ 可用时）**

Run: cd web; npx playwright test --config=playwright.wiki.config.ts

Expected: PASS。Node 版本不足时在验证记录中标记 not_run，不以组件测试替代浏览器结论。

- [ ] **Step 5: 编写证据与提交**

创建 docs/testing/wiki-readable-summary.tdd.md，记录用户旅程、真实 RED 与 GREEN 命令输出、每项测试保证、覆盖边界、独立数据库与浏览器未运行项。更新 VERIFY-005.md，单独记录可读摘要验收与真实模型质量边界。

在证据中明确上线行为：数据库迁移不会调用模型重写已有 `wiki-v1` 页面；已有页面继续走兼容展示，后续对目标文档发起现有的新版本入库流程后，才会生成 `wiki-v2` 可读摘要。

~~~bash
git add docs/testing/wiki-readable-summary.tdd.md docs/verification/VERIFY-005.md web/tests/e2e/wiki.spec.ts
git commit -m "测试：验证Wiki可读摘要闭环"
~~~
