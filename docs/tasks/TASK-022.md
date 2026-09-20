---
id: TASK-022
title: 实现混合检索与 Agent 知识工具
status: todo
execution_scope: approved
kind: backend
priority: high
owner: unassigned
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
base_revision: 83c58e29e87019acc449de54d6ef368726a85668
updated_at: 2026-09-20T17:53:12+08:00
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

尚未开始。

## 验证证据

| AC／条件 | 环境与命令／操作 | 结果 | 证据与修订 |
| --- | --- | --- | --- |
| AC-006 | 待执行真实 Milvus 混合检索与降级测试 | not_run | 实现后记录 |
| AC-007 | 待执行确定性 Agent 工具测试 | not_run | 实现后记录 |
| AC-008/010 | 待执行 schema/SSE/引用测试 | not_run | 实现后记录 |

## 阻碍与解除条件

依赖 TASK-020 的活动版本和 chunk hydrate 接口。Reranker 模型不可用时必须证明 RRF 降级，而不是阻塞核心检索。

## 后续事项

无独立建议。

## 交接

完成后向 TASK-023 提供冻结的 `knowledge_scope`、`status` 和 `sources` 契约；向 TASK-025 提供检索测试入口和指标输出。

## 变更历史

- 2026-09-20T17:53:12+08:00：创建 approved 后端检索任务，status=todo。
