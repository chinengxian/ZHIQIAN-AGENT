---
id: TASK-025
title: 验证原生知识库与多级 RAG 全栈闭环
status: todo
execution_scope: approved
kind: integration
priority: normal
owner: unassigned
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
updated_at: 2026-09-20T17:53:12+08:00
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

尚未开始。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-001～AC-012 | 待执行完整集成矩阵 | not_run | 写入 VERIFY-004 |
| 检索评测 | 待运行固定数据集 | not_run | 写入 VERIFY-004 |
| 质量门 | 待运行后端/前端/E2E/依赖命令 | not_run | 写入 VERIFY-004 |

## 阻碍与解除条件

等待 TASK-021、TASK-023、TASK-024 完成。发现实现缺陷时重开对应 owner 任务或在明确协调范围内修复并更新其证据，不把缺陷隐藏在验收报告中。

## 后续事项

真实付费 Embedding 或聊天供应商联网验证只有在用户提供凭据和费用授权时执行；否则按设计使用确定性替身并记录 not_run。

## 交接

完成后以 `docs/verification/VERIFY-004.md` 为最终证据，更新开发入口并说明 Wiki 阶段是否具备启动条件。

## 变更历史

- 2026-09-20T17:53:12+08:00：创建 approved 集成验收任务，status=todo。
