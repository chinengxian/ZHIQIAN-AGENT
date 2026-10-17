---
id: TASK-034
title: 实现前端文档详情与 Wiki 来源跳转
status: done
execution_scope: approved
kind: frontend
priority: normal
owner: /root
depends_on:
  - TASK-033
requirements:
  - REQ-007
acceptance:
  - REQ-007/AC-001
  - REQ-007/AC-002
  - REQ-007/AC-003
  - REQ-007/AC-004
  - REQ-007/AC-006
read_refs:
  - docs/requirements/REQ-007.md
  - docs/superpowers/specs/2026-10-05-document-inspection-design.md
  - docs/superpowers/plans/2026-10-05-document-inspection.md
  - web/src/views/knowledge/KnowledgeView.vue
  - web/src/views/knowledge/WikiPanel.vue
  - web/src/router/index.ts
  - web/src/services/knowledgeApi.ts
  - web/src/types/knowledge.ts
write_scope:
  - web/src/router/index.ts
  - web/src/views/knowledge/
  - web/src/components/knowledge/
  - web/src/services/knowledgeApi.ts
  - web/src/types/knowledge.ts
  - web/tests/knowledge/
base_revision: 9ed1938358f7901c30da5d7b88b6de2e8ec150db
updated_at: 2026-10-05T23:58:00+08:00
---

## 目标

在现有知识库工作台增加可深链恢复的文档详情和正文预览，并让 Wiki 来源进入对应版本与 chunk 上下文。

## 完成条件

- 文档列表有清晰、可键盘操作的详情入口，URL 保存文档、版本和锚点。
- 页面展示元数据、版本、正文分页及处理中/失败/删除/正文不可用状态。
- Wiki 来源跳转到同一详情页，定位有效锚点；失效来源不切换到其他版本。
- 正文只通过文本插值渲染，桌面和移动布局无横向溢出。

## 范围依据

REQ-007 已由用户明确指令确认；本任务依赖 TASK-033 冻结的 API 契约。

## 实施记录

- 2026-10-05T23:39:00+08:00：TASK-033 已完成并固定后端契约，按 zq-flow 进入前端实现；沿用现有知识库工作台视觉基线，不触发新 UI 样板确认。
- 2026-10-05T23:58:00+08:00：完成文档详情路由、文档列表详情入口、版本/正文预览接入、Wiki 来源深链跳转和移动端详情布局；正文通过 Vue 文本插值渲染。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-001～AC-004、AC-006 | `npm test -- knowledgeApi.spec.ts --run` | pass | RED：`knowledgeApi.getDocument is not a function`；GREEN：3 passed，覆盖详情、版本与预览 API 参数编码 |
| AC-001～AC-004、AC-006 | `npm run type-check`、`npm run lint`、`npm run build` | pass | Vue 类型检查、ESLint 和生产构建均通过 |
| AC-001、AC-003、AC-004、AC-006 | Playwright + 本地 Vite + 拦截 API 冒烟 | pass | 桌面和 390px 移动详情页无横向溢出；正文 `<script>` 按文本显示；Wiki 来源跳转到 `/knowledge/kb1/documents/doc1?version=ver1&chunk=child1` |

## 阻碍与解除条件

无。TASK-033 已完成并提供已验证的后端契约。

## 后续事项

已与 TASK-033 一起解锁 TASK-035。

## 交接

沿用现有知识库工作台视觉基线；新增组件为 `web/src/components/knowledge/DocumentDetailPanel.vue`。未引入新 UI 框架、原文件渲染器或文档编辑器。

## 变更历史

- 2026-10-05T23:05:56+08:00：由 TASK-032 创建，status=todo。
- 2026-10-05T23:39:00+08:00：前端实现启动，status=in_progress，owner=/root。
- 2026-10-05T23:58:00+08:00：前端实现与聚焦验证完成，status=done。

