---
id: TASK-023
title: 实现会话知识范围与回答引用交互
status: todo
execution_scope: approved
kind: frontend
priority: normal
owner: unassigned
depends_on:
  - TASK-021
  - TASK-022
requirements:
  - REQ-005
acceptance:
  - REQ-005/AC-008
  - REQ-005/AC-010
read_refs:
  - docs/requirements/REQ-005.md
  - docs/superpowers/specs/2026-09-20-native-knowledge-rag-design.md
  - web/src/services/chatApi.ts
  - web/src/services/sseParser.ts
  - web/src/composables/useChat.ts
  - web/src/components/chat/
write_scope:
  - web/src/types/chat.ts
  - web/src/services/chatApi.ts
  - web/src/services/sseParser.ts
  - web/src/composables/useChat.ts
  - web/src/components/chat/ChatHeader.vue
  - web/src/components/chat/ChatMessage.vue
  - web/src/components/chat/KnowledgeScopeSelector.vue
  - web/src/components/chat/SourceDrawer.vue
  - web/tests/services/
  - web/tests/composables/
  - web/tests/ChatMessage.spec.ts
  - web/tests/e2e/chat-knowledge.spec.ts
  - docs/tasks/TASK-023.md
base_revision: 83c58e29e87019acc449de54d6ef368726a85668
updated_at: 2026-09-20T17:53:12+08:00
---

## 目标

在现有流式聊天中加入会话级知识范围、检索状态、引用标记和来源抽屉，同时保留停止生成、错误处理和短期记忆体验。

## 完成条件

- 默认全部启用知识库，并允许按当前会话选择多个知识库。
- 前端解析 status/sources 且兼容现有 SSE 事件。
- PDF 来源显示页码，其他格式显示标题路径；不展示磁盘路径。
- 桌面和移动 E2E 覆盖知识范围、命中、无命中和中途停止。

## 范围依据

REQ-005 AC-008、AC-010；工作台布局已由 TASK-021 提供，后端契约由 TASK-022 提供。

## 实施记录

尚未开始。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-008 | 待执行请求与 SSE 状态测试 | not_run | 实现后记录 |
| AC-010 | 待执行引用组件与 E2E | not_run | 实现后记录 |

## 阻碍与解除条件

依赖 TASK-021 与 TASK-022。后端 `sources` 未冻结前不得自行定义平行字段。

## 后续事项

无独立建议。

## 交接

完成后 TASK-025 以真实 API 和确定性模型执行完整上传到引用 E2E。

## 变更历史

- 2026-09-20T17:53:12+08:00：创建 approved 前端对话集成任务，status=todo。
