---
id: TASK-031
title: 验证 Wiki 全栈业务闭环
status: done
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
updated_at: 2026-10-16T10:00:00+08:00
---

## 目标

以真实数据库、队列、Worker 和浏览器验证 REQ-006 全部 AC、故障恢复、旧 RAG 回归与 Wiki 质量评测。

## 完成条件

- 本任务关联的 AC 在本任务范围内有可复验的实现与测试证据。
- 共享契约与上下游调用保持一致；集成闭环由 TASK-031 最终证明。

## 范围依据

用户于 2026-09-22 回复“按此规格实施”，确认 REQ-006、技术设计与实施计划。属于已确认范围的必要拆解。

## 实施记录

已在独立 PostgreSQL 测试库完成迁移回归、真实页面流、Redis/Celery Worker 与桌面／移动浏览器验收。可读摘要 v2 的模型 stub 覆盖两阶段 token 结算。路线 C 的独立 Wiki 语义索引已完成，固定 18 题检索评测连续三次通过质量门，详见 `docs/verification/VERIFY-005.md`。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-001、AC-002、AC-003、AC-004、AC-005、AC-007、AC-009、AC-010 | 独立 PostgreSQL、Docker Redis、真实 Celery Worker、桌面/移动 Playwright | pass | 2026-10-07 复验，详见 `docs/verification/VERIFY-005.md` |
| AC-006、AC-008 的检索质量门 | `tests/eval/test_wiki_retrieval_quality.py -q -s` | pass | 连续三次通过；Page Recall@5 最低 0.9278，Claim Recall@8 0.9130，Citation Precision 最低 0.9877，同义一致性最低 0.9074 |

## 阻碍与解除条件

无任务内阻碍。路线 C 的独立 Wiki 向量索引和固定集评测已通过；真实付费模型生成质量与供应商 token 对账仍需另行授权，不影响本任务对确定性检索链路的闭环结论。

## 后续事项

真实付费模型摘要质量、供应商 token 对账和 `docling` 依赖安全升级分别作为后续任务处理，不混入本检索闭环。

## 交接

后续变更必须继续运行 `wiki-quality light`；涉及检索、索引或排序时运行完整质量门，并保留结构化评测报告。

## 变更历史

- 2026-09-22T19:25:25+08:00：根据用户批准的 Wiki 规格建立 todo 任务。
- 2026-10-07T16:33:10+08:00：完成独立数据库、队列 Worker 和浏览器复验；固定检索集失败，保留 in_progress 并记录路线 C 触发条件。
- 2026-10-16T10:00:00+08:00：路线 C 完成；修复随机 UUID 同分排序、跨页面 claim 预算独占和候选池过早截断，完整质量门连续三次通过，任务关闭。
