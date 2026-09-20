---
id: TASK-026
title: 设计 Wiki 衍生知识阶段
status: todo
execution_scope: proposed
kind: architecture
priority: normal
owner: unassigned
depends_on:
  - TASK-025
requirements: []
acceptance:
  - 定义 source knowledge base 到 Wiki synthesis 的关系和增量更新规则
  - 定义 Wiki 页面、版本、来源链接、编辑、diff、回滚和引用失效契约
  - 比较纯 Wiki 检索与可选 GraphRAG 的收益、成本和启用门槛
  - 先形成独立需求、设计和实施计划，不直接实现 Wiki
read_refs:
  - docs/superpowers/specs/2026-09-20-native-knowledge-rag-design.md
  - docs/verification/VERIFY-004.md
  - docs/requirements/REQ-005.md
write_scope:
  - docs/requirements/REQ-006.md
  - docs/decisions/
  - docs/superpowers/specs/
  - docs/tasks/TASK-026.md
  - docs/tasks/INDEX.md
  - docs/development/README.md
base_revision: 83c58e29e87019acc449de54d6ef368726a85668
updated_at: 2026-09-20T17:53:12+08:00
---

## 目标

在高级 RAG 通过验收后，为 Wiki 衍生知识层单独形成需求、数据/生成架构、增量更新、编辑版本和检索路由设计。

## 完成条件

- Wiki 不被当作 RAG 的简单开关，并且所有结论可回溯至文档版本和 chunk。
- 人工编辑与自动增量更新冲突、删除来源后的引用失效和版本回滚均有明确规则。
- GraphRAG 只有在独立收益证据成立时才进入后续范围。
- 用户批准独立规格前不创建实现任务。

## 范围依据

用户选择“高级 RAG 与 Wiki 分两阶段”，并要求后续先生成计划、保留文档。当前只登记后续设计工作，不代表已授权 Wiki 实施。

## 实施记录

尚未开始。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| 独立设计门 | 待 TASK-025 完成后执行 | not_run | proposed，不进入当前阶段 |

## 阻碍与解除条件

执行范围尚未批准，且依赖 TASK-025。用户明确批准 Wiki 设计阶段并完成高级 RAG 验收后，才可改为 approved。

## 后续事项

无。

## 交接

当前协调者只保留该任务记录；本轮不得开始 REQ-006 或 Wiki 代码。

## 变更历史

- 2026-09-20T17:53:12+08:00：登记 proposed 后续设计任务，status=todo。
