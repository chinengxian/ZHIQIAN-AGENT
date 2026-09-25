---
id: TASK-030
title: 接入 Agent 只读 Wiki 与可信引用过滤
status: done
execution_scope: approved
kind: backend
priority: normal
owner: unassigned
depends_on:
  - TASK-028
requirements:
  - REQ-006
acceptance:
  - REQ-006/AC-005
  - REQ-006/AC-006
  - REQ-006/AC-010
read_refs:
  - docs/requirements/REQ-006.md
  - docs/superpowers/specs/2026-09-22-wiki-synthesis-design.md
  - docs/superpowers/plans/2026-09-22-wiki-synthesis.md
write_scope:
  - src/agent_api/knowledge/wiki/tools/
  - src/agent_api/llm/
  - src/agent_api/core/lifespan.py
  - tests/test_wiki_*.py
base_revision: 4d95039a5f056caaca9b187c1cb747d92686ef0b
updated_at: 2026-09-22T19:25:25+08:00
---

## 目标

为 Agent 提供库内 Wiki 搜索和页面读取，实时过滤待核实或失效 claim，并保持原文检索引用契约。

## 完成条件

- 本任务关联的 AC 在本任务范围内有可复验的实现与测试证据。
- 共享契约与上下游调用保持一致；集成闭环由 TASK-031 最终证明。

## 范围依据

用户于 2026-09-22 回复“按此规格实施”，确认 REQ-006、技术设计与实施计划。属于已确认范围的必要拆解。

## 实施记录

接入 `wiki_search` 与 `wiki_read_page` 只读工具，按当前会话知识库范围筛选，仅注册来源仍有效的 claim 到原有引用流。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-005、AC-010 | `tests/test_knowledge_tools.py` 与独立数据库集成测试 | pass | 越权范围拒绝、失效来源过滤、停用后无 Wiki 输出 |

## 阻碍与解除条件

无。

## 后续事项

无。

## 交接

按实施计划和依赖顺序执行；不要把未运行的集成验收视为通过。

## 变更历史

- 2026-09-22T19:25:25+08:00：根据用户批准的 Wiki 规格建立 todo 任务。
