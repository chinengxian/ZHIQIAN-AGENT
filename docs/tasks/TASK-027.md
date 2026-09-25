---
id: TASK-027
title: 建立 Wiki 数据、版本与 API 契约
status: done
execution_scope: approved
kind: backend
priority: normal
owner: 23196
depends_on:
  - TASK-026
requirements:
  - REQ-006
acceptance:
  - REQ-006/AC-001
  - REQ-006/AC-003
  - REQ-006/AC-004
read_refs:
  - docs/requirements/REQ-006.md
  - docs/superpowers/specs/2026-09-22-wiki-synthesis-design.md
  - docs/superpowers/plans/2026-09-22-wiki-synthesis.md
write_scope:
  - migrations/
  - src/agent_api/knowledge/wiki/
  - src/agent_api/knowledge/infrastructure/database/models.py
  - src/agent_api/knowledge/api/
  - web/src/types/wiki.ts
  - tests/test_wiki_*.py
base_revision: 4d95039a5f056caaca9b187c1cb747d92686ef0b
updated_at: 2026-09-22T19:25:25+08:00
---

## 目标

完成 Wiki 归属、页面版本、claim 来源、任务与额度的数据模型，以及基础配置和页面读写 API。

## 完成条件

- 本任务关联的 AC 在本任务范围内有可复验的实现与测试证据。
- 共享契约与上下游调用保持一致；集成闭环由 TASK-031 最终证明。

## 范围依据

用户于 2026-09-22 回复“按此规格实施”，确认 REQ-006、技术设计与实施计划。属于已确认范围的必要拆解。

## 实施记录

已建立 Wiki 配置、页面版本、claim 来源、任务与 token 记录表；新增迁移、服务及 API。独立数据库完成迁移与集成读写测试。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-001、AC-003、AC-004 | `tests/integration/test_wiki_flow.py`，独立 PostgreSQL；`tests/test_knowledge_models.py` | pass | 页面归属、来源定位、编辑／回滚／审核及迁移版本 |

## 阻碍与解除条件

无。

## 后续事项

无。

## 交接

按实施计划和依赖顺序执行；不要把未运行的集成验收视为通过。

## 变更历史

- 2026-09-22T19:25:25+08:00：根据用户批准的 Wiki 规格建立 in_progress 任务。
