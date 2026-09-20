---
id: TASK-024
title: 强化知识任务恢复、安全与可观测性
status: todo
execution_scope: approved
kind: maintenance
priority: normal
owner: unassigned
depends_on:
  - TASK-020
  - TASK-022
requirements:
  - REQ-005
acceptance:
  - REQ-005/AC-003
  - REQ-005/AC-005
  - REQ-005/AC-007
  - REQ-005/AC-011
read_refs:
  - docs/requirements/REQ-005.md
  - docs/superpowers/specs/2026-09-20-native-knowledge-rag-design.md
  - docs/tasks/TASK-020.md
  - docs/tasks/TASK-022.md
write_scope:
  - src/agent_api/knowledge/application/recovery.py
  - src/agent_api/knowledge/application/cleanup.py
  - src/agent_api/knowledge/infrastructure/jobs/
  - src/agent_api/knowledge/infrastructure/observability/
  - src/agent_api/knowledge/worker/
  - tests/test_knowledge_recovery.py
  - tests/test_knowledge_cleanup.py
  - tests/test_knowledge_security.py
  - tests/test_knowledge_observability.py
  - docs/tasks/TASK-020.md
  - docs/tasks/TASK-022.md
  - docs/tasks/TASK-024.md
base_revision: 83c58e29e87019acc449de54d6ef368726a85668
updated_at: 2026-09-20T17:53:12+08:00
---

## 目标

对已实现入库与检索链路执行故障注入和安全加固，补齐超时任务恢复、孤儿索引清理、日志脱敏、资源限制和知识 Prompt Injection 边界。

## 完成条件

- Redis/API/Worker/Milvus 受控故障后，任务可恢复或稳定失败，无永久 pending。
- 删除、重建和孤儿索引清理均幂等。
- 错误、日志、SSE 和前端响应不泄露密钥、正文、向量或真实路径。
- 检索内容保持数据身份，不能诱导 Agent 获得写工具或忽略系统规则。

## 范围依据

REQ-005 AC-003、AC-005、AC-007、AC-011 及设计第 13～14 节。

## 实施记录

尚未开始。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| 恢复与补偿 | 待执行故障注入测试 | not_run | 实现后记录 |
| 安全与隐私 | 待执行恶意文档、路径和日志扫描 | not_run | 实现后记录 |

## 阻碍与解除条件

依赖 TASK-020 与 TASK-022 的主链路就绪。发现的当前 AC 必需缺陷必须回到对应实现任务修复，不能通过登记后续事项关闭本任务。

## 后续事项

病毒扫描实现仍在本阶段范围外；只验证扫描端口不会破坏上传事务。

## 交接

完成后向 TASK-025 提供故障矩阵、日志/错误扫描命令和恢复证据。

## 变更历史

- 2026-09-20T17:53:12+08:00：创建 approved 可靠性与安全任务，status=todo。
