---
id: TASK-031
title: 验证 Wiki 全栈业务闭环
status: in_progress
execution_scope: approved
kind: integration
priority: normal
owner: /root
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
updated_at: 2026-10-07T16:33:10+08:00
---

## 目标

以真实数据库、队列、Worker 和浏览器验证 REQ-006 全部 AC、故障恢复、旧 RAG 回归与 Wiki 质量评测。

## 完成条件

- 本任务关联的 AC 在本任务范围内有可复验的实现与测试证据。
- 共享契约与上下游调用保持一致；集成闭环由 TASK-031 最终证明。

## 范围依据

用户于 2026-09-22 回复“按此规格实施”，确认 REQ-006、技术设计与实施计划。属于已确认范围的必要拆解。

## 实施记录

已在独立 PostgreSQL 测试库完成迁移回归、真实页面流、Redis/Celery Worker 与桌面／移动浏览器验收。可读摘要 v2 的模型 stub 覆盖两阶段 token 结算。固定 16 题检索评测已实际运行但未达质量门，详见 `docs/verification/VERIFY-005.md`。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-001、AC-002、AC-003、AC-004、AC-005、AC-007、AC-009、AC-010 | 独立 PostgreSQL、Docker Redis、真实 Celery Worker、桌面/移动 Playwright | pass | 2026-10-07 复验，详见 `docs/verification/VERIFY-005.md` |
| AC-006、AC-008 的检索质量门 | `tests/eval/test_wiki_retrieval_quality.py -q -s` | fail | Page Recall@5=0.3537，低于 0.90；路线 C 已触发 |

## 阻碍与解除条件

固定检索质量未达已确认门槛，不能将 Wiki 检索称为可用闭环。解除条件：实施并通过路线 C 的独立 Wiki 向量索引和固定集评测；真实付费模型质量仍需另行授权。

## 后续事项

用户已将路线 C 定为下一步目标；进入实现前应按独立向量 schema、collection 生命周期、原子可见性和评测门槛完成设计与计划。

## 交接

按实施计划和依赖顺序执行；不要把未运行的集成验收视为通过。

## 变更历史

- 2026-09-22T19:25:25+08:00：根据用户批准的 Wiki 规格建立 todo 任务。
- 2026-10-07T16:33:10+08:00：完成独立数据库、队列 Worker 和浏览器复验；固定检索集失败，保留 in_progress 并记录路线 C 触发条件。
