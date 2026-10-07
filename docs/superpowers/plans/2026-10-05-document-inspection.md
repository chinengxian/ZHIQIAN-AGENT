# 文档详情与正文预览实施计划

- 状态：approved
- 需求：`docs/requirements/REQ-007.md`
- 技术设计：`docs/superpowers/specs/2026-10-05-document-inspection-design.md`
- 执行方式：当前代理顺序协调；用户未要求并行，不创建子代理。API 响应契约由后端任务先固定，前端随后接入，最后单列真实集成验收。

## 工作包与依赖

| 顺序 | 工作包 | 主要文件归属 | 验收 | 依赖 |
| --- | --- | --- | --- | --- |
| 1 | 后端详情、版本与正文预览 API：响应模型、归属校验、分页、parent/child 锚点、脱敏和测试 | `src/agent_api/knowledge/api/routes.py`、`src/agent_api/knowledge/application/management.py`、`tests/test_knowledge_api.py`、`tests/test_knowledge_management_integration.py` | AC-001、AC-002、AC-004、AC-005 | 无迁移；复用现有模型 |
| 2 | 前端文档详情工作台：深链、详情状态、版本选择、正文分页、锚点高亮和 Wiki 来源跳转 | `web/src/router/index.ts`、`web/src/views/knowledge/`、`web/src/components/knowledge/`、`web/src/services/knowledgeApi.ts`、`web/src/types/knowledge.ts`、`web/tests/` | AC-001～AC-004、AC-006 | 工作包 1 |
| 3 | 全栈集成验收：真实 PostgreSQL 数据、API、桌面/移动浏览器及既有知识/Wiki 回归 | `tests/integration/`、`web/tests/e2e/document-inspection.spec.ts`、`docs/verification/VERIFY-006.md` | AC-001～AC-006 | 工作包 1、2 |

## 共享契约与文件边界

- 后端工作包负责冻结 Pydantic 响应、游标、错误码和归属规则；不修改前端页面。
- 前端工作包按已冻结契约更新 TypeScript 类型和 API 客户端；不修改数据库或后端响应语义。
- 集成工作包只补真实闭环用例和验收文档；发现必需缺陷时回到对应实现任务，不通过降低 AC 关闭任务。
- 本轮不需要 Alembic 迁移、依赖或锁文件修改；若实现发现必须改 schema，先更新技术设计和任务范围再执行。

## 验收门

- 后端：聚焦 API/服务测试、Ruff、format check、mypy 通过；响应中不存在 `file_path`、`sha256`、Embedding 配置和内部异常。
- 前端：Vitest、lint、type-check、build 通过；正文只按文本渲染，URL 可恢复版本和锚点。
- 集成：桌面/移动均完成“列表 → 详情 → 翻页 → 刷新恢复 → Wiki 来源定位”，并覆盖失效来源与跨文档拒绝。
- 回归：现有 `knowledge.spec.ts` 与 `wiki.spec.ts` 保持通过。

## 风险与处理

- **大文档响应过大**：使用稳定游标、条数与字符双上限，禁止整篇加载。
- **Wiki 锚点是 child、页面展示 parent**：服务端负责解析父子关系并返回 focus 范围，前端不猜数据库结构。
- **旧来源正文已清理**：保留来源元数据和不可用原因，不切换到活动版本冒充原证据。
- **快速切换产生竞态**：前端以路由标识和取消信号丢弃旧响应。

## 第一项可执行工作

执行 `TASK-033`：实现后端文档详情、版本和正文预览契约。完成并验证后再进入 `TASK-034`；`TASK-035` 只在两个实现任务都完成后开始。

