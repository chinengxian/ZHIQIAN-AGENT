---
id: TASK-029
title: 实现 Wiki 浏览、编辑与审核工作台
status: done
execution_scope: approved
kind: frontend
priority: normal
owner: unassigned
depends_on:
  - TASK-028
requirements:
  - REQ-006
acceptance:
  - REQ-006/AC-001
  - REQ-006/AC-003
  - REQ-006/AC-004
  - REQ-006/AC-007
  - REQ-006/AC-008
  - REQ-006/AC-009
  - REQ-006/AC-010
read_refs:
  - docs/requirements/REQ-006.md
  - docs/superpowers/specs/2026-09-22-wiki-synthesis-design.md
  - docs/superpowers/plans/2026-09-22-wiki-synthesis.md
write_scope:
  - web/src/views/knowledge/
  - web/src/components/knowledge/
  - web/src/services/
  - web/src/types/wiki.ts
  - web/tests/
base_revision: 4d95039a5f056caaca9b187c1cb747d92686ef0b
updated_at: 2026-09-22T19:25:25+08:00
---

## 目标

在现有知识库页面加入 Wiki 浏览、来源、版本差异、人工编辑、待审核更新、回滚和用量状态。

## 完成条件

- 本任务关联的 AC 在本任务范围内有可复验的实现与测试证据。
- 共享契约与上下游调用保持一致；集成闭环由 TASK-031 最终证明。

## 范围依据

用户于 2026-09-22 回复“按此规格实施”，确认 REQ-006、技术设计与实施计划。属于已确认范围的必要拆解。

## 实施记录

知识库详情加入 Wiki 标签页，含开关、用量、任务、页面、来源、编辑、版本对比、回滚与审核。桌面和移动浏览器测试通过。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| 浏览、来源、审核、停用后浏览 | `web/tests/e2e/wiki.spec.ts`，桌面与移动 | pass | 2 passed，截图由 Playwright 生成 |

## 阻碍与解除条件

无。

## 后续事项

无。

## 交接

按实施计划和依赖顺序执行；不要把未运行的集成验收视为通过。

## 变更历史

- 2026-09-22T19:25:25+08:00：根据用户批准的 Wiki 规格建立 todo 任务。
