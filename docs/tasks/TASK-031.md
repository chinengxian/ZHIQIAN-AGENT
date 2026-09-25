---
id: TASK-031
title: 验证 Wiki 全栈业务闭环
status: in_progress
execution_scope: approved
kind: integration
priority: normal
owner: unassigned
depends_on:
  - TASK-029
  - TASK-030
requirements:
  - REQ-006
acceptance:
  - REQ-006/AC-001
  - REQ-006/AC-002
  - REQ-006/AC-003
  - REQ-006/AC-004
  - REQ-006/AC-005
  - REQ-006/AC-006
  - REQ-006/AC-007
  - REQ-006/AC-008
  - REQ-006/AC-009
  - REQ-006/AC-010
read_refs:
  - docs/requirements/REQ-006.md
  - docs/superpowers/specs/2026-09-22-wiki-synthesis-design.md
  - docs/superpowers/plans/2026-09-22-wiki-synthesis.md
write_scope:
  - tests/integration/
  - tests/eval/
  - web/tests/e2e/
  - docs/verification/VERIFY-005.md
  - docs/tasks/
  - docs/development/README.md
base_revision: 4d95039a5f056caaca9b187c1cb747d92686ef0b
updated_at: 2026-09-22T19:25:25+08:00
---

## 目标

以真实数据库、队列、Worker 和浏览器验证 REQ-006 全部 AC、故障恢复、旧 RAG 回归与 Wiki 质量评测。

## 完成条件

- 本任务关联的 AC 在本任务范围内有可复验的实现与测试证据。
- 共享契约与上下游调用保持一致；集成闭环由 TASK-031 最终证明。

## 范围依据

用户于 2026-09-22 回复“按此规格实施”，确认 REQ-006、技术设计与实施计划。属于已确认范围的必要拆解。

## 实施记录

已执行数据库集成、旧 RAG 回归、前端构建及桌面／移动浏览器验收。真实队列故障恢复与真实模型质量仍待运行；详见 `docs/verification/VERIFY-005.md`。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| 已覆盖条件 | `docs/verification/VERIFY-005.md` | partial | 明列通过证据和未运行项 |

## 阻碍与解除条件

无。

## 后续事项

无。

## 交接

按实施计划和依赖顺序执行；不要把未运行的集成验收视为通过。

## 变更历史

- 2026-09-22T19:25:25+08:00：根据用户批准的 Wiki 规格建立 todo 任务。
