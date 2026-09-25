---
id: TASK-022
title: 实现混合检索与 Agent 知识工具
status: done
execution_scope: approved
kind: backend
priority: high
owner: 23196
depends_on:
  - TASK-020
requirements:
  - REQ-005
acceptance:
  - REQ-005/AC-006
  - REQ-005/AC-007
  - REQ-005/AC-008
  - REQ-005/AC-010
read_refs:
  - docs/requirements/REQ-005.md
  - docs/superpowers/specs/2026-09-20-native-knowledge-rag-design.md
  - src/agent_api/llm/agent.py
  - src/agent_api/llm/protocol.py
  - src/agent_api/api/chat.py
  - src/agent_api/schemas/chat.py
  - src/agent_api/core/lifespan.py
  - pyproject.toml
  - uv.lock
write_scope:
  - src/agent_api/knowledge/application/retrieval.py
  - src/agent_api/knowledge/application/citations.py
  - src/agent_api/knowledge/tools/
  - src/agent_api/knowledge/infrastructure/rerank/
  - src/agent_api/knowledge/infrastructure/milvus/
  - src/agent_api/llm/agent.py
  - src/agent_api/llm/protocol.py
  - src/agent_api/api/chat.py
  - src/agent_api/schemas/chat.py
  - tests/test_hybrid_retrieval.py
  - tests/test_knowledge_tools.py
  - tests/test_chat_knowledge_stream.py
  - tests/test_prompt_injection_boundary.py
  - docs/tasks/TASK-022.md
base_revision: 4d95039a5f056caaca9b187c1cb747d92686ef0b
updated_at: 2026-09-22T03:00:00+08:00
---

## 目标

实现 Dense + BM25 + RRF、可选 BGE Rerank、父块/邻块扩展、引用组装和三个只读 LangChain 工具，并扩展聊天知识范围与 SSE 来源契约。

## 完成条件

- `hybrid|semantic|keyword` 在真实 Milvus 上按知识范围检索并校验活动版本。
- `search_knowledge`、`read_document`、`list_documents` 有受限 schema、长度预算和稳定错误。
- Agent 只在需要时检索，无证据不伪造引用，知识内容不能提升权限。
- Chat 请求与 SSE 新事件兼容现有 message/done/error、短期记忆和回滚语义。

## 范围依据

REQ-005 AC-006～AC-008、AC-010 及设计第 8～10、13 节。

## 实施记录

- 2026-09-22：`zq-flow` 在 TASK-021 完成后选中本任务，TASK-020 依赖已满足，开始检索、工具与聊天契约实现。
- 2026-09-22：实现真实 Milvus Dense/BM25/RRF 检索、活动版本和启用范围过滤、可选 BGE Rerank 降级、父块上下文、三个受限只读工具、请求知识范围及 status/sources SSE。未显式提交知识范围的旧聊天仍使用原事件契约。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-006 | `RUN_KNOWLEDGE_INTEGRATION=1` 运行 `tests/test_hybrid_retrieval.py`，真实 PostgreSQL/Milvus | pass | 1 通过；hybrid/semantic/keyword、范围、停用和旧版本过滤、Rerank 故障回退 RRF |
| AC-007 | `tests/test_knowledge_tools.py`、`tests/test_prompt_injection_boundary.py` | pass | 只读工具、受限 schema、来源去重和跨 chunk 引用过滤 |
| AC-008/010 | `tests/test_chat_knowledge_stream.py`；`pytest -q` | pass | status/sources/message 及旧聊天契约；全套 143 通过、26 跳过 |
| 启动和静态门 | `ruff check src tests`、`ruff format --check src tests`、`mypy src`、`uv lock --check`、真实 Uvicorn `/health` | pass | `/health` 返回 200 `{"status":"ok"}`；可选 Rerank 依赖未安装时保持基础检索可用 |

## 阻碍与解除条件

无。Reranker 模型为可选依赖；不可用或执行失败时退回 RRF，不阻塞核心检索。真实付费模型调用未在本任务执行。

## 后续事项

无独立建议。

## 交接

向 TASK-023 提供请求 `knowledge_scope: {mode: "all_enabled" | "selected", knowledge_base_ids?: string[]}`；显式设置范围时 SSE 依次可出现 `status`、`sources`、`message`、`done`/`error`，旧请求仍只有旧事件。`sources` 的公开元数据由 `Source` 定义，不含磁盘路径。向 TASK-025 提供真实检索测试入口 `RUN_KNOWLEDGE_INTEGRATION=1`。

## 变更历史

- 2026-09-20T17:53:12+08:00：创建 approved 后端检索任务，status=todo。
- 2026-09-22T02:11:34+08:00：依赖已满足，`todo → in_progress`。
- 2026-09-22：真实检索、完整回归和服务启动通过，`in_progress → done`。
