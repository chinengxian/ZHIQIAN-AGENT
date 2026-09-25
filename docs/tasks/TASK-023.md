---
id: TASK-023
title: 实现会话知识范围与回答引用交互
status: done
execution_scope: approved
kind: frontend
priority: normal
owner: 23196
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
  - web/src/views/ChatView.vue
  - tests/e2e_app.py
  - src/agent_api/api/chat.py
  - tests/test_chat_knowledge_stream.py
  - web/tests/services/
  - web/tests/composables/
  - web/tests/ChatMessage.spec.ts
  - web/tests/e2e/chat-knowledge.spec.ts
  - docs/tasks/TASK-023.md
base_revision: 83c58e29e87019acc449de54d6ef368726a85668
updated_at: 2026-09-22T03:45:00+08:00
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

- 2026-09-22：`zq-flow` 在 TASK-022 完成后选中本任务，后端 `knowledge_scope`、`status`、`sources` 契约已冻结。
- 2026-09-22：会话页头支持全部或多选启用知识库，流式响应接收检索状态和来源，回答中的注册引用与来源抽屉联动；PDF 显示页码，其余格式显示标题路径。浏览器假模型固定关闭知识功能，后端 `all_enabled` 在知识服务未配置时保留普通聊天。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-008 | `npm test -- --run`、`tests/test_chat_knowledge_stream.py` | pass | 27 项前端单测，知识关闭时全部范围退回旧聊天、指定范围仍返回 503；后端全套 144 通过、26 跳过 |
| AC-010 | `npx playwright test`（Codex Node 22 运行时） | pass | 桌面/移动 8 通过、2 个真实联调专用用例默认跳过；范围选择、命中引用、无命中、停止生成及旧会话回归；抽屉截图已人工检查 |
| 静态与构建 | `npm run lint`、`npm run build`、Ruff、mypy、`git diff --check` | pass | 无错误；未使用真实付费模型 |

## 阻碍与解除条件

无。TASK-025 将以真实 API、Worker 和确定性模型验证上传到回答引用的完整链路；本任务的浏览器知识回答使用契约化模拟 SSE。

## 后续事项

无独立建议。

## 交接

完成后 TASK-025 以真实 API 和确定性模型执行完整上传到引用 E2E。

## 变更历史

- 2026-09-20T17:53:12+08:00：创建 approved 前端对话集成任务，status=todo。
- 2026-09-22：依赖已满足，`todo → in_progress`。
- 2026-09-22：契约、组件、桌面/移动 E2E 及完整回归通过，`in_progress → done`。
