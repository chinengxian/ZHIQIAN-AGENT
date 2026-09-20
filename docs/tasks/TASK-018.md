---
id: TASK-018
title: 规划原生知识库与多级 RAG 实施任务
status: done
execution_scope: approved
kind: planning
priority: normal
owner: /root
depends_on: []
requirements:
  - REQ-005
acceptance:
  - 将已确认技术设计转化为有依赖、AC、read_refs 和 write_scope 的正式任务
  - 优先形成知识库管理纵向闭环，再接 Agent 检索与引用
  - 单列最终集成验收，不自动启动开发
read_refs:
  - docs/superpowers/specs/2026-09-20-native-knowledge-rag-design.md
  - docs/tasks/INDEX.md
  - docs/development/README.md
  - src/agent_api/
  - web/src/
write_scope:
  - docs/requirements/REQ-005.md
  - docs/tasks/TASK-018.md
  - docs/tasks/TASK-019.md
  - docs/tasks/TASK-020.md
  - docs/tasks/TASK-021.md
  - docs/tasks/TASK-022.md
  - docs/tasks/TASK-023.md
  - docs/tasks/TASK-024.md
  - docs/tasks/TASK-025.md
  - docs/tasks/TASK-026.md
  - docs/tasks/INDEX.md
  - docs/development/README.md
base_revision: 83c58e29e87019acc449de54d6ef368726a85668
updated_at: 2026-09-20T17:53:12+08:00
---

## 目标

把已批准的原生知识库技术设计整理为可顺序执行、可独立验收和可从文档恢复的任务图。

## 完成条件

- REQ-005 包含可观察 AC，并与技术设计一致。
- 实现任务覆盖基础设施、入库、知识 UI、检索工具、对话引用、可靠性和最终验收。
- 依赖不存在缺失、循环或 cancelled 节点，共享根配置有明确负责人。
- Wiki 只登记为后续 proposed 设计任务。

## 范围依据

用户于 2026-09-20 明确调用 `zq-plan` 生成任务；此前已逐节确认并提交原生知识库技术设计。

## 实施记录

- 自动扫描现有 TASK-001～TASK-017、REQ-001～REQ-004、开发入口和当前 Git 基线。
- 创建 REQ-005，并建立 TASK-019～TASK-025 的本阶段任务链及 TASK-026 后续 Wiki 设计任务。
- 当前任务仅修改规划文档，未修改 `src/`、`tests/` 或 `web/src/` 业务代码。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| ID 去重 | 扫描 `docs/tasks/TASK-*.md` | pass | 原最高 ID 为 TASK-017，新任务从 TASK-018 开始 |
| AC 覆盖 | 核对 TASK-019～TASK-025 与 REQ-005 AC-001～AC-012 | pass | 每条 required AC 至少进入一个实现任务并由 TASK-025 最终验收 |
| 依赖有效 | 解析 depends_on 并检查缺失与循环 | pass | 任务图无缺失和循环；TASK-025 等待全部实现任务 |
| 阶段边界 | 核对本轮 Git diff | pass | 仅需求、任务、索引与开发入口文档发生变化 |

## 阻碍与解除条件

无规划阻碍。开发尚未开始。

## 后续事项

第一项可执行工作为 TASK-019。TASK-026 保持 proposed，不进入本轮实现。

## 交接

默认按 TASK-019 → TASK-020 → TASK-021 → TASK-022 → TASK-023 → TASK-024 → TASK-025 顺序推进。只有用户明确要求并行时才调整分派；共享配置、依赖清单、Compose、迁移入口和锁文件由 TASK-019 统一负责。TASK-026 保持 proposed。

## 变更历史

- 2026-09-20T17:53:12+08:00：创建规划并完成任务拆解，status=done。
