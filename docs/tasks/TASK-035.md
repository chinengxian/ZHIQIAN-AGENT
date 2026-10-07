---
id: TASK-035
title: 验证文档查看与 Wiki 来源定位全栈闭环
status: done
execution_scope: approved
kind: integration
priority: normal
owner: /root
depends_on:
  - TASK-033
  - TASK-034
requirements:
  - REQ-007
acceptance:
  - REQ-007/AC-001
  - REQ-007/AC-002
  - REQ-007/AC-003
  - REQ-007/AC-004
  - REQ-007/AC-005
  - REQ-007/AC-006
read_refs:
  - docs/requirements/REQ-007.md
  - docs/superpowers/specs/2026-10-05-document-inspection-design.md
  - docs/superpowers/plans/2026-10-05-document-inspection.md
  - docs/tasks/TASK-033.md
  - docs/tasks/TASK-034.md
write_scope:
  - tests/integration/test_document_inspection.py
  - web/tests/e2e/document-inspection.spec.ts
  - docs/verification/VERIFY-006.md
  - docs/tasks/TASK-033.md
  - docs/tasks/TASK-034.md
  - docs/tasks/TASK-035.md
  - docs/development/README.md
base_revision: 9ed1938358f7901c30da5d7b88b6de2e8ec150db
updated_at: 2026-10-05T23:59:00+08:00
---

## 目标

用真实 PostgreSQL 数据、API 和桌面/移动浏览器验证文档详情、正文分页及 Wiki 来源定位闭环。

## 完成条件

- REQ-007 每条必需 AC 均有当前、可复验的证据。
- 有效和失效 Wiki 来源、跨文档拒绝、分页稳定性和字段脱敏均被覆盖。
- 现有知识库与 Wiki 浏览 E2E 保持通过。
- 验收结果写入 VERIFY-006，并按真实结果关闭或退回实现任务。

## 范围依据

REQ-007 已由用户明确指令确认；集成验收独立于仍在进行的 Wiki 付费模型质量验证。

## 实施记录

- 2026-10-05T23:59:00+08:00：完成真实 PostgreSQL 服务层验收、真实 API + 前端浏览器闭环和回归命令；验收结果写入 VERIFY-006。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-001～AC-006 | [VERIFY-006](../verification/VERIFY-006.md) | pass | 后端聚焦测试、真实 PostgreSQL 集成、前端 Vitest/type-check/lint/build、真实 API 浏览器冒烟均通过 |
| AC-001、AC-003、AC-004、AC-006 | Playwright + 本地 Chrome + Vite + `http://127.0.0.1:8000` 真实 API | pass | Wiki 来源点击进入文档详情深链；桌面和移动无横向溢出；正文 `<script>` 按文本显示 |

## 阻碍与解除条件

无。

## 后续事项

无；验收发现的本范围缺陷必须回到对应实现任务修复。

## 交接

REQ-007 范围已完成。真实浏览器验收使用临时 PostgreSQL 数据并已清理；后续若接入更多文件格式原生阅读器，应另建需求，不属于本任务。

## 变更历史

- 2026-10-05T23:05:56+08:00：由 TASK-032 创建，status=todo。
- 2026-10-05T23:59:00+08:00：完成全栈验收并记录 VERIFY-006，status=done，owner=/root。

