# 开发入口

## 当前范围

第一阶段定义一个完整的最小流式对话应用：后端使用 Python、LangChain 与 FastAPI，前端使用 Vue 3、TypeScript、Vite 与 Vuetify。模型在启动时通过必填配置选择 OpenAI 兼容或 Anthropic 策略，供应商、服务地址、API Key 与模型名仅由后端配置提供。

## 权威文档

- 需求与验收条件：[`../requirements/REQ-001.md`](../requirements/REQ-001.md)
- 前端需求与验收条件：[`../requirements/REQ-002.md`](../requirements/REQ-002.md)
- Agent 短期记忆增量需求：[`../requirements/REQ-003.md`](../requirements/REQ-003.md)
- 多模型策略增量需求：[`../requirements/REQ-004.md`](../requirements/REQ-004.md)
- 原生知识库与多级 RAG 需求：[`../requirements/REQ-005.md`](../requirements/REQ-005.md)
- 待决策事项：[`../decisions/DEC-001.md`](../decisions/DEC-001.md)
- 前端框架决定：[`../decisions/DEC-002.md`](../decisions/DEC-002.md)
- 首批多模型供应商决定：[`../decisions/DEC-003.md`](../decisions/DEC-003.md)
- 技术设计：[`../superpowers/specs/2026-09-17-streaming-chat-agent-design.md`](../superpowers/specs/2026-09-17-streaming-chat-agent-design.md)
- 前端设计：[`../superpowers/specs/2026-09-17-vue-chat-workbench-design.md`](../superpowers/specs/2026-09-17-vue-chat-workbench-design.md)
- UI 视觉基线：[`../design/ui/style.md`](../design/ui/style.md)
- 已失效的旧实施计划：[`../superpowers/plans/2026-09-17-streaming-chat-agent.md`](../superpowers/plans/2026-09-17-streaming-chat-agent.md)
- 当前全栈实施计划：[`../superpowers/plans/2026-09-17-full-stack-streaming-chat.md`](../superpowers/plans/2026-09-17-full-stack-streaming-chat.md)
- Agent 记忆改造计划：[`../superpowers/plans/2026-09-18-agent-memory-migration.md`](../superpowers/plans/2026-09-18-agent-memory-migration.md)
- Agent 记忆技术设计：[`../superpowers/specs/2026-09-18-agent-memory-design.md`](../superpowers/specs/2026-09-18-agent-memory-design.md)
- 多模型策略技术设计：[`../superpowers/specs/2026-09-19-multi-provider-model-strategy-design.md`](../superpowers/specs/2026-09-19-multi-provider-model-strategy-design.md)
- 多模型策略实施计划：[`../superpowers/plans/2026-09-19-multi-provider-model-strategy.md`](../superpowers/plans/2026-09-19-multi-provider-model-strategy.md)
- 原生知识库与多级 RAG 技术设计：[`../superpowers/specs/2026-09-20-native-knowledge-rag-design.md`](../superpowers/specs/2026-09-20-native-knowledge-rag-design.md)
- 原生知识库中文链路说明：[`knowledge-infrastructure-flow.md`](knowledge-infrastructure-flow.md)
- 集成验收报告：[`../verification/VERIFY-001.md`](../verification/VERIFY-001.md)
- Agent 记忆改造验收：[`../verification/VERIFY-002.md`](../verification/VERIFY-002.md)
- 多模型策略验收：[`../verification/VERIFY-003.md`](../verification/VERIFY-003.md)
- 需求任务记录：[`../tasks/TASK-001.md`](../tasks/TASK-001.md)
- 架构任务记录：[`../tasks/TASK-002.md`](../tasks/TASK-002.md)
- 规划任务记录：[`../tasks/TASK-003.md`](../tasks/TASK-003.md)
- 前端需求与 UI 任务：[`../tasks/TASK-004.md`](../tasks/TASK-004.md)
- 当前任务索引：[`../tasks/INDEX.md`](../tasks/INDEX.md)
- 多模型需求任务：[`../tasks/TASK-013.md`](../tasks/TASK-013.md)
- 多模型架构任务：[`../tasks/TASK-014.md`](../tasks/TASK-014.md)
- 多模型计划任务：[`../tasks/TASK-015.md`](../tasks/TASK-015.md)
- 多模型后端任务：[`../tasks/TASK-016.md`](../tasks/TASK-016.md)
- 多模型集成任务：[`../tasks/TASK-017.md`](../tasks/TASK-017.md)
- 原生知识库规划任务：[`../tasks/TASK-018.md`](../tasks/TASK-018.md)
- 知识基础设施任务：[`../tasks/TASK-019.md`](../tasks/TASK-019.md)
- 文档入库后端任务：[`../tasks/TASK-020.md`](../tasks/TASK-020.md)
- 知识管理前端任务：[`../tasks/TASK-021.md`](../tasks/TASK-021.md)
- 混合检索与 Agent 工具任务：[`../tasks/TASK-022.md`](../tasks/TASK-022.md)
- 会话知识范围与引用任务：[`../tasks/TASK-023.md`](../tasks/TASK-023.md)
- 知识可靠性与安全任务：[`../tasks/TASK-024.md`](../tasks/TASK-024.md)
- 原生知识库集成验收任务：[`../tasks/TASK-025.md`](../tasks/TASK-025.md)
- Wiki 后续设计任务（proposed）：[`../tasks/TASK-026.md`](../tasks/TASK-026.md)

## 项目状态

- 需求阶段：已确认；流式响应协议为 SSE。
- 架构：设计规格已获用户复核确认。
- 前端：Vuetify V1 视觉基线与书面规格均已获确认。
- 实施计划：新的全栈计划已完成；旧计划仅用于追溯。
- 实现与验证：前后端实现与 REQ-004 最终复验均已完成；历史失效证据、最终新鲜命令和边界说明见 VERIFY-003。
- 增量改造：TASK-009～TASK-012 全部完成；`create_agent` 短期记忆、前端新契约和桌面／移动全栈验收均通过。
- 多模型策略：REQ-004 已完成。TASK-016 与 TASK-017 均为 `done`，VERIFY-003 为 `PASS`。最终新鲜证据为 12 项聚焦验收、69 个后端测试、Ruff／format／mypy／依赖门，以及固定 Node v22.23.2 下 20 个前端测试、构建和 desktop/mobile E2E；未选中畸形 URL、不可变注册表、未知 provider 与同／异会话并发要求均已显式覆盖。
- 原生知识库开发：REQ-005 与技术设计已确认；TASK-019 与 TASK-020 已完成。TASK-020 的知识管理 API、四格式入库、版本切换、Worker/Redis/API 重启恢复等 25 项真实容器集成均通过；默认后端测试 137 passed、25 skipped，静态质量门通过。TASK-021 为下一开发任务；TASK-022～TASK-025 尚未开始。Wiki 仅登记为 TASK-026 proposed。
- Git：当前目录现为 Git 仓库；原生知识库设计基线为 `83c58e2`，新任务从该修订开始。

## 恢复入口

REQ-003 与 REQ-004 均已完成，证据分别见 VERIFY-002 与 VERIFY-003。若继续多模型工作，从已关闭的 TASK-016／TASK-017 和 VERIFY-003 的范围边界恢复；当前没有遗留实现阻碍。部署时用户仍须在私有 `.env` 显式增加 `AGENT_MODEL_PROVIDER` 并重启；真实付费供应商验证为 `not_run`，生产持久化、工具调用、请求级选模、热切换和记忆治理不在当前范围。

恢复原生知识库开发时从 TASK-021 知识管理工作台开始，再按 TASK-022 → TASK-023 → TASK-024 → TASK-025 顺序推进；TASK-019 的容器与迁移基线、TASK-020 的文档入库后端均已完成，TASK-026 未获执行授权。
