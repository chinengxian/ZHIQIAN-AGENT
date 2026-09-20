---
id: TASK-021
title: 实现知识库管理工作台
status: todo
execution_scope: approved
kind: frontend
priority: normal
owner: unassigned
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
  - web/package.json
  - web/package-lock.json
  - web/src/App.vue
  - web/src/main.ts
  - web/src/router/
  - web/src/layouts/
  - web/src/views/knowledge/
  - web/src/components/knowledge/
  - web/src/services/knowledgeApi.ts
  - web/src/composables/useKnowledge.ts
  - web/src/types/knowledge.ts
  - web/tests/knowledge/
  - web/tests/e2e/knowledge.spec.ts
  - docs/tasks/TASK-021.md
base_revision: 83c58e29e87019acc449de54d6ef368726a85668
updated_at: 2026-09-20T17:53:12+08:00
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

尚未开始。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-002/003 | 待执行真实 API 页面测试 | not_run | 实现后记录 |
| AC-009 | 待执行组件、桌面和移动 E2E | not_run | 实现后记录 |

## 阻碍与解除条件

依赖 TASK-020 的正式 REST/SSE 契约。若契约变更，先更新 REQ-005/设计和后端测试，不在前端静默猜测字段。

## 后续事项

无独立建议。

## 交接

TASK-023 复用本任务创建的工作台布局与路由，只修改聊天知识范围和引用相关文件，避免重复改造导航。

## 变更历史

- 2026-09-20T17:53:12+08:00：创建 approved 前端知识管理任务，status=todo。
