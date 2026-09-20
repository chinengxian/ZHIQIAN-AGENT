---
id: TASK-020
title: 实现文档入库与知识管理 API
status: todo
execution_scope: approved
kind: backend
priority: high
owner: unassigned
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
  - src/agent_api/knowledge/application/ingestion.py
  - src/agent_api/knowledge/application/management.py
  - src/agent_api/knowledge/api/
  - src/agent_api/knowledge/infrastructure/docling/
  - src/agent_api/knowledge/infrastructure/embedding/
  - src/agent_api/knowledge/infrastructure/jobs/
  - src/agent_api/knowledge/infrastructure/milvus/
  - src/agent_api/knowledge/worker/
  - src/agent_api/api/router.py
  - src/agent_api/main.py
  - tests/fixtures/documents/
  - tests/test_knowledge_api.py
  - tests/test_ingestion_pipeline.py
  - tests/test_chunking.py
  - tests/test_outbox.py
  - tests/test_document_versions.py
  - docs/tasks/TASK-020.md
base_revision: 83c58e29e87019acc449de54d6ef368726a85668
updated_at: 2026-09-20T17:53:12+08:00
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

尚未开始。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-002～AC-005 | 待执行 API、fixture、Worker 与版本测试 | not_run | 实现后记录 |
| AC-011 | 待执行 Redis/Milvus 故障与恢复测试 | not_run | 实现后记录 |

## 阻碍与解除条件

依赖 TASK-019 完成。真实 OCR fixture 若受模型下载或平台限制，文本 PDF 仍需通过；扫描 PDF 证据必须准确记录环境和限制，不能伪装通过。

## 后续事项

无独立建议。

## 交接

完成后 TASK-021 可以接入真实知识管理 API；TASK-022 可以复用活动版本过滤和 Milvus adapter。

## 变更历史

- 2026-09-20T17:53:12+08:00：创建 approved 后端入库任务，status=todo。
