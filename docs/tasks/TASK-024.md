---
id: TASK-024
title: 强化知识任务恢复、安全与可观测性
status: done
execution_scope: approved
kind: maintenance
priority: normal
owner: 23196
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
  - src/agent_api/knowledge/infrastructure/database/models.py
  - src/agent_api/knowledge/application/ingestion.py
  - src/agent_api/knowledge/infrastructure/observability/
  - src/agent_api/knowledge/worker/
  - tests/test_knowledge_recovery.py
  - tests/test_knowledge_cleanup.py
  - tests/test_knowledge_security.py
  - tests/test_knowledge_observability.py
  - tests/test_knowledge_management_integration.py
  - tests/test_ingestion_pipeline.py
  - tests/test_knowledge_worker.py
  - docs/tasks/TASK-020.md
  - docs/tasks/TASK-022.md
  - docs/tasks/TASK-024.md
base_revision: 83c58e29e87019acc449de54d6ef368726a85668
updated_at: 2026-09-22T04:30:00+08:00
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

- 2026-09-22：`zq-flow` 在 TASK-023 完成后选中本任务，先审计恢复扫描、outbox、清理和安全边界。
- 2026-09-22：恢复扫描补齐已发布却未消费的 pending/retry_wait 任务，保留未发布事件的重试预算；重试耗尽时更新文档/版本终态，激活后崩溃直接补记成功。摄取 job 改为 PostgreSQL 行锁原子领取，重复队列消息不二次解析。outbox/恢复增加仅含固定事件名、UUID 和不透明引用的结构化日志；Celery 设置时间、并发和子进程内存上限。
- 2026-09-22：TASK-025 的真实浏览器验收发现删除后迟到的队列消息仍触发无意义重试；缺失 job／版本／文档或文档处于删除中时按已领取处理并 ACK，新增回归。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| 恢复与补偿 | `RUN_KNOWLEDGE_INTEGRATION=1` 执行 `pytest -q`，真实 PostgreSQL/Redis/Milvus/Worker/API | pass | 原 180 项通过；TASK-025 后续全套 183 通过，含删除后迟到消息 ACK 回归。无永久 pending，重复消费不重复入库 |
| 安全与隐私 | `test_knowledge_security.py`、`test_knowledge_observability.py`、`test_prompt_injection_boundary.py` 与全套 | pass | 越界路径拒绝、公开来源不含存储路径/向量、故障日志不含异常里的密钥/正文、恶意引用被过滤；只读工具和系统提示边界沿用 TASK-022 |
| 资源与代码质量 | `test_knowledge_worker.py`；Ruff、format、mypy、`uv lock --check`、`git diff --check` | pass | Celery 软/硬时限 1500/1800 秒、并发 2、子进程 1 GiB、20 任务回收；静态门全部通过 |

## 阻碍与解除条件

无。当前 compose 只运行数据服务；Worker 在本地进程运行，Celery 进程级限额已配置，容器级 CPU/内存限制需在未来 Worker 容器部署时另行配置。

## 后续事项

病毒扫描实现仍在本阶段范围外；只验证扫描端口不会破坏上传事务。

## 交接

向 TASK-025 提供 `RUN_KNOWLEDGE_INTEGRATION=1` 的真实容器回归入口、恢复/安全用例和静态门；完整上传到带引用回答已由 VERIFY-004 验收。

## 变更历史

- 2026-09-20T17:53:12+08:00：创建 approved 可靠性与安全任务，status=todo。
- 2026-09-22：依赖已满足，`todo → in_progress`。
- 2026-09-22：恢复、故障、隐私和质量门通过，`in_progress → done`。
