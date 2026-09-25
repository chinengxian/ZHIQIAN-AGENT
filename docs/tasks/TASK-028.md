---
id: TASK-028
title: 实现 Wiki 异步生成、额度和来源失效
status: done
execution_scope: approved
kind: backend
priority: normal
owner: unassigned
depends_on:
  - TASK-027
requirements:
  - REQ-006
acceptance:
  - REQ-006/AC-002
  - REQ-006/AC-005
  - REQ-006/AC-008
  - REQ-006/AC-009
  - REQ-006/AC-010
read_refs:
  - docs/requirements/REQ-006.md
  - docs/superpowers/specs/2026-09-22-wiki-synthesis-design.md
  - docs/superpowers/plans/2026-09-22-wiki-synthesis.md
write_scope:
  - src/agent_api/knowledge/wiki/
  - src/agent_api/knowledge/application/
  - src/agent_api/knowledge/infrastructure/jobs/
  - src/agent_api/knowledge/worker/
  - tests/test_wiki_*.py
  - tests/integration/
base_revision: 4d95039a5f056caaca9b187c1cb747d92686ef0b
updated_at: 2026-09-22T19:25:25+08:00
---

## 目标

让已开启知识库的活动文档版本生成摘要、主题和目录，并处理任务恢复、累计 token 上限及引用失效。

## 完成条件

- 本任务关联的 AC 在本任务范围内有可复验的实现与测试证据。
- 共享契约与上下游调用保持一致；集成闭环由 TASK-031 最终证明。

## 范围依据

用户于 2026-09-22 回复“按此规格实施”，确认 REQ-006、技术设计与实施计划。属于已确认范围的必要拆解。

## 实施记录

已接入文档活动版本切换与删除触发、outbox Wiki 事件、批次生成、额度暂停恢复和过时来源校验。真实 Redis/Celery Worker 消费在独立测试库通过；付费模型质量与账单对照归入 TASK-031 的外部验证边界。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-005、AC-008、AC-009、AC-010 | `tests/integration/test_wiki_flow.py`，独立 PostgreSQL | pass | 双文档主题、长文档尾部、来源失效、额度暂停续跑、停用浏览 |
| AC-002 真实队列 | `tests/integration/test_wiki_celery.py` | pass | 投递失败退避、Worker 离线入队，随后启动消费，模型 stub 用量入账；Redis 服务中断未单独操作 |

## 阻碍与解除条件

无。

## 后续事项

无。

## 交接

按实施计划和依赖顺序执行；不要把未运行的集成验收视为通过。

## 变更历史

- 2026-09-22T19:25:25+08:00：根据用户批准的 Wiki 规格建立 todo 任务。
