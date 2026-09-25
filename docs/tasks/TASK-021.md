---
id: TASK-021
title: 实现知识库管理工作台
status: done
execution_scope: approved
kind: frontend
priority: normal
owner: 23196
depends_on:
  - TASK-020
requirements:
  - REQ-005
acceptance:
  - REQ-005/AC-002
  - REQ-005/AC-003
  - REQ-005/AC-009
read_refs:
  - docs/requirements/REQ-005.md
  - docs/superpowers/specs/2026-09-20-native-knowledge-rag-design.md
  - docs/design/ui/style.md
  - web/src/App.vue
  - web/src/styles/tokens.css
write_scope:
  - .gitignore
  - web/package.json
  - web/package-lock.json
  - web/src/App.vue
  - web/src/main.ts
  - web/src/views/ChatView.vue
  - web/src/router/
  - web/src/layouts/
  - web/src/views/knowledge/
  - web/src/components/knowledge/
  - web/src/services/knowledgeApi.ts
  - web/src/composables/useKnowledge.ts
  - web/src/types/knowledge.ts
  - web/tests/knowledge/
  - web/tests/App.spec.ts
  - web/tests/e2e/knowledge.spec.ts
  - web/tests/e2e/knowledge.real.spec.ts
  - web/playwright.real.config.ts
  - docs/tasks/TASK-021.md
  - docs/development/README.md
base_revision: 4d95039a5f056caaca9b187c1cb747d92686ef0b
updated_at: 2026-09-22T02:08:56+08:00
---

## 目标

按已确认的左侧工作台布局实现知识库列表、知识库详情、文件上传、实时阶段、失败重试、重建和删除确认，并适配移动端抽屉。

## 完成条件

- 桌面和移动端导航不破坏现有聊天页面。
- 页面使用真实 API 和 SSE，不以 mock 成功替代入库闭环。
- 上传、状态恢复、失败反馈、重试、重建和删除行为可自动验证。
- 当前视觉令牌和无障碍基线保持一致。

## 范围依据

REQ-005 AC-002、AC-003、AC-009；用户已在视觉伴侣中确认方案 B“左侧工作台”。

## 实施记录

- 2026-09-22：按 `zq-flow` 选中本任务，确认 TASK-020 已完成、左侧工作台方案 B 已获用户确认。接入 Vue Router，保留聊天页面并新增桌面侧栏与移动抽屉。
- 实现知识库 CRUD、搜索、启停、显式删除确认，文档多文件上传、重复文件新版本确认、状态/进度 SSE、断线轮询恢复、重试、重建和删除；使用 TASK-020 已实现的 REST/SSE 字段。
- 单人协调下将聊天视图迁移、原聊天测试和开发入口纳入写入范围。`AGENTS.md` 为既有未跟踪文件，未修改。
- 2026-09-22：Docker 恢复后，以本地确定性 OpenAI-compatible Embedding 测试服务配合真实 FastAPI、Celery Worker/Beat、PostgreSQL、Redis 和 Milvus 完成浏览器联调。发现上传前签名拒绝与异步解析失败是两个不同状态；页面为签名错误增加明确中文反馈，真实 E2E 分别覆盖两者。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-002/003 前端契约 | `npm run test -- --run`、`npm run test:e2e`（`AGENT_KNOWLEDGE_ENABLED=false`，Playwright 拦截知识 API） | pass（模拟响应） | 22 个单元测试、桌面/移动 4 个 E2E 通过；覆盖创建、编辑、启停、上传、刷新恢复、失败重试、重建、文档删除和非空库确认删除；不能代替真实入库 |
| AC-009 界面 | 桌面 1440×900、移动 iPhone 13 浏览器操作与截图 | pass（模拟响应） | `web/test-results/knowledge-*/knowledge-*.png`；无横向溢出，原聊天桌面/移动回归通过 |
| 代码质量 | Node 24：`npm run type-check`、`npm run lint`、`npm run build`；`git diff --check` | pass | 类型、Lint、生产构建及差异检查均通过 |
| AC-002/003/009 真实 API | `RUN_KNOWLEDGE_INTEGRATION=1 npm run test:e2e -- --config=playwright.real.config.ts`；Docker 29.8.0 五个容器 healthy，本地 API/Worker/Beat 与确定性 Embedding 测试服务 | pass | 桌面真实 E2E 1 passed（1.5 分钟）：创建库、202 上传到 ready、刷新持久化、重建后活动版本 ID 切换、无效签名即时拒绝、可接收但不可解析 PDF 进入 failed、重试、文档与非空库异步删除；测试后库列表为 0。外部付费 Embedding 供应商未参与 |
| 最终回归 | Node 24：type-check、lint、22 项 Vitest、build；默认 Playwright 桌面/移动 | pass | 4 passed、2 skipped（真实集成测试仅显式启用）；`git diff --check` 通过 |

## 阻碍与解除条件

无当前阻碍。原 Docker 引擎阻碍已解除；本轮真实浏览器验收使用本地确定性 Embedding 服务，不代表外部付费 Embedding 供应商可用。

## 后续事项

无独立建议。

## 交接

TASK-023 可复用本任务创建的工作台布局与路由。TASK-021 的页面与真实 API 联调已完成。

## 变更历史

- 2026-09-20T17:53:12+08:00：创建 approved 前端知识管理任务，status=todo。
- 2026-09-22T01:12:41+08:00：依赖已满足，`todo → in_progress`，开始知识库工作台实现。
- 2026-09-22T01:26:10+08:00：模拟契约、视觉与质量门通过；真实 API 联调受 Docker 引擎阻断，`in_progress → blocked`。
- 2026-09-22T02:01:00+08:00：Docker 与五个基础容器恢复，`blocked → verifying`，启动真实 API/Worker/Beat 页面联调。
- 2026-09-22T02:08:56+08:00：真实 E2E、清理核查及最终前端质量门通过，`verifying → done`。
