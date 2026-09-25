# 知识库内 Wiki 衍生知识技术设计

- 状态：approved / confirmed
- 关联需求：`docs/requirements/REQ-006.md` 修订 8（AC-001～AC-010 为 required）
- 已有基础：FastAPI、PostgreSQL、Celery/Redis、Milvus、Vue 3；`Document.active_version_id` 是在线原文版本指针，`Chunk` 保留位置，outbox 支持任务恢复
- 范围依据：用户于 2026-09-22 回复“按此规格实施”；本设计作为实现依据

## 模块与数据流

Wiki 作为现有知识库的衍生模块，沿用 `knowledge_base_id`，不创建另一种知识库。文档和 chunk 仍是事实源；Wiki 页面是可编辑、可重建、有版本的解释层。`Milvus` 继续服务原文 Dense/BM25；首版 Wiki 页面搜索优先用 PostgreSQL 标题、别名与正文索引，避免再建一套向量写入管线。Agent 只读 Wiki，遇到具体事实应能回读现有原文工具。

```text
知识库手动启用 → 扫描当前 ready 活动文档版本 → 同事务写 Wiki 待处理项和 outbox
后续文档活动版本切换／删除请求 → 同事务记录来源变化和 Wiki 待处理项
outbox → Redis/Celery Wiki Worker → 额度预留 → 摘要／主题候选生成 → 引用校验
        → 页面草稿或待审核版本 → 目录索引更新 → 发布／暂停／失败状态
Vue 知识库 Wiki 页签 → 页面、来源、差异、审核、用量与任务状态
Agent 只读 wiki_search/wiki_read_page → 仅返回有效且已发布的可信结论
```

建议新增 `knowledge/wiki/{domain,application,infrastructure,api,worker,tools}`，共享现有数据库会话、outbox、Celery app、知识范围校验与错误格式。生成器通过独立 `WikiGenerator` 端口调用已配置聊天模型；不把 Wiki 写工具交给对话 Agent。首次生成先做每篇文档摘要，再从摘要和原文证据提取并合并跨文档主题，最后用确定性代码生成目录索引。每次模型输出必须经过结构校验和引用校验，无法定位来源的事实只进入待核实草稿，不进入可信发布内容。

### 触发与增量

1. `wiki_enabled` 默认关闭。用户为单个知识库首次开启时须同时设置正数 token 上限；按 `active_version_id` 为该库所有 ready、未删除文档建立幂等待处理项。没有 ready 文档时显示空 Wiki，不调用模型。关闭后保留页面供浏览，停止新生成和 Agent Wiki 工具命中；已排队任务在下一次模型调用前检查开关并暂停。重新开启时先复核现有来源状态，再补齐增量任务。
2. 新文档或重建版本在 `SqlAlchemyIngestionRepository.activate()` 成功切换活动版本的同一事务内写 `document.version.activated` outbox；失败版本不会触发 Wiki。对同一版本的重复事件用 `(knowledge_base_id, document_version_id, wiki_pipeline_version)` 去重。
3. 文档删除请求在管理事务中先记录来源失效和待处理项，再继续现有异步清理。知识库删除时取消待处理 Wiki 任务，并随知识库级联清理 Wiki 数据；已出队任务检查知识库是否仍存在，失败关闭。
4. 同一库的页面合并串行化，跨库可并发。Worker 重启后从 PostgreSQL 的待处理项与 outbox 恢复；重复执行不能重复记 token 或发布同一版本。
5. 原文新版本只重算关联的摘要与受影响主题，目录索引在页面集合变化后重建。主题候选先匹配现有 slug/别名，再做同名和语义重复判定；主题相关不等于同一主题，模型不能直接覆盖身份映射。旧页面在新结果完成前保持可读；引用已失效的结论标记待核实，并从 Agent 可信结果排除。为覆盖异步标记尚未完成的窗口，可信检索在读取时再次校验来源文档仍为 ready、未删除且 `active_version_id` 匹配；任一条件不满足即降为待核实。

## 数据与权限

PostgreSQL 是 Wiki 的权威存储。建议迁移增加：

| 表 | 核心字段与约束 | 用途 |
| --- | --- | --- |
| `wiki_configs` | `knowledge_base_id` 唯一 FK；`enabled`；`token_limit` 非负；`tokens_reserved`、`tokens_charged` 非负；`generation_model_fingerprint`；时间戳 | 每库开启状态与累计额度，不随日期清零 |
| `wiki_pages` | UUID、`knowledge_base_id`、稳定 `slug`、`page_type` (`summary`/`topic`/`index`)、标题、当前已发布版本 ID、状态；库内 slug 唯一 | 页面身份与导航；摘要 slug 绑定 `document_id`，避免标题变化产生新页 |
| `wiki_page_versions` | UUID、页面 ID、递增版本号、Markdown、摘要、`origin` (`pipeline`/`user`/`revert`)、`review_state`、`base_published_version_id`、时间戳；`(page_id, version_no)` 唯一 | 内容和来源不可变；审核状态可转换，待审核更新、diff 和回滚由发布指针单事务切换 |
| `wiki_claims` | 版本 ID、稳定 claim ID、结论文本或正文位置、`trust_state` (`verified`/`needs_review`)、原因 | 对受影响的结论单独标记，避免整页一概失效 |
| `wiki_claim_sources` | claim ID、`document_id`、`document_version_id`、chunk ID、kind/ordinal、内容哈希、标题与位置快照；复合唯一 | 结论到证据的可追溯关系；失效后仍能解释原来的出处 |
| `wiki_jobs` | 库、源版本、任务类型、状态、阶段、进度、幂等键、心跳、重试信息、预留 token、错误码 | 首次生成、增量、失效扫描及恢复 |
| `wiki_token_usage` | 调用幂等键唯一、库、任务、模型、预留 token、实测 token 可空、计费占用 token、时间戳 | 累计用量与重试去重；不得记录模型密钥或完整原文 |

旧文档版本和 chunk 在 `cleanup_version()` 后仍留 PostgreSQL，但 `replace_chunks()` 重试会重建 chunk UUID。因而引用必须同时保存版本、块序号和内容哈希，读取时校验是否仍匹配；不能只相信 chunk UUID。单篇文档删除会移除原文件和索引、保留数据库版本记录；页面可以展示旧来源快照，但其结论转为待核实。知识库删除会级联删除 Wiki；跨知识库引用首版不允许。

当前是固定单工作区、无登录。所有 Wiki API 复用 `DEFAULT_WORKSPACE_ID` 与知识库归属检查；`conversation_id` 不是权限凭证。将来增加多用户时，读、编辑、审核与额度调整需要独立权限，不能凭现有无登录状态推断所有用户可写。

### 页面状态、人工编辑与回滚

- 页面当前已发布版本与候选版本分离。自动更新人工编辑或回滚过的页面时创建 `pending_review` 版本，附来源差异；当前发布指针不动。审核通过时要求 `base_published_version_id` 仍匹配，否则返回冲突并重新生成差异。
- 每页最多保留一个当前待审核候选；若待审核期间又有来源变化，Worker 基于最新活动来源和当前已发布版本生成新的候选，旧候选留在历史中并标记 `superseded`，避免使用者审核过期内容。
- 人工编辑创建新版本。正文中的 claim 使用稳定标记关联 `wiki_claims`；人工新增或改写的事实若没有有效 `wiki_claim_sources`，标为 `needs_review`，不可成为 Agent 的可信引用。页面浏览仍显示该内容及状态。审核发布也不能把缺来源或已失效 claim 自动改成 `verified`。
- 回滚从历史版本复制内容与来源到新版本，`origin=revert`、版本号递增，历史不可改。复制后重新校验来源；已失效 claim 保持待核实。回滚后的后续增量与人工编辑相同，必须审核后发布。
- 页面链接使用稳定 slug；重命名保留别名或跳转。目录索引通过页面记录生成，不让模型直接维护目录 Markdown，避免死链和重复项。首版不建立 Neo4j；页面链接图可由现有关系计算，若需要可后续单独评估。

### token 上限

每库 `token_limit` 是自启用以来的累计生成 token 上限，不自动重置。调用前以输入估算值加明确的最大输出 token 数做额度预留，事务中检查 `tokens_charged + tokens_reserved + 新预留 <= token_limit`；不足则置任务 `paused_budget`，显示预计用量、已计费占用和调整入口。响应有供应商 usage 时将实测输入与输出 token 写入 `wiki_token_usage` 并结算；无 usage 时按预留量保守占用，界面将该项标为“估算占用”，不能声称实测。调用已发出后无法精确停止在上限边界，预留保证任务启动时不超额；重试使用调用幂等键防止重复入账。提高上限后重新入队暂停任务，沿已完成检查点继续。

## 接口契约

正式字段应落在后续 `src/agent_api/knowledge/wiki/api/schemas.py` 与 `web/src/types/wiki.ts`，OpenAPI 由 FastAPI 生成；此处先冻结设计级语义。现有 `/api/v1/knowledge-bases`、`/api/v1/documents` 和聊天 SSE 保持兼容。HTTP 错误沿用稳定 `code`、用户安全信息和关联 ID。

| 方法与路径 | 关键请求 | 关键响应／语义 |
| --- | --- | --- |
| `GET/PATCH /api/v1/knowledge-bases/{id}/wiki/config` | PATCH: `enabled?`, `token_limit?`（非负整数；首次开启须为正数） | 当前开关、上限、实测/估算/预留 token、生成状态；首次开启返回 202 并排队已有活动文档，缺上限返回 422 |
| `GET /api/v1/knowledge-bases/{id}/wiki/pages` | `page_type?`, `status?`, `cursor?`, `limit?` | 页面摘要、slug、当前版本、待审核/待核实计数；稳定分页 |
| `GET /api/v1/knowledge-bases/{id}/wiki/pages/{page_id}` | — | Markdown、claim 状态、来源快照与可回读位置、链接、已发布版本号 |
| `GET /api/v1/knowledge-bases/{id}/wiki/pages/{page_id}/versions` | `cursor?`, `limit?` | 历史及待审核版本元数据；单版本详情用于 diff |
| `PUT /api/v1/knowledge-bases/{id}/wiki/pages/{page_id}` | `base_version`, `content`, `title` | 乐观并发创建人工版本；旧 base 返回 409 |
| `POST /api/v1/knowledge-bases/{id}/wiki/pages/{page_id}/review` | `candidate_version_id`, `base_version`, `decision` (`publish`/`reject`) | 发布或驳回待审核版本；重复同一决定幂等，过期 base 返回 409 |
| `POST /api/v1/knowledge-bases/{id}/wiki/pages/{page_id}/revert` | `target_version_id`, `base_version` | 新版本号与重新校验后的 claim 状态；不会改历史 |
| `GET /api/v1/knowledge-bases/{id}/wiki/jobs` | `cursor?`, `limit?` | 各阶段、`paused_budget`、失败原因与进度；前端也可复用知识进度 SSE |
| `GET /api/v1/knowledge-bases/{id}/wiki/search` | `q`, `limit?` | 标题／主题候选与可信状态，限制单库范围；只读 Agent 服务复用同一应用层 |

路径参数均校验 UUID、工作区归属及知识库存在；不存在或已删除返回 404。停用 Wiki 后停止新生成、保留已发布页面可读并排除 Agent 使用；重新开启先核实来源。预算不足返回可恢复的 `paused_budget` 状态，不使用 500；模型故障区分可重试与永久错误。时间统一 UTC ISO 8601；版本号为整数；计量只使用整数 token，货币费用不作为上限单位。

Agent 首版增加 `wiki_search` 和 `wiki_read_page` 两个只读工具，沿会话 `knowledge_scope` 过滤知识库。工具只把 `verified` 且来源仍有效的 claim 标记为可信，并提供可回读原文的定位；`needs_review` 可以作为页面状态提示，但不能提供为回答依据。现有 `search_knowledge`、`read_document`、`list_documents` 保留，聊天引用 SSE 只为真实原文证据发 `sources`，Wiki 页身份可作为额外导航信息，不能代替原文 citation ID。

## 环境与验证

- 不新增长驻服务，沿用 API、Worker、PostgreSQL、Redis；新增 Alembic 迁移和 Wiki Worker 队列/路由。旧知识库默认关闭 Wiki，迁移不扫描或调用模型；开启后的首次任务显式入队。
- 迁移应先建表和约束，再上线写入逻辑；回滚部署先关 Wiki 生成并排空/停止任务，不直接删除含人工编辑的 Wiki 表。数据删除需单独操作及备份决定。
- 用确定性模型替身验证候选提取、来源校验、预算预留/结算、失败恢复、重复事件、人工并发审核与回滚。真实 PostgreSQL/Redis 路径验证活动版本切换和删除失效；浏览器验证桌面/移动的目录、来源、diff、暂停与继续。
- 维护固定 Wiki 问答集，分别测 Wiki 开关前后的多文档问题覆盖、有效引用比例、待核实内容被引用次数（目标为 0）、生成 token 与任务完成率；不凭主观样例决定把 Wiki 默认接入 Agent。REQ-005 的检索与聊天回归必须继续通过。

## 选择与影响

- 采用库内 Wiki 和 PostgreSQL 页面存储，复用现有工作区、版本与任务机制。WeKnora 的异步生成、页面版本和来源引用是参考；其实体页、Agent 写 Wiki、图谱及多租户权限不直接移植。
- 纯 Wiki 检索复用页面标题、主题与原文引用，增量成本主要是页面生成 token 和 PostgreSQL 索引维护，适合首版的主题浏览与跨文档概括。GraphRAG 还需实体/关系抽取、图存储或图查询能力、去重与失效修复；对关系链、多跳问题可能有收益，但当前固定评测集尚无证明，也会增加运维与调用成本。[WeKnora 知识图谱说明](https://github.com/Tencent/WeKnora/blob/main/docs/KnowledgeGraph.md) 使用可选 Neo4j，这只是参考，不构成本项目依赖。
- GraphRAG 的候选启用门槛：先在 Wiki+原文混合检索基线上收集明确的多跳关系问题失败样本；独立原型必须在固定集上提高正确且可追溯的回答比例，同时记录额外 token、查询延迟、索引维护与删除恢复成本；收益与成本经用户认可后再另立需求。首版不加 Neo4j、不加图谱任务。
- 已确认业务规则来自 REQ-006：每库手动开启、摘要/主题/目录、自动增量、累计 token 上限、人工编辑与回滚待审核、来源失效的结论待核实、停用时保留页面但停止生成和 Agent 使用。
- 常规 UI 细节在沿用现有视觉基线时落实：无有效来源的内容显示“待核实”；目录最多两级，超出时并入同级主题列表。首版不另设页面数量上限，生成规模受累计 token 上限约束。若真实文档验收暴露导航过载，再调整目录规则并复验 AC-008。
- 后续计划应分别覆盖迁移与后端生成、前端浏览编辑、Agent 只读接入、真实集成和质量评测；在规格确认前不创建实现任务，也不修改现有知识库或聊天代码。
