---
id: TASK-026
title: 设计 Wiki 衍生知识阶段
status: done
execution_scope: approved
kind: architecture
priority: normal
owner: 23196
depends_on:
  - TASK-025
requirements:
  - REQ-006
acceptance:
  - 定义 source knowledge base 到 Wiki synthesis 的关系和增量更新规则
  - 定义 Wiki 页面、版本、来源链接、编辑、diff、回滚和引用失效契约
  - 比较纯 Wiki 检索与可选 GraphRAG 的收益、成本和启用门槛
  - 先形成独立需求、设计和实施计划，不直接实现 Wiki
read_refs:
  - docs/superpowers/specs/2026-09-20-native-knowledge-rag-design.md
  - docs/verification/VERIFY-004.md
  - docs/requirements/REQ-005.md
  - docs/requirements/REQ-006.md
  - docs/superpowers/specs/2026-09-22-wiki-synthesis-design.md
  - docs/superpowers/plans/2026-09-22-wiki-synthesis.md
write_scope:
  - docs/requirements/REQ-006.md
  - docs/decisions/
  - docs/superpowers/specs/
  - docs/superpowers/plans/2026-09-22-wiki-synthesis.md
  - docs/tasks/TASK-026.md
  - docs/tasks/INDEX.md
  - docs/development/README.md
base_revision: 4d95039a5f056caaca9b187c1cb747d92686ef0b
updated_at: 2026-09-22T19:25:25+08:00
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

已根据用户请求完成 WeKnora 对照、REQ-006 需求草案、Wiki 技术设计与实施计划草案。用户随后要求“下一步”，继续本任务的需求收敛和架构设计；Wiki 代码实施仍未开始。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| 需求发现 | 查阅 WeKnora 当前 Wiki 文档及本仓库知识模型，记录用户选择 | pass | `docs/requirements/REQ-006.md` 修订 2；仅限需求草案 |
| Wiki 数据与生成架构 | 检查现有模型、Worker、outbox、删除与 chunk 重写路径 | pass | `docs/superpowers/specs/2026-09-22-wiki-synthesis-design.md`；含来源、并发、用量和恢复契约 |
| 页面版本与引用失效 | 对照 REQ-006 的人工编辑、回滚、来源删除规则 | pass | 设计稿“数据与权限”“接口契约”；REQ-006 修订 7 |
| Wiki 与 GraphRAG 比较 | 对照现有 RAG 与 WeKnora 图谱参考 | pass | 设计稿“选择与影响”；GraphRAG 仅在独立收益证据成立后考虑 |
| 独立实施计划 | 分解依赖、文件归属与集成验收 | pass | `docs/superpowers/plans/2026-09-22-wiki-synthesis.md`；未创建实现任务 |
| 独立规格确认 | 提供完整需求、设计、计划供用户审阅 | pass | 用户于 2026-09-22 回复“按此规格实施”；REQ-006 修订 8；已建立 TASK-027～031 |

## 阻碍与解除条件

无；独立规格已确认，Wiki 实现任务已获授权。

## 后续事项

无。

## 交接

从 REQ-006 修订 8、已确认技术设计与实施计划恢复；TASK-027 是第一项实现任务，后续依赖见 TASK-028～031。

## 变更历史

- 2026-09-20T17:53:12+08:00：登记 proposed 后续设计任务，status=todo。
- 2026-09-22T18:51:04+08:00：完成需求发现，记录 REQ-006 草案；架构设计仍为 proposed。
- 2026-09-22T18:56:41+08:00：用户要求继续“下一步”，设计任务转 approved/in_progress；不包含 Wiki 代码实施。
- 2026-09-22T19:00:38+08:00：完成需求修订 7、设计及计划草案，进入 verifying，等待规格审阅。
- 2026-09-22T19:25:25+08:00：用户确认“按此规格实施”；需求、设计、计划成为批准基线，任务完成并拆出 TASK-027～031。
