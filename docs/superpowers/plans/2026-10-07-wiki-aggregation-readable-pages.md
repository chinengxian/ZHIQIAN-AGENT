# Wiki 聚合页可读化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** 在不调用模型的情况下，把现有 Wiki 首页和主题页升级为来源可追溯的结论优先阅读体验，并完成常驻 worker 更新与已有页面重建。

**Architecture:** 后端在现有 \`WikiProcessor\` 聚合事务中，从文档摘要页的 \`summary_section\` claim 按 source chunk 交集构建主题段落，同时保留 \`topic_fact\` 作为折叠证据层。新增无模型聚合重建入口供现有数据升级；前端复用既有 \`summary_sections\` 契约，把首页渲染为导航卡、主题页渲染为结论优先页面。完成测试后重启 Celery worker，并对当前知识库执行一次聚合重建。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy async、Celery/Redis、PostgreSQL、Vue 3、TypeScript、Vitest、Pytest、Playwright。

---

### Task 1: 为主题聚合段落建立失败回归测试

**Files:**
- Modify: \`tests/integration/test_wiki_flow.py\`
- Modify: \`tests/test_wiki_api.py\`

- [ ] **Step 1: 扩展集成测试生成器并写失败断言**

在集成测试的 fake generator 中让两个文档输出“概述”和“核心要求”段落；两个文档处理完成后增加以下断言：

~~~python
topic_detail = await service.get_page(knowledge_base_id, topic["id"])
assert [section["title"] for section in topic_detail["summary_sections"]] == [
    "文档 1 · 概述",
    "文档 2 · 概述",
]
assert all(section["sources"] for section in topic_detail["summary_sections"])
assert all(claim["claim_type"] == "topic_fact" for claim in topic_detail["claims"])
~~~

在 API 测试中增加一个包含 \`summary_sections\` 与 \`topic_fact\` 的主题页响应，确认 Pydantic 契约同时接受两类数据。

- [ ] **Step 2: 运行测试并确认当前实现失败**

运行：

~~~powershell
$env:AGENT_WIKI_TEST_DATABASE_URL='postgresql+asyncpg://agent:agent@127.0.0.1:5432/agent_wiki_test_20261007'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_wiki_flow.py::test_wiki_deduplicates_repeated_topic_membership_and_keeps_review_boundary tests/test_wiki_api.py -q
~~~

预期：主题页 \`summary_sections\` 为空，因为当前聚合器只写入 \`topic_fact\`。

- [ ] **Step 3: 提交测试变更**

~~~powershell
git add tests/integration/test_wiki_flow.py tests/test_wiki_api.py
git commit -m "测试：补齐Wiki主题聚合段落契约"
~~~

### Task 2: 在后端生成来源驱动的主题摘要段落

**Files:**
- Modify: \`src/agent_api/knowledge/wiki/processor.py:672-837\`
- Modify: \`tests/integration/test_wiki_flow.py\`

- [ ] **Step 1: 为每个主题收集匹配的 \`summary_section\` claim**

在 \`_rebuild_topic_pages\` 读取每个摘要页的 \`summary_fact\` 前，查询该版本的 \`summary_section\` claim 及 \`WikiClaimSource\`。仅当 section 的来源 \`chunk_id\` 与 \`topic_source_ids\` 有交集时加入候选；以源 section claim ID 去重，并保存其原始来源列表。

~~~python
selected_sections: list[tuple[WikiPage, WikiClaim, list[WikiClaimSource]]] = []
selected_section_ids: set[UUID] = set()
~~~

- [ ] **Step 2: 运行 Task 1 测试确认仍失败于未写入主题 sections**

运行同一条集成测试命令，确认失败点仍为主题页缺少 \`summary_sections\`，而不是数据库或测试装置错误。

- [ ] **Step 3: 写入主题页 \`summary_section\` claims 和标题元数据**

在主题 metadata 中增加现有契约可读取的字段：

~~~python
"summary_sections": [
    {
        "claim_key": f"section-{position + 1}",
        "title": f"{source_page.title} · {source_claim_title}",
    }
    for position, (source_page, source_claim, source_claim_title, sources)
    in enumerate(selected_sections)
],
"presentation_metadata": [
    {
        "key": "document_count",
        "label": "涉及文档",
        "value": str(len({item[0].id for item in items})),
    },
    {
        "key": "verified_fact_count",
        "label": "已核实事实",
        "value": str(len(selected)),
    },
]
~~~

为每个 section 写入 \`WikiClaim(claim_type="summary_section")\`，复制其来源快照；随后按原逻辑写入去重后的 \`topic_fact\`。当现有主题页内容未变化时保持幂等，不创建新版本或重复 claims。

- [ ] **Step 4: 运行后端回归并提交**

~~~powershell
$env:AGENT_WIKI_TEST_DATABASE_URL='postgresql+asyncpg://agent:agent@127.0.0.1:5432/agent_wiki_test_20261007'
.\.venv\Scripts\python.exe -m pytest tests/test_wiki_processor.py tests/integration/test_wiki_flow.py tests/test_wiki_api.py -q
.\.venv\Scripts\python.exe -m ruff check src/agent_api/knowledge/wiki/processor.py tests/integration/test_wiki_flow.py tests/test_wiki_api.py
.\.venv\Scripts\python.exe -m mypy src/agent_api/knowledge/wiki/processor.py src/agent_api/knowledge/wiki/service.py
~~~

~~~powershell
git add src/agent_api/knowledge/wiki/processor.py tests/integration/test_wiki_flow.py tests/test_wiki_api.py
git commit -m "功能：生成来源驱动的Wiki主题摘要段落"
~~~

### Task 3: 增加无模型的聚合页重建入口

**Files:**
- Modify: \`src/agent_api/knowledge/wiki/processor.py\`
- Modify: \`src/agent_api/knowledge/wiki/service.py\`
- Modify: \`src/agent_api/knowledge/wiki/api.py\`
- Modify: \`tests/integration/test_wiki_flow.py\`
- Modify: \`tests/test_wiki_api.py\`

- [ ] **Step 1: 写失败测试验证重建不会创建 Wiki 生成任务**

为 \`WikiService\` 增加集成测试：先创建已发布的摘要页和主题旧页，调用聚合重建方法，断言主题页版本被更新、\`wiki_jobs\` 数量不变、\`wiki_token_usage\` 数量及 \`tokens_charged\` 不变。

~~~python
before_jobs = await count_wiki_jobs(session, knowledge_base_id)
before_usage = await count_token_usage(session, knowledge_base_id)
await service.rebuild_aggregation_pages(knowledge_base_id)
assert await count_wiki_jobs(session, knowledge_base_id) == before_jobs
assert await count_token_usage(session, knowledge_base_id) == before_usage
~~~

- [ ] **Step 2: 运行测试确认入口尚不存在**

运行新增测试，预期收到 \`AttributeError\` 或 404，证明测试覆盖了尚未实现的入口。

- [ ] **Step 3: 实现事务型聚合重建**

新增 \`WikiProcessor.rebuild_aggregation_pages(sessions, knowledge_base_id)\`：开启一个数据库事务，调用 \`_rebuild_topic_pages\` 与 \`_rebuild_index\`，不读取模型、不调用 \`schedule_document_wiki\`。在 \`WikiService\` 暴露同名方法；API 增加 \`POST /api/v1/knowledge-bases/{knowledge_base_id}/wiki/rebuild-aggregation\`，返回知识库 ID、主题页数量、文档摘要页数量。

- [ ] **Step 4: 运行测试并提交**

~~~powershell
$env:AGENT_WIKI_TEST_DATABASE_URL='postgresql+asyncpg://agent:agent@127.0.0.1:5432/agent_wiki_test_20261007'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_wiki_flow.py tests/test_wiki_api.py -q
.\.venv\Scripts\python.exe -m ruff check src/agent_api/knowledge/wiki/processor.py src/agent_api/knowledge/wiki/service.py src/agent_api/knowledge/wiki/api.py
~~~

~~~powershell
git add src/agent_api/knowledge/wiki/processor.py src/agent_api/knowledge/wiki/service.py src/agent_api/knowledge/wiki/api.py tests/integration/test_wiki_flow.py tests/test_wiki_api.py
git commit -m "功能：增加Wiki聚合页无模型重建入口"
~~~

### Task 4: 实现首页导航卡和主题结论优先界面

**Files:**
- Modify: \`web/src/views/knowledge/WikiPanel.vue\`
- Modify: \`web/tests/knowledge/wikiPanel.spec.ts\`
- Modify: \`web/src/types/knowledge.ts\`（仅在 API 响应类型需要补齐时修改）

- [ ] **Step 1: 写失败的 Vitest 断言**

增加三个行为测试：

~~~typescript
it('首页显示主题和文档摘要导航卡而不是 Markdown 目录', async () => {
  // listWikiPages 返回 index、topic、summary 三类页面
  // 打开 index 后断言显示“主题入口”“文档摘要入口”，不显示原始目录符号
})

it('主题页默认显示摘要段落并把原子事实收进折叠区', async () => {
  // getWikiPage 返回 summary_sections 与 topic_fact
  // 断言段落可见、事实文本初始不可见，展开 details 后事实可见
})

it('主题段落可以跳转到对应文档摘要页', async () => {
  // pages 中提供 slug 为 summary/{document_id} 的摘要页
  // 点击“打开文档摘要”后断言 getWikiPage 使用摘要页 ID
})
~~~

- [ ] **Step 2: 运行 Vitest 确认失败**

运行：

~~~powershell
Set-Location web
npm run test:unit -- --run tests/knowledge/wikiPanel.spec.ts
Set-Location ..
~~~

预期新断言失败，因为当前 index 仍显示 \`page.content\`，topic 的 claims 也默认展开。

- [ ] **Step 3: 实现最小前端改动**

在 \`WikiPanel.vue\` 中：

- 对 \`page.page_type === 'index'\` 使用 \`pages\` 计算主题卡和摘要卡，卡片按钮调用现有 \`openPage\`；
- 对 topic 复用现有 \`summary-section\` 模板，给来源项增加匹配摘要页的“打开文档摘要”按钮；
- 将 topic 的 facts 放入原生 \`details\` 折叠区，summary 文案为“全部已核实事实（数量）”，展开后复用现有 claim 来源渲染；
- 保留人工版本与 legacy 页面原有 \`pre\` 展示；
- 所有新增可见文案和测试注释使用中文。

- [ ] **Step 4: 运行前端验证并提交**

~~~powershell
Set-Location web
npm run test:unit -- --run tests/knowledge/wikiPanel.spec.ts
npm run lint
npm run type-check
Set-Location ..
~~~

~~~powershell
git add web/src/views/knowledge/WikiPanel.vue web/src/types/knowledge.ts web/tests/knowledge/wikiPanel.spec.ts
git commit -m "功能：优化Wiki首页与主题页可读展示"
~~~

### Task 5: 重启 worker 并重建现有聚合页

**Files:**
- Runtime operation only; no source file changes.

- [ ] **Step 1: 在重启前确认没有运行中的 Wiki 任务**

~~~powershell
docker compose exec -T postgres psql -U agent -d agent -P pager=off -c "SELECT status, stage, COUNT(*) FROM wiki_jobs GROUP BY status, stage ORDER BY status, stage;"
~~~

只有 \`succeeded / ready\` 时继续；若存在 \`running\`，先等待其完成。

- [ ] **Step 2: 重启当前 Celery knowledge worker**

使用当前项目的 worker 启动配置重启：

~~~powershell
.\.venv\Scripts\python.exe -m celery -A agent_api.knowledge.worker.celery_app:app worker --pool=solo -Q knowledge --loglevel=INFO --without-gossip --without-mingle
~~~

启动后确认 worker 已注册 \`agent_api.knowledge.process_event\`、\`agent_api.knowledge.recover_stale_wiki_jobs\` 和 \`agent_api.knowledge.dispatch_outbox\`。

- [ ] **Step 3: 调用无模型聚合重建入口**

对已恢复的知识库调用新 API：

~~~powershell
$body = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/v1/knowledge-bases/c2cd4ae8-4e83-42bd-869c-aceb171593ab/wiki/rebuild-aggregation'
$body
~~~

记录返回的主题页与摘要页数量；不要调用文档重新入库或 \`wiki-v2\` 生成接口。

- [ ] **Step 4: 核验 token 与页面数据**

~~~powershell
docker compose exec -T postgres psql -U agent -d agent -P pager=off -c "SELECT status, stage, COUNT(*) FROM wiki_jobs GROUP BY status, stage; SELECT COUNT(*), COALESCE(SUM(charged_tokens),0) FROM wiki_token_usage; SELECT COUNT(*) FROM wiki_claims WHERE claim_type = 'summary_section';"
~~~

确认 Wiki job 全部成功、token 使用量未因重建增加、主题页存在结构化 \`summary_section\` 及其来源。

### Task 6: 端到端验收与回归

**Files:**
- Modify: \`docs/verification/VERIFY-005.md\`
- Modify: \`docs/tasks/TASK-031.md\`

- [ ] **Step 1: 运行后端完整 Wiki 回归**

~~~powershell
$env:AGENT_WIKI_TEST_DATABASE_URL='postgresql+asyncpg://agent:agent@127.0.0.1:5432/agent_wiki_test_20261007'
.\.venv\Scripts\python.exe -m pytest tests/test_wiki_processor.py tests/test_wiki_api.py tests/integration/test_wiki_flow.py tests/integration/test_wiki_celery.py -q
~~~

- [ ] **Step 2: 运行前端与浏览器验收**

~~~powershell
Set-Location web
npm run lint
npm run type-check
npx playwright test --config=playwright.wiki.config.ts
Set-Location ..
~~~

浏览器验收：首页导航卡、主题页结论优先、来源展开、事实折叠、文档摘要跳转和 legacy 页面兼容。

- [ ] **Step 3: 更新验证文档并检查 diff**

在验证文档记录：重建前后页面数量、\`summary_section\` 数量、来源数量、\`tokens_charged\` 是否变化和 worker 重启时间。运行：

~~~powershell
git diff --check
git status --short
~~~

仅在确认没有误纳入用户既有修改后提交：

~~~powershell
git add docs/verification/VERIFY-005.md docs/tasks/TASK-031.md
git commit -m "验证：完成Wiki聚合页可读化验收"
~~~

## 计划自检

- 首页导航、主题结论优先、来源展开、事实折叠、legacy 兼容均有对应 Task 4 验收。
- \`summary_section\` 来源筛选、别名去重和幂等发布均有 Task 1、Task 2 集成测试。
- 无模型重建、token 不变和事务回滚边界均有 Task 3、Task 5 验收。
- worker 重启与现有 8 份数据升级均有 Task 5 操作步骤。
- Route C 搜索向量索引明确列为非目标，避免本计划越界。

