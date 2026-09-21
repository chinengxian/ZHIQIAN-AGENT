---
id: TASK-020
title: 实现文档入库与知识管理 API
status: done
execution_scope: approved
kind: backend
priority: high
owner: /root
depends_on:
  - TASK-019
requirements:
  - REQ-005
acceptance:
  - REQ-005/AC-002
  - REQ-005/AC-003
  - REQ-005/AC-004
  - REQ-005/AC-005
  - REQ-005/AC-011
read_refs:
  - docs/requirements/REQ-005.md
  - docs/superpowers/specs/2026-09-20-native-knowledge-rag-design.md
  - src/agent_api/api/router.py
  - src/agent_api/core/lifespan.py
  - tests/conftest.py
write_scope:
  - pyproject.toml
  - uv.lock
  - src/agent_api/knowledge/application/ingestion.py
  - src/agent_api/knowledge/application/management.py
  - src/agent_api/knowledge/api/
  - src/agent_api/knowledge/infrastructure/docling/
  - src/agent_api/knowledge/infrastructure/embedding/
  - src/agent_api/knowledge/infrastructure/jobs/
  - src/agent_api/knowledge/infrastructure/milvus/
  - src/agent_api/knowledge/worker/
  - src/agent_api/api/router.py
  - src/agent_api/core/lifespan.py
  - src/agent_api/knowledge/domain/statuses.py
  - tests/fixtures/documents/
  - tests/test_knowledge_api.py
  - tests/test_ingestion_pipeline.py
  - tests/test_chunking.py
  - tests/test_outbox.py
  - tests/test_document_versions.py
  - tests/test_docling_adapter.py
  - tests/test_embedding_adapter.py
  - tests/test_ingestion_e2e_integration.py
  - tests/test_knowledge_management_integration.py
  - tests/test_knowledge_worker.py
  - tests/test_lifespan.py
  - tests/test_milvus_adapter.py
  - tests/test_version_recovery_integration.py
  - tests/test_worker_restart_integration.py
  - tests/test_api_restart_integration.py
  - docs/tasks/TASK-020.md
  - docs/development/README.md
base_revision: 83c58e29e87019acc449de54d6ef368726a85668
updated_at: 2026-09-21T20:23:00+08:00
---

## 目标

实现从知识库 CRUD、四类文件上传到 Docling 解析、父子分块、Embedding、Milvus 写入、版本切换、进度 SSE 和删除补偿的后端闭环。

## 完成条件

- 知识库与文档 REST、202 上传和工作区进度 SSE 契约可用。
- 四类有效 fixture 可到 ready；损坏文件进入 failed。
- outbox、Celery、heartbeat、幂等重试、版本切换和清理补偿有真实持久化证据。
- 仅子块写入 Milvus，父块和引用位置可从 PostgreSQL hydrate。

## 范围依据

REQ-005 AC-002～AC-005、AC-011 及设计第 4～7、10、13～14 节。

## 实施记录

- 2026-09-20：由 `zq-flow` 自动选择为下一项 approved 高优先级后端任务；已核实 TASK-019 完成、工作树干净，owner 更新为 `/root` 并按 TDD 开始实现。
- 依赖预检确认上传表单、Docling 与 Worker 运行库尚未进入根依赖；单人协调模式下将 `pyproject.toml`、`uv.lock` 加入写入范围，保持 TASK-020 的既定业务边界不变。
- 2026-09-21：完成管理 REST、202 上传、Docling、父子块、Embedding、Milvus、outbox/Celery、版本切换、SSE 和删除补偿的首轮实现；补充 outbox 领取租约、重复删除幂等、上传限量读取及删除／入库竞态保护。真实 Redis/Celery Worker 已分别消费排队上传和删除；状态仍为 `verifying`，不把未覆盖的故障恢复判为通过。
- 2026-09-21：补齐知识库编辑/启停、重复上传显式新版本、重建失败补偿、幂等重试及 Worker 中断/恢复测试；真实 PostgreSQL、Milvus、Redis、Celery Worker 的单项故障测试已分别通过。长耗时 Embedding 新增持续心跳及先红后绿的回归测试。
- 2026-09-21：恢复五个容器后完成整套真实集成；增加 Redis 容器重启、API 进程重启与已提交任务持久性验收。必需 AC 均有当前证据，任务结案。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-002 | `tests/test_knowledge_api.py`；真实 PostgreSQL `tests/test_knowledge_management_integration.py` | pass | 创建、查询、编辑、启停、数量、显式确认删除及异步清理通过；包含于 25 项真实集成 |
| AC-003 | 真实容器套件及 `tests/test_api_restart_integration.py`、`tests/test_worker_restart_integration.py` | pass | 四格式 ready、损坏 PDF 安全失败、202 与进度阶段通过；已提交任务经历真实 Redis 重启、API 进程重启、Worker 中断/恢复后仍在 PostgreSQL/outbox 并最终被 Worker 消费 |
| AC-004 | 真实容器套件；`tests/test_chunking.py`、`tests/test_docling_adapter.py`、`tests/test_milvus_adapter.py` | pass | 父子块、标题/页码或替代位置、PostgreSQL 可追溯内容及仅子块写入真实 Milvus 均通过 |
| AC-005 | `tests/test_document_versions.py`、真实 Milvus `tests/test_version_recovery_integration.py` | pass | 重建失败保留旧活动版及索引；成功后原子切换并清理旧索引，晚到的清理不会删除活动版 |
| AC-011 | 真实 PostgreSQL/Redis/Milvus/Worker 故障注入及单元安全测试 | pass | broker、数据库、Embedding、Milvus 瞬时故障，延迟 outbox、心跳、幂等重试/补偿与脱敏错误均通过；长耗时 Embedding 心跳回归先红后绿 |
| 代码质量 | `pytest -q`、真实容器套件、`ruff check src tests`、`ruff format --check src tests`、`mypy src`、`uv lock --check`、`git diff --check` | pass | 2026-09-21 20:23 +08:00：默认 137 passed、25 skipped；显式容器套件 25 passed（372.69 秒）；Ruff、格式、mypy、锁文件及差异空白检查均通过 |

## 阻碍与解除条件

无当前阻碍。此前 Docker Desktop 自身 socket 故障已解除，五个容器均 healthy；未删除或重置数据卷。当前 PDF 验收使用文本 PDF；扫描版 OCR 属后续扩展，不将其伪装为本任务证据。

## 后续事项

TASK-021 可以按已验证的知识管理 REST/SSE 契约开发；TASK-025 仍需独立执行原生知识库全栈验收和检索质量门。

## 交接

完成后 TASK-021 可以接入真实知识管理 API；TASK-022 可以复用活动版本过滤和 Milvus adapter。

## 变更历史

- 2026-09-20T17:53:12+08:00：创建 approved 后端入库任务，status=todo。
- 2026-09-20T22:19:00+08:00：依赖解除后由 `zq-flow` 选中，owner=/root，status=in_progress。
- 2026-09-21T14:13:00+08:00：首轮实现和单元/容器集成通过，真实队列和故障验收仍有缺口，status=verifying。
- 2026-09-21T19:51:00+08:00：故障恢复与持续心跳已实现；本轮真实容器回归被 Docker Desktop 自身启动故障阻断，status 保持 verifying。
- 2026-09-21T20:23:00+08:00：五条必需 AC、25 项真实容器集成及本地质量门通过，status=done。
