# Wiki 衍生知识实施计划草案

- 状态：approved；用户于 2026-09-22 回复“按此规格实施”
- 输入：`docs/requirements/REQ-006.md` 修订 8；`docs/superpowers/specs/2026-09-22-wiki-synthesis-design.md`
- 范围：每个知识库一个 Wiki，首版摘要／跨文档主题／目录、手动开启与增量生成、可核验来源、人工编辑与回滚、每库累计 token 上限、Agent 只读使用
- 执行方式：单任务顺序实施；共享 schema、迁移、根配置和前后端契约由同一执行者协调。工作包据此转为正式任务。

## 前置条件

1. REQ-006 与技术设计已完成规格确认；真实付费模型调用另行按已有授权处理。
2. 复核现有 `Document.active_version_id`、`Chunk`、outbox、Celery 路由、`KnowledgeView.vue` 和 `knowledge_scope` 的当前实现及工作树改动，避免覆盖其他已完成工作。
3. 冻结 Wiki HTTP schema 与前端 TypeScript 类型；旧知识库迁移后必须默认关闭 Wiki，不能在迁移中触发模型调用。

## 工作包与依赖

| 顺序 | 工作包与独立交付 | 主要文件归属 | 对应 AC | 前置 |
| --- | --- | --- | --- | --- |
| 1 | 数据与契约：Alembic 建表、约束、Wiki Pydantic/TS 类型、库内归属及稳定来源定位；迁移前后旧 RAG 行为一致 | `migrations/`、`src/agent_api/knowledge/wiki/domain/`、`src/agent_api/knowledge/wiki/api/schemas.py`、`web/src/types/wiki.ts` | AC-001、AC-003、AC-006 | 规格确认 |
| 2 | 生成闭环：开启已有文档扫描、活动版本切换事件、幂等 Worker、摘要／主题生成、确定性目录、来源校验、失败恢复 | `src/agent_api/knowledge/application/`、`src/agent_api/knowledge/infrastructure/jobs/`、`src/agent_api/knowledge/wiki/{application,infrastructure,worker}/` | AC-002、AC-008 | 1 |
| 3 | 用量与失效：每库累计 token 预留/结算/暂停恢复，文档更新或删除的 claim 失效和读时校验，Wiki 停用/重启检查 | `src/agent_api/knowledge/wiki/`、相关文档激活与删除事务 | AC-005、AC-009、AC-010 | 2 |
| 4 | 浏览与人工维护：知识库 Wiki 页签、摘要/主题/目录导航、来源定位、编辑、版本差异、审核、回滚、用量与任务状态；桌面及移动沿用现有视觉基线 | `web/src/views/knowledge/`、`web/src/services/knowledgeApi.ts` 或独立 Wiki API、`web/src/types/wiki.ts` | AC-001、AC-003、AC-004、AC-007、AC-008～AC-010 | 1～3 |
| 5 | Agent 只读与全栈验收：作用域过滤、可信 claim 限制、原文 citation 回读、固定 Wiki 评测集与真实 API/Worker/浏览器闭环 | `src/agent_api/knowledge/wiki/tools/`、`src/agent_api/llm/`、`tests/integration/`、`tests/eval/`、`web/tests/e2e/`、`docs/verification/` | AC-001～AC-010 | 1～4 |

## 验收门

- 工作包 1：迁移前后现有知识库数据、文档引用和聊天 API 可读；Wiki 默认关闭；约束能拒绝跨库来源和重复页面身份。
- 工作包 2：两篇以上真实文档形成各自摘要、一个有多来源证据的主题页和可导航目录；Worker 重试或重启不重复发布。
- 工作包 3：来源切换或删除到失效标记完成之间，读时校验仍阻止 Agent 引用旧 claim；预算耗尽暂停，增加上限后继续且用量不重复计入；停用 Wiki 不触发新生成。
- 工作包 4：人工编辑后增量只进待审核；回滚创建新版本；桌面和移动均能查看差异、来源状态与暂停原因。
- 工作包 5：REQ-005 全部必要回归保持通过；Wiki 固定集记录多文档问题覆盖、有效引用比例、待核实引用次数（必须为 0）、生成 token 与任务完成率。真实付费模型联网试验只在独立授权及预算内执行。

## 主要风险与处理

- **引用漂移**：现有 `replace_chunks()` 会重建 chunk UUID。保存版本、序号、内容哈希及位置快照，读时校验，不能只存 chunk UUID。
- **异步窗口**：来源切换和 Wiki 失效任务不同时完成。Agent 查询必须实时验证来源是否仍为活动 ready 版本；后台事件负责持久化页面提示。
- **生成幻觉**：模型产出每条 claim 都需映射到已读取 chunk，缺证据则保留为待核实，不进入可信回答。
- **人工内容覆盖**：页面发布使用 `base_published_version_id` 乐观并发检查，自动更新人工编辑与回滚后的页面只产生待审核版本。
- **用量偏差**：调用前预留输入估算加最大输出，供应商无 usage 时保守占用预留并在 UI 明示估算；重试按调用幂等键去重。

## 收尾与后续授权

此计划说明顺序、文件归属与验收。用户已批准实施，可据此建立正式任务 ID、`read_refs`、`write_scope`、依赖与负责人；完成各工作包后由单独集成验收任务关闭 AC。GraphRAG、实体页、Agent 写 Wiki 与跨知识库综合均不在首版实施计划中。
