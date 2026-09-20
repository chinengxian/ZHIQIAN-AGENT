---
id: TASK-019
title: 建设知识基础设施与持久化基线
status: todo
execution_scope: approved
kind: backend
priority: high
owner: unassigned
depends_on:
  - TASK-018
requirements:
  - REQ-005
acceptance:
  - REQ-005/AC-001
  - REQ-005/AC-005
  - REQ-005/AC-011
read_refs:
  - docs/requirements/REQ-005.md
  - docs/superpowers/specs/2026-09-20-native-knowledge-rag-design.md
  - pyproject.toml
  - src/agent_api/core/config.py
  - src/agent_api/core/lifespan.py
write_scope:
  - pyproject.toml
  - .env.example
  - README.md
  - compose.yaml
  - alembic.ini
  - migrations/
  - src/agent_api/core/config.py
  - src/agent_api/core/lifespan.py
  - src/agent_api/knowledge/domain/
  - src/agent_api/knowledge/infrastructure/database/
  - src/agent_api/knowledge/infrastructure/storage/
  - tests/test_knowledge_config.py
  - tests/test_knowledge_models.py
  - tests/test_knowledge_storage.py
  - docs/tasks/TASK-019.md
base_revision: 83c58e29e87019acc449de54d6ef368726a85668
updated_at: 2026-09-20T17:53:12+08:00
---

## 目标

建立 PostgreSQL、Redis、Milvus、文件存储、迁移和知识领域模型的可测试基础，为后续入库与检索提供唯一共享配置和持久化边界。

## 完成条件

- SQLAlchemy/Alembic 模型覆盖工作区、知识库、文档、版本、chunk、任务和 outbox。
- Compose 可以启动 PostgreSQL、Redis、Milvus 及其内部依赖，应用文件卷独立。
- 启动期校验连接、迁移、存储目录、Milvus schema 和 Embedding 维度，不静默破坏已有数据。
- 根依赖、锁文件、配置示例和 README 由本任务统一修改。

## 范围依据

REQ-005 AC-001、AC-005、AC-011 及已确认设计第 3、6、7、12 节。

## 实施记录

尚未开始。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-001 | 待执行配置、迁移与容器测试 | not_run | 实现后记录 |
| AC-005 | 待执行 schema/版本约束测试 | not_run | 实现后记录 |
| AC-011 | 待执行存储路径和错误脱敏测试 | not_run | 实现后记录 |

## 阻碍与解除条件

无。开始前确认本机 Docker 可用；若不可用，单元实现可继续，但容器集成证据保持 not_run，任务不得 done。

## 后续事项

无独立建议。

## 交接

完成后向 TASK-020 交付可导入的 repository、迁移、配置、存储端口和可运行基础设施；向 TASK-022 交付 Milvus/Embedding 配置边界。

## 变更历史

- 2026-09-20T17:53:12+08:00：创建 approved 高优先级基础任务，status=todo。
