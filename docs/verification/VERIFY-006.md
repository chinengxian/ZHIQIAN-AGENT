# VERIFY-006 文档详情与 Wiki 来源定位验收

- 日期：2026-10-05
- 范围：REQ-007 / TASK-033 / TASK-034 / TASK-035
- 结论：通过。后端服务层、前端页面、真实 API + PostgreSQL + 浏览器闭环均有当前证据。

## 验收矩阵

| AC | 证据 | 结果 |
| --- | --- | --- |
| REQ-007/AC-001 | `uv run pytest tests/test_knowledge_api.py -q`；真实浏览器访问文档详情页 | pass |
| REQ-007/AC-002 | `RUN_KNOWLEDGE_INTEGRATION=1 uv run pytest tests/test_knowledge_management_integration.py::test_document_inspection_uses_safe_versions_preview_and_ownership -q` | pass |
| REQ-007/AC-003 | 真实 Vite 页面 + 真实 API + PostgreSQL：Wiki 来源点击进入文档详情深链，URL 保留 `version` 与 `chunk` | pass |
| REQ-007/AC-004 | 集成用例覆盖跨文档版本和 chunk 拒绝；浏览器验证失效来源路径使用固定版本/锚点，不回退活动版本 | pass |
| REQ-007/AC-005 | API 和服务测试确认详情响应不包含 `file_path`、`sha256`、Embedding 模型或指纹；跨文档资源返回稳定 not found | pass |
| REQ-007/AC-006 | Playwright 使用 Chrome 检查 1440px 桌面与 390px 移动详情页，无横向溢出；刷新深链可恢复详情 | pass |

## 实际执行

- `uv run pytest tests/test_knowledge_api.py tests/test_knowledge_models.py tests/test_document_versions.py -q`：18 passed。
- `RUN_KNOWLEDGE_INTEGRATION=1 uv run pytest tests/test_knowledge_management_integration.py::test_document_inspection_uses_safe_versions_preview_and_ownership -q`：1 passed。
- `uv run ruff check src\agent_api\knowledge\api\routes.py src\agent_api\knowledge\application\management.py tests\test_knowledge_api.py tests\test_knowledge_management_integration.py`：pass。
- `uv run mypy src\agent_api\knowledge\api\routes.py src\agent_api\knowledge\application\management.py`：pass。
- `npm test -- knowledgeApi.spec.ts --run`：3 passed。
- `npm run type-check`：pass。
- `npm run lint`：pass。
- `npm run build`：pass。
- Playwright + 本地 Chrome + Vite dev server + API `http://127.0.0.1:8000`：real api browser smoke passed。

## 说明

真实浏览器验收使用固定 UUID 临时写入本地 PostgreSQL 的知识库、文档、版本、parent/child chunk 与 Wiki claim source；验收后已删除该临时知识库及相关 outbox 记录。

正文预览中的 `<script>alert(1)</script>` 在浏览器中按文本显示，未执行为 HTML。页面未实现原文件下载、PDF/Office 原生阅读或正文编辑，符合 REQ-007 非目标。
