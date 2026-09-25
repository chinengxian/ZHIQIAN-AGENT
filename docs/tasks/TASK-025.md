---
id: TASK-025
title: 验证原生知识库与多级 RAG 全栈闭环
status: done
execution_scope: approved
kind: integration
priority: normal
owner: 23196
depends_on:
  - TASK-021
  - TASK-023
  - TASK-024
requirements:
  - REQ-005
acceptance:
  - REQ-005/AC-001
  - REQ-005/AC-002
  - REQ-005/AC-003
  - REQ-005/AC-004
  - REQ-005/AC-005
  - REQ-005/AC-006
  - REQ-005/AC-007
  - REQ-005/AC-008
  - REQ-005/AC-009
  - REQ-005/AC-010
  - REQ-005/AC-011
  - REQ-005/AC-012
read_refs:
  - docs/requirements/REQ-005.md
  - docs/superpowers/specs/2026-09-20-native-knowledge-rag-design.md
  - docs/tasks/TASK-019.md
  - docs/tasks/TASK-020.md
  - docs/tasks/TASK-021.md
  - docs/tasks/TASK-022.md
  - docs/tasks/TASK-023.md
  - docs/tasks/TASK-024.md
write_scope:
  - tests/integration/
  - tests/eval/
  - web/tests/e2e/
  - docs/verification/VERIFY-004.md
  - docs/tasks/TASK-019.md
  - docs/tasks/TASK-020.md
  - docs/tasks/TASK-021.md
  - docs/tasks/TASK-022.md
  - docs/tasks/TASK-023.md
  - docs/tasks/TASK-024.md
  - docs/tasks/TASK-025.md
  - docs/development/README.md
base_revision: 83c58e29e87019acc449de54d6ef368726a85668
updated_at: 2026-09-22T14:47:00+08:00
---

## 目标

以真实 PostgreSQL、Redis、Milvus、四类文档 fixture、确定性模型和桌面/移动浏览器证明 REQ-005 全栈闭环，并保存可复验的 AC 映射。

## 完成条件

- AC-001～AC-012 各有新鲜、可定位的 pass/fail/not_run 证据。
- 完成四格式上传到引用的 E2E、故障恢复、版本失败保护和安全边界检查。
- 固定检索评测集 Recall@8 >= 90%，记录 MRR、引用正确率和无答案误答率。
- 后端、前端、格式、类型、依赖、构建和适用 E2E 全部通过。

## 范围依据

REQ-005 的最终集成门。子任务通过不能替代本任务的真实全栈证据。

## 实施记录

- 2026-09-22：`zq-flow` 在 TASK-024 完成后选中本任务，开始最终全栈矩阵与固定检索评测。
- 2026-09-22：建立四格式上传到引用的真实 API/Worker/Milvus 链路、10 文档固定检索集，以及桌面/移动真实浏览器编排；修复全栈验收发现的迟到队列消息和移动知识范围弹层问题。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-001～AC-012 | 本地 PostgreSQL/Redis/Milvus/Worker/API 与桌面/移动真实浏览器 | pass | [VERIFY-004](../verification/VERIFY-004.md) 各 AC 映射；183 项真实后端、4 项真实浏览器通过 |
| 检索评测 | 10 文档、10 个命中问题、2 个无答案问题的真实 Milvus hybrid 评测 | pass | Recall@8=1.0、MRR=1.0、Top-1 引用正确率=1.0；词面无答案误答率=0，边界见 VERIFY-004 |
| 质量门 | 后端、前端、E2E、lint、类型、格式、依赖与构建 | pass | 默认后端 148 通过/35 跳过；前端 27 通过、默认 E2E 8 通过/4 跳过；静态门与构建 exit 0 |

## 阻碍与解除条件

无。TASK-021、TASK-023、TASK-024 已完成；本轮发现的迟到消息与移动弹层缺陷已在相应实现和回归中修复，未转移为未完成任务。

## 后续事项

真实付费 Embedding 或聊天供应商联网验证只有在用户提供凭据和费用授权时执行；否则按设计使用确定性替身并记录 not_run。

## 交接

以 [VERIFY-004](../verification/VERIFY-004.md) 为最终证据。REQ-005 当前 approved 范围完成；Wiki 阶段仍为 TASK-026 proposed，只有另获授权才可启动。

## 变更历史

- 2026-09-20T17:53:12+08:00：创建 approved 集成验收任务，status=todo。
- 2026-09-22：依赖已满足，`todo → in_progress`。
- 2026-09-22：真实全栈、评测与质量门齐全，`in_progress → verifying → done`。
