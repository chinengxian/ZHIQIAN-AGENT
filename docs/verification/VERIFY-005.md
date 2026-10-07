# VERIFY-005 知识库 Wiki 实施验证

- 范围：REQ-006、TASK-027～TASK-031，以及 2026-10-06 Wiki 来源驱动混合检索路线 B
- 日期：2026-09-22；路线 B 复验于 2026-10-06
- 环境：Windows、Python 3.11、本地 Docker PostgreSQL/Redis/Milvus、Node.js 21.1.0
- 状态：**真实数据库、Worker 与浏览器闭环已复验；固定检索集未达质量门，路线 C 触发；真实付费模型质量未完成**

## 2026-10-06 路线 B 增量验证

## 2026-10-07 可读摘要增量验证

- 自动摘要改为“原子事实抽取 → 基于事实的摘要分段合成”；分段与来源单独返回，检索、主题页和 Agent 仅消费 `summary_fact/topic_fact`。
- 页面标题优先取受限 Front Matter，信息卡仅展示白名单字段；旧页面安全清理 Markdown 标记后兼容展示。
- 本轮实际结果：后端聚焦 31 passed、前端 36 passed、`npm run lint` 和 `npm run build` 通过。
- `pytest -q` 在本轮 30 秒执行窗口内未返回可记录的完成结果；独立数据库集成、浏览器与真实模型质量仍为 not_run。

## 2026-10-07 真实闭环复验

- 仅新建并使用 `agent_wiki_test_20261007` 独立 PostgreSQL 测试库；执行 Alembic `upgrade → downgrade(20261006_0003) → upgrade`，最终版本为 `20261007_0004`。既有 `agent` 业务库未迁移。
- `tests/integration/test_wiki_flow.py` 通过，覆盖可读摘要的页面生成、来源追溯、主题、审核/回滚、额度和停用恢复。
- `tests/integration/test_wiki_celery.py` 通过：失败 outbox 重试、Docker Redis 投递、真实 Celery Worker 消费与 v2 两阶段摘要生成。模型 stub 每次调用 60 token，实际累计结算为 120 token。
- 评测和集成测试均清理自己的 Wiki outbox/config；固定评测为每次运行创建并删除唯一 Milvus collection，避免触碰共享 `knowledge_chunks`。
- `web/tests/e2e/wiki.spec.ts` 桌面/移动 2 passed，验证信息卡、可读摘要分段、按分段展开的来源、版本审核及关闭后的浏览。

### 固定检索集结果（失败，作为路线 C 触发证据）

`tests/eval/test_wiki_retrieval_quality.py -q -s` 使用确定性 grounded generator、真实 PostgreSQL/Milvus 和 16 道题的 3 个查询变体，输出：

```text
WIKI_EVAL {"page_recall_at_5": 0.3537, "claim_recall_at_8": 0.5196,
"citation_precision": 0.8395, "synonym_result_consistency": 0.6333,
"no_answer_false_match_rate": 1.0, "needs_review_leaks": 0,
"invalid_source_leaks": 0}
```

- `Page Recall@5` 未达到 0.90，`Claim Recall@8` 未达到 0.85，引用精度、同义查询一致性和无答案抑制也均未达标。
- 失效来源与待核实结论均未泄漏，可信过滤仍然通过。
- 因此路线 B 不能作为“真正可用”的检索闭环结论；按既定门槛，路线 C（独立 Wiki 向量索引与评测）现为下一步目标。该路线尚未实施。

### 已验证能力

| 能力 | 证据 | 结果 |
| --- | --- | --- |
| 摘要与主题来源化 | 生成协议输出逐条 `source_indices`；发布 claim 带 `position`、`claim_type` 与 chunk 引用；正文由引用 claim 确定性组装 | pass |
| 混合召回 | 页面标题、slug、摘要、别名的 PostgreSQL 候选与原文 Dense/BM25 chunk 反向定位通过 RRF 融合 | pass |
| 相关性与预算 | claim 按来源命中与词面相关性重排，使用 MMR 去重，并受页面、claim、字符预算约束 | pass |
| 可信过滤 | 当前版本、`verified` claim、活动文档版本和 chunk 内容哈希共同决定结果可见性 | pass |
| API 与 Agent | Web API 返回分页混合结果、降级原因和命中字段；Agent 不再读取固定前 8 条 claim | pass |
| 前端搜索 | 支持主题/摘要/来源搜索、降级与失败状态、清除恢复目录、打开页面并定位命中 claim | pass |
| 固定评测定义 | `tests/eval/wiki_cases.json` 覆盖 Q01～Q16，每题 3 个查询变体；Q16 为无答案题 | pass |

### 本轮实际质量门

| 命令或检查 | 结果 |
| --- | --- |
| 聚焦后端测试 | 47 passed |
| `pytest -q` | 189 passed、39 skipped |
| `ruff check src tests` | 通过 |
| `mypy src` | 通过，63 个源文件 |
| `npm test -- --run` | 34 passed（11 个测试文件） |
| `npm run lint` | 通过 |
| `npm run build` | 通过 |
| 固定评测结构测试 | 1 passed；真实质量用例因缺少独立数据库 URL 跳过 |
| 旧生产路径扫描 | `trusted[:8]`、`pages[:8]`、`agent_pages`、`chunk.content[:350]` 无命中 |
| `git diff --check` | 通过；仅显示现有 CRLF 转换提示 |

全量测试第一次运行暴露生命周期测试未隔离新 Milvus 客户端，表现为尝试解析 `milvus:19530`。补充测试替身后目标测试通过。独立代码审查随后发现 slug 通配/排序和过期主题生命周期边界；补充 SQL 转义与排序、主题软删除和回归测试后，最终全量为 189 passed、39 skipped，且 reviewer 未发现剩余 Critical/Important。

### 未完成与环境边界

- 固定评测使用确定性 grounded generator，只隔离验证检索链路；本次结果失败，且无论是否通过都不能代替真实付费模型的摘要生成质量验收。
- 真实付费模型、供应商 token 账单和生产/共享环境迁移均未运行。
- `npm audit` 因配置的 npm 镜像不实现 audit API 而返回 404；当前虚拟环境未安装 `pip-audit`。这两项不得记为安全扫描通过。

### 路线 C 状态

路线 C 为 `planned / not implemented`。仅当 Page Recall@5 未达 0.90、主题综合语义无法通过来源 chunk 召回，或反向连接无法满足延迟目标时启动。下一步从 Wiki 独立向量 schema、独立 Milvus collection 生命周期和当前页面版本原子可见性开始，不在本轮混入 GraphRAG。

## 已验证

| 条件 | 证据 | 结果 |
| --- | --- | --- |
| AC-001、AC-003、AC-008 | Alembic 新建独立测试库；两文档及超过 12 个分块生成摘要、共同主题、目录；主题 claim 指向文档版本和分块 | pass |
| AC-004、AC-007 | 集成测试人工编辑、回滚、来源切换后待审核版本、审核发布及已发布版本保持不变 | pass |
| AC-005、AC-010 | 集成测试来源失效后 `needs_review`，Agent 只读服务过滤；关闭 Wiki 后页面仍可读而 Agent 返回空 | pass |
| AC-009 | 集成测试累计 token 用量、额度不足暂停、提高额度后续跑；API 暴露任务预估和实际用量 | pass |
| AC-002 | `tests/integration/test_wiki_celery.py`：模拟投递失败后 outbox 保留并退避重试；Worker 离线时经 Redis 入队，启动真实 Celery Worker 后消费 Wiki 事件、生成页面并记录模型 stub 的 60 token | pass |
| AC-001、AC-003、AC-004、AC-007、AC-010 的浏览器路径 | `web/tests/e2e/wiki.spec.ts` 桌面与移动各一例，覆盖开关、来源展开、版本对比、审核发布及关闭后浏览 | pass |
| 旧 RAG 代码回归 | 修复新增 Wiki 表与迁移版本引起的两个旧断言后，Python 全量测试 149 passed、37 skipped；`tests/test_knowledge_tools.py` 含 Wiki 范围和失效来源过滤 | pass |

## 质量门

| 命令 | 结果 |
| --- | --- |
| `pytest tests/integration/test_wiki_flow.py tests/test_knowledge_tools.py tests/test_knowledge_models.py -q`，设置独立测试库 URL | 10 passed |
| `pytest tests/integration/test_wiki_celery.py -q`，设置独立测试库 URL | 1 passed，真实 Redis/Celery 与本地模型 stub |
| `pytest -q` | 149 passed、37 skipped |
| `ruff check .`、`mypy src` | 通过，60 个源文件类型检查 |
| `npm test -- --run` | 27 passed |
| `npm run build` | 通过 |
| `playwright test --config=playwright.wiki.config.ts` | 2 passed |

## 尚待验收

- AC-006、AC-008：真实付费模型生成质量、固定问答集增益和供应商 token 账单对照，尚未运行。现有测试使用确定性生成器及本地兼容接口 stub，不代表真实模型质量。
- AC-002：Redis 服务本身中断未单独操作；已验证投递失败退避、Worker 离线入队及恢复消费。
- AC-009：模型调用中实际用量超过预留值时会记录真实用量；单次调用可能越过设定上限，后续任务暂停。需用真实供应商用量记录验证该边界。

## 环境隔离

测试仅迁移新建的 `agent_wiki_test_20260922`，未对既有业务库执行迁移或真实模型调用。浏览器测试拦截 Wiki API，不修改业务数据。既有工作树中 REQ-005 的未提交改动继续保留。

## 交付自检

| 维度 | 评分 | 依据及改进 |
| --- | --- | --- |
| 准确性 | 4/5 | 数据库、Worker、前端构建与浏览器均有运行结果；付费模型账单尚未对照，不能外推实测费用。 |
| 完整性 | 3/5 | 已实现 REQ-006 的页面、增量、审核、额度和 Agent 过滤；真实模型质量集及供应商用量验收仍缺，需在明确付费调用范围后补跑。 |
| 清晰性 | 4/5 | 本文把通过与未运行项分别列出；后续可补实际页面录屏帮助人工复核。 |
| 可执行性 | 4/5 | 提供独立测试库、具体测试文件和命令；真实供应商验证需额外授权与凭据。 |
| 简洁性 | 4/5 | 映射表可快速定位证据；任务文档与本文有少量重复。 |

平均 3.8/5。主要改进是补真实模型固定问答集与 token 对账；使用者应能从上面的未运行项看出当前结论的边界。
