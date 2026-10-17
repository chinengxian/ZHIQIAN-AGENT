---
id: TASK-033
title: 实现文档详情、版本与正文预览 API
status: done
execution_scope: approved
kind: backend
priority: normal
owner: /root
depends_on:
  - TASK-032
requirements:
  - REQ-007
acceptance:
  - REQ-007/AC-001
  - REQ-007/AC-002
  - REQ-007/AC-004
  - REQ-007/AC-005
read_refs:
  - docs/requirements/REQ-007.md
  - docs/superpowers/specs/2026-10-05-document-inspection-design.md
  - docs/superpowers/plans/2026-10-05-document-inspection.md
  - src/agent_api/knowledge/api/routes.py
  - src/agent_api/knowledge/application/management.py
  - src/agent_api/knowledge/infrastructure/database/models.py
write_scope:
  - src/agent_api/knowledge/api/routes.py
  - src/agent_api/knowledge/application/management.py
  - tests/test_knowledge_api.py
  - tests/test_knowledge_management_integration.py
base_revision: 9ed1938358f7901c30da5d7b88b6de2e8ec150db
updated_at: 2026-10-05T23:38:00+08:00
---

## 目标

提供可供前端使用的文档详情、版本列表和分页正文预览 API，并支持 Wiki chunk 锚点定位。

## 完成条件

- 详情响应包含必要元数据和最近任务，且不暴露敏感内部字段。
- 版本与正文使用稳定游标分页，条数和正文字符数有上限。
- child 锚点可解析到 parent 上下文并返回 focus；失效或已清理正文有明确空状态。
- 文档、版本和 chunk 归属校验覆盖跨库/跨文档拒绝。

## 范围依据

REQ-007 已由用户明确指令确认；本任务是已批准范围的首个实现工作包。

## 实施记录

- 2026-10-05T23:20:00+08:00：由 zq-flow 自动选择为当前可执行后端任务，状态改为 in_progress，先进入后端 TDD 红灯阶段。
- 2026-10-05T23:38:00+08:00：完成详情、版本列表和正文预览 API；详情响应保留旧字段并增加安全版本/任务元数据，新增版本分页与正文预览端点，服务层校验 document/version/chunk 归属并支持 child chunk 定位到 parent 上下文。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-001、AC-002、AC-004、AC-005 | `uv run pytest tests/test_knowledge_api.py -q` | pass | RED：新增 3 个契约测试失败；GREEN：10 passed，覆盖详情脱敏、版本分页端点、预览锚点参数与纯文本返回 |
| AC-001、AC-002、AC-004、AC-005 | `uv run pytest tests/test_knowledge_api.py tests/test_knowledge_models.py tests/test_document_versions.py -q` | pass | 18 passed，确认知识库 API、模型和版本恢复相关回归未破坏 |
| AC-001、AC-002、AC-004、AC-005 | `uv run pytest tests/test_knowledge_management_integration.py::test_document_inspection_uses_safe_versions_preview_and_ownership -q` | not_run | 1 skipped；当前环境未设置 `RUN_KNOWLEDGE_INTEGRATION=1`，真实 PostgreSQL 用例已补充但未执行 |
| AC-001、AC-002、AC-004、AC-005 | `uv run ruff check ...`、`uv run ruff format --check ...`、`uv run mypy src\agent_api\knowledge\api\routes.py src\agent_api\knowledge\application\management.py` | pass | Ruff、format、mypy 均通过 |

## 阻碍与解除条件

无。TASK-032 已完成。

## 后续事项

已解锁 TASK-034。

## 交接

后端契约已固定；前端可使用 `GET /api/v1/documents/{id}`、`GET /api/v1/documents/{id}/versions` 和 `GET /api/v1/documents/{id}/preview` 接入详情页。真实 PostgreSQL 闭环留给 TASK-035 在集成环境执行。

## 变更历史

- 2026-10-05T23:05:56+08:00：由 TASK-032 创建，status=todo。
- 2026-10-05T23:20:00+08:00：zq-flow 开始执行，status=in_progress，owner=/root。
- 2026-10-05T23:38:00+08:00：后端实现与聚焦验证完成，status=done。

