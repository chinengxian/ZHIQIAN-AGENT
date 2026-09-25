# VERIFY-004 原生知识库与多级 RAG 全栈验收

- 范围／任务：REQ-005/AC-001～AC-012，TASK-019～TASK-025
- 时间：2026-09-22T14:47:00+08:00
- 代码基线：`83c58e29e87019acc449de54d6ef368726a85668` 之上的本地未提交改动
- 环境：Windows、Python 3.11、Node 22、本地 Docker PostgreSQL／Redis／Milvus／MinIO／etcd；API、Celery Worker/Beat、Vite 和 Playwright 由测试入口临时启动
- 结论：**PASS**（范围内必需条件）；真实付费聊天与 Embedding 供应商联网验证 **not_run**

## 验收映射

| 条件 | 实际操作与观察 | 判定 | 主要证据 |
| --- | --- | --- | --- |
| AC-001 | 真实容器启动 API 和 Worker；迁移、连接、目录、Milvus schema／维度检查及不匹配安全失败回归通过 | pass | `tests/test_knowledge_startup.py`、`tests/test_knowledge_management_integration.py`、真实后端全套 |
| AC-002 | 知识库 CRUD、启停、列表数量、含文档确认删除和异步清理；浏览器删除后轮询得到 404 | pass | `tests/test_knowledge_management_integration.py`、`web/tests/e2e/knowledge.real.spec.ts` |
| AC-003 | PDF、DOCX、MD、TXT 上传返回 202，经真实 Worker 到 ready；坏 PDF 安全失败；重启与丢消息恢复用例通过 | pass | `tests/integration/test_full_knowledge_chat.py`、`web/tests/e2e/knowledge-chat.real.spec.ts`、`tests/test_knowledge_recovery.py` |
| AC-004 | Docling 解析和父子块追溯、标题路径及页码／位置回退；Milvus 仅索引子块 | pass | `tests/test_docling_adapter.py`、`tests/test_ingestion_pipeline.py`、四格式真实入库 |
| AC-005 | 重建成功切换活动版本；失败继续服务旧版；重复清理幂等 | pass | `tests/test_ingestion_e2e_integration.py`、`tests/test_knowledge_recovery.py`、真实管理 E2E |
| AC-006 | 真实 Milvus 执行 hybrid、semantic、keyword；Dense/BM25 经 RRF 融合，Reranker 可关闭／降级，范围受限 | pass | `tests/test_hybrid_retrieval.py`、`tests/eval/test_retrieval_quality.py` |
| AC-007 | 确定性 Agent 执行 search/read/list 只读工具；无答案不造引用，文档指令不能提升权限 | pass | `tests/test_knowledge_tools.py`、`tests/test_prompt_injection_boundary.py`、真实浏览器无答案场景 |
| AC-008 | 后端校验 `knowledge_scope`；SSE 保留 message/done/error 并新增 status/sources；默认范围兼容旧聊天 | pass | `tests/test_chat_knowledge_stream.py`、`tests/integration/test_full_knowledge_chat.py`、`web/tests/services/sseParser.spec.ts` |
| AC-009 | 桌面和移动浏览器以真实 API/Worker 操作知识库、上传、进度、失败、重试、重建、删除和导航 | pass | `web/tests/e2e/knowledge.real.spec.ts`、真实浏览器 4 项通过 |
| AC-010 | 四格式在桌面／移动端均显示回答引用与来源抽屉；PDF 页码、其他格式标题路径回退及路径隐藏由组件／API 回归覆盖 | pass | `web/tests/e2e/knowledge-chat.real.spec.ts`、`web/tests/SourceDrawer.spec.ts`、`tests/test_knowledge_security.py` |
| AC-011 | broker、数据库、Embedding、Milvus、Worker/API 故障与恢复矩阵；原子领取、迟到队列消息 ACK、清理幂等、日志脱敏 | pass | `tests/test_knowledge_recovery.py`、`tests/test_knowledge_observability.py`、`tests/test_knowledge_security.py`、真实后端全套 |
| AC-012 | 固定检索集 Recall@8=1.0，超过 0.90 门槛；完整质量门通过 | pass | `tests/eval/knowledge_cases.json`、`tests/eval/test_retrieval_quality.py`、下表 |

## 最终质量门

| 检查 | 结果 |
| --- | --- |
| `RUN_KNOWLEDGE_INTEGRATION=1 .\.venv\Scripts\python.exe -m pytest -q` | **183 passed**，847.46s；包含真实数据库／向量库集成与固定评测 |
| 默认 `pytest -q` | 148 passed、35 skipped；跳过项需要真实知识容器 |
| `python -m tests.integration.run_browser_e2e`（设置真实集成环境） | **4 passed**，6.2m；桌面／移动各覆盖管理与四格式引用闭环，测试栈退出后清理 |
| 默认 Playwright | 8 passed、4 skipped；跳过真实服务专用用例 |
| `npm test -- --run`、`npm run build` | 27 passed；116 modules transformed，构建成功 |
| `npm run lint`、`npm run type-check` | exit 0，无诊断 |
| `ruff check .`、`ruff format --check .`、`mypy src` | exit 0；105 files formatted，54 source files 无类型错误 |
| `pip check`、`uv lock --check`、`git diff --check` | exit 0，无破损依赖／空白错误 |

固定集为 10 篇不同主题的 TXT、10 个可回答查询及 2 个无答案查询，真实 Milvus hybrid 检索结果的 `Recall@8=1.0`、`MRR=1.0`、`citation_correctness_top_1=1.0`，十个目标均排第 1。`no_answer_false_answer_rate=0.0` 是针对这两道题的确定性词面证据检查，**不是**通用模型幻觉率；真实浏览器另外验证确定性 Agent 在未知查询时显示“未找到相关资料”且不显示来源。评测集规模小、查询关键词明确，不能外推生产数据集质量。

## 边界与清理

- 不读取或修改私有 `.env`，不调用真实付费聊天／Embedding 供应商；本地 Embedding 服务和确定性工具调用模型只证明契约及全栈接线。真实供应商的凭据、费用、可用性和回答质量均为 **not_run**，不影响本次已定义的本地集成门。
- 真实浏览器用例在 finally 中删除所建知识库并等待 API 404；数据库中测试命名前缀的残留数复核为 0。故意损坏的 PDF 产生预期 `document_parse_failed`；未发现 `ingestion_unavailable` 日志。
- Docker 数据服务继续运行；本次临时 API、Worker/Beat、Embedding stub 和测试 Vite 服务在 runner 结束时关闭。页面开发服务与后端是否持续运行不属于此验收结论。
- Wiki 仍为 TASK-026 `proposed`，未取得执行授权，也未混入 REQ-005。

## 结论与下一步

AC-001～AC-012 全部有范围内的实际通过证据。TASK-025 可关闭为 `done`，REQ-005 已批准的 TASK-019～TASK-025 阶段完成。未来接入真实付费模型或扩展 Wiki 时需分别取得凭据／费用或任务授权，并重新验证其新增边界。
