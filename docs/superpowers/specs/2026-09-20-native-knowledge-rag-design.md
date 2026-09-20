# 原生知识库与多级 RAG 技术设计

- 状态：approved / confirmed
- 日期：2026-09-20
- 参考项目：[Tencent/WeKnora](https://github.com/Tencent/WeKnora)
- 当前项目基础：FastAPI、LangChain `create_agent`、LangGraph `InMemorySaver`、OpenAI/Anthropic 模型策略、Vue 3、Vuetify、SSE 流式对话
- 实施边界：本设计确认后另行编写实施计划；本文件不授权直接实施

## 1. 背景与目标

当前应用已经具备流式对话、短期会话记忆和可配置聊天模型，但 Agent 在 `src/agent_api/llm/agent.py` 中仍以 `tools=[]` 创建，只能依赖聊天模型自身知识。本设计参考 WeKnora 的文档入库、父子分块、混合检索、Agent 工具和来源引用思路，在当前代码库内原生实现知识能力，不部署、调用或复制 WeKnora 服务。

本阶段交付一个可持续演进的高级 RAG 基座：

- 管理 PDF、DOCX、Markdown 和 TXT 文档；
- 使用成熟解析和分块工具，不自行实现文档解析器；
- 使用 PostgreSQL 保存权威业务数据，Milvus 保存可重建检索索引，Redis 承载异步任务；
- 支持结构感知与父子分块、Dense + BM25 混合召回、RRF 融合和可选 Cross-Encoder 精排；
- 将检索作为 Agent 可自主调用的只读工具，而不是每轮无条件拼接上下文；
- 在回答中展示可回溯至文件、页码和原文片段的引用；
- 新增 Vue 知识库管理页面、文档上传进度和会话级知识范围选择；
- 保留 Wiki 所需的版本、标题层级和来源引用模型，Wiki 本身进入下一阶段。

## 2. 范围与非目标

### 2.1 本阶段范围

- 单工作区、无登录和用户隔离；所有领域记录仍包含 `workspace_id`，固定种子工作区由迁移创建。
- 文档格式：`.pdf`、`.docx`、`.md`、`.txt`。
- 文档解析：Docling；扫描 PDF 可启用 Docling OCR，手写体识别质量不作保证。
- 本地持久卷保存上传原文件；存储接口允许后续增加 S3/MinIO。
- PostgreSQL、Milvus Standalone、Redis 和独立 Celery Worker。
- 独立于聊天模型的 Embedding 配置；首个适配器使用 OpenAI-compatible Embedding API。
- 独立 Reranker 接口；支持关闭和本地多语言 BGE reranker 适配器。
- 左侧工作台导航；桌面常驻、移动端折叠抽屉。

### 2.2 非目标

- Wiki 页面生成、Wiki 编辑器、页面版本和增量刷新；
- GraphRAG、Neo4j 或其他知识图谱；
- FAQ 专用知识库；
- 多用户登录、RBAC、租户管理；
- 飞书、Notion、网页 URL 等外部数据源同步；
- 云对象存储；
- 长期会话持久化；
- 文档内容人工编辑、chunk 在线编辑和版本回滚 UI；
- 病毒扫描实现；上传管线仅预留扫描端口；
- 聊天模型自动切换、Embedding 热切换或不停机向量维度迁移。

## 3. 方案选择与总体架构

采用“模块化单体 + 独立 Worker”。FastAPI 继续负责 HTTP/SSE、领域编排和 Agent；Celery Worker 负责解析、分块、Embedding 和索引写入。该方案比 LangChain-first 的直接拼装更能控制版本、一致性和 Wiki 演进，又避免当前阶段引入独立知识微服务的协议与部署成本。

```text
Vue 3 Workbench
  ├─ Chat + knowledge scope + citations
  └─ Knowledge bases + documents + progress
             │ REST / SSE
             ▼
FastAPI modular monolith
  ├─ Chat API / LangChain Agent
  ├─ Knowledge management API
  ├─ Agent read-only tools
  ├─ Ingestion orchestration / outbox dispatcher
  └─ ports: Parser / Chunker / Embedding / Reranker / VectorIndex / JobQueue
             │
       Redis / Celery ───────────────> Worker
                                         ├─ Docling
                                         ├─ parent/child chunking
                                         ├─ batch embedding
                                         └─ Milvus indexing
             │
             ├─ PostgreSQL: source of truth
             ├─ Milvus: rebuildable dense + BM25 index
             └─ local file volume: original uploads
```

### 3.1 主要模块边界

- `knowledge/domain`：知识库、文档、版本、chunk、任务和引用的领域类型与状态机，不依赖 FastAPI、Docling 或 Milvus。
- `knowledge/application`：上传、重试、重建、删除、检索和 outbox 编排。
- `knowledge/infrastructure`：SQLAlchemy repository、Docling adapter、Embedding adapter、PyMilvus adapter、Celery/Redis adapter 和文件存储 adapter。
- `knowledge/api`：REST、multipart 上传和进度 SSE。
- `knowledge/tools`：LangChain `search_knowledge`、`read_document`、`list_documents` 工具。
- `web/src`：工作台导航、知识库页面、文档页面、进度流和回答来源展示。

领域层只依赖最小协议。Docling、Milvus 或任务队列可以替换，而不改变上层 API 和 Agent 工具契约。

## 4. 文档入库管线

### 4.1 上传与校验

1. API 验证扩展名、MIME、文件签名和大小；默认单文件上限 50 MB。
2. 生成 UUID 文件路径，清洗后的原文件名只用于展示。
3. 流式计算 SHA-256。同一知识库存在相同活动文件哈希时，默认返回 `409 duplicate_document`；显式 `on_duplicate=new_version` 才创建新版本。并发检查使用知识库与哈希组合的 PostgreSQL advisory lock 串行化，避免两个同时上传绕过重复检查。
4. 在一个 PostgreSQL 事务中创建 `document`、`document_version`、`ingestion_job` 和 `outbox_event`。
5. 返回 HTTP 202、`document_id`、`document_version_id` 和 `job_id`。
6. outbox dispatcher 将任务投递到 Redis；即使 Redis 暂时不可用，已提交任务仍可恢复。

### 4.2 Worker 阶段

状态机为：

```text
pending -> parsing -> chunking -> embedding -> indexing -> ready
                 \       \          \           \
                  +-------+----------+-------------> failed
```

瞬时网络、数据库或 Milvus 错误采用指数退避，最多自动重试三次。文件损坏、格式不支持或不可解析属于永久错误，不自动重试。任务幂等键为 `document_version_id + pipeline_version`。

### 4.3 解析与标准化

Docling `DocumentConverter` 处理四类文件并输出统一结构。标准化结果至少包含：

- 原始/规范化 Markdown；
- 标题层级和 breadcrumb；
- 页码、元素序号和字符偏移；
- 段落、列表、表格和代码等结构类型；
- Docling/解析器版本和解析选项。

Parser 只负责提取结构化内容。分块、Embedding 和索引写入属于后续独立阶段，遵循 WeKnora 的职责分离原则。

## 5. 自适应父子分块

### 5.1 父块

- 优先使用 Docling `HierarchicalChunker` 的章节/结构节点形成父块；
- 默认目标约 1200 tokens；过长父块使用 LangChain `RecursiveCharacterTextSplitter` 再分割；
- 父块保存标题路径、页码范围和源元素范围；
- 父块用于最终提供给聊天模型的连贯上下文。

### 5.2 子块

- 使用 Docling `HybridChunker` 在父块内形成检索子块；
- 初始目标约 400 tokens，overlap 约 60 tokens；
- `embedding_text` 在正文前加入标题 breadcrumb，`content` 保持可直接引用的原文；
- 子块用于 Dense/BM25 索引和精确命中；
- 参数保存在知识库的 `chunking_config`，不是代码常量。

### 5.3 降级链

```text
Docling structured chunks
  -> Docling Markdown + Markdown heading split
  -> RecursiveCharacterTextSplitter
  -> failed with stable parse error
```

任何降级均记录所用策略、原因和 parser/chunker 版本。坏文档只影响自身任务。

## 6. PostgreSQL 数据模型

使用 SQLAlchemy 2 和 Alembic。所有主键使用 UUID，时间使用带时区 UTC 时间戳。

### 6.1 `workspaces`

- `id`, `name`, `created_at`, `updated_at`
- 首版迁移创建一个固定工作区。

### 6.2 `knowledge_bases`

- `id`, `workspace_id`, `name`, `description`, `enabled`
- `chunking_config JSONB`, `retrieval_config JSONB`
- `created_at`, `updated_at`
- 同一工作区名称唯一。

### 6.3 `documents`

- `id`, `knowledge_base_id`, `title`, `filename`, `mime_type`
- `status`, `active_version_id`
- `created_at`, `updated_at`, `deleted_at`
- `active_version_id` 是查询和引用的权威活动版本指针。

### 6.4 `document_versions`

- `id`, `document_id`, `version_no`, `file_path`, `sha256`, `file_size`
- `parser_name`, `parser_version`, `pipeline_version`
- `embedding_provider`, `embedding_model`, `embedding_dimension`, `embedding_fingerprint`
- `status`, `error_code`, `error_reference`
- `created_at`, `ready_at`
- `(document_id, version_no)` 唯一；同一知识库活动版本 SHA-256 由上传事务中的 advisory lock 和应用策略保护，允许显式创建内容相同的新版本。

### 6.5 `chunks`

- `id`, `document_version_id`, `parent_chunk_id`, `ordinal`, `kind`
- `content`, `embedding_text`, `heading_path JSONB`
- `page_start`, `page_end`, `char_start`, `char_end`, `token_count`；DOCX/Markdown/TXT 无可靠物理页码时页码字段为 `NULL`，引用退化为标题路径和 chunk 序号；
- `metadata JSONB`, `created_at`
- `parent_chunk_id` 允许 `NULL`；子块必须引用同一文档版本的父块。

### 6.6 `ingestion_jobs`

- `id`, `document_version_id`, `stage`, `status`, `attempt`, `progress`
- `error_code`, `error_reference`, `started_at`, `heartbeat_at`, `finished_at`
- 用于页面状态、重启恢复和审计，不依赖 Redis 队列历史。

### 6.7 `outbox_events`

- `id`, `event_type`, `aggregate_id`, `payload JSONB`
- `status`, `attempt`, `available_at`, `published_at`, `last_error_reference`
- dispatcher 使用 `FOR UPDATE SKIP LOCKED` 并发领取，投递成功后标记 published。

未来 Wiki 迁移新增 `wiki_pages`、`wiki_page_versions` 和 `wiki_source_links`。`wiki_source_links` 直接引用 `document_version_id + chunk_id`；本阶段不预建空 Wiki 表。

## 7. Milvus 索引模型与一致性

首个 collection 为 `knowledge_chunks_v1`。不为每个知识库创建 collection，避免 collection 数量随业务对象增长。

### 7.1 字段

- 主键：`chunk_id`，与 PostgreSQL chunk UUID 相同；
- 过滤：`workspace_id`、`knowledge_base_id`、`document_id`、`document_version_id`、`parent_chunk_id`；
- 文本：`content`，启用 analyzer；
- Dense：`dense_vector`，COSINE + HNSW；
- Sparse：Milvus BM25 Function 生成的 `sparse_vector`，使用 sparse inverted index；
- 展示辅助：`heading`、`page_start`、`page_end`、`ordinal`。

首版只把子块写入 Milvus；父块仅保存在 PostgreSQL，并在子块命中后 hydrate。Milvus 中的正文是 BM25 所需副本，不是业务事实源。命中后必须按 `chunk_id` 从 PostgreSQL 校验活动版本并读取权威内容。

### 7.2 安全版本切换

1. 新 `document_version` 和 chunks 写入 PostgreSQL，旧活动版本继续服务。
2. 新子块写入 Milvus 并做数量、ID 和维度校验。
3. 校验通过后在 PostgreSQL 事务中切换 `documents.active_version_id` 并标记 ready。
4. 检索候选按 PostgreSQL 活动版本二次校验；失效候选不进入模型上下文。
5. 异步清理旧版本 Milvus 记录；失败可重试。

Embedding 模型或维度改变时创建新 collection 版本并执行显式全量重建。首版采用维护式切换，不承诺无停机热迁移；旧 collection 在新 collection 验证通过前保持活动。

## 8. 多级检索策略

### 8.1 查询与范围

Agent 工具接收：`query`、可选 `knowledge_base_ids`、`mode=hybrid|semantic|keyword` 和受限 `limit`。服务端从固定工作区和会话知识范围导出 UUID filter；模型不得提供原始 Milvus 表达式。

默认不额外调用一次 LLM 做 query rewrite。Agent 根据对话上下文形成独立检索语句；未来评测证明必要后，才增加独立 query-rewrite 步骤。

### 8.2 召回与融合

- Dense 召回 Top 30；
- BM25 召回 Top 30；
- Milvus `hybrid_search` 使用 `RRFRanker(k=60)` 融合为 Top 20；
- 过滤非活动版本并从 PostgreSQL hydrate；不足时可在受限次数内过取候选；
- 按父块和规范化内容去重。

RRF 是 Dense/BM25 排名融合，不等同于语义 Cross-Encoder 精排。

### 8.3 可选 Cross-Encoder Rerank

`Reranker` 是独立协议：

- `provider=none`：直接使用 RRF 排名；
- `provider=local_bge`：使用成熟多语言 BGE reranker，将 20 个候选精排到 8 个；
- 模型路径、设备、batch size 和超时与 Chat/Embedding 分开配置。

Reranker 不可用时记录降级并回退 RRF，不中断整个聊天请求。

### 8.4 父子和邻块扩展

- 子块负责命中；
- 命中后读取对应父块；
- 仅在命中跨边界或父块不足时补充前后邻块；
- 默认最多 8 个来源，总上下文预算约 6000 tokens；
- 超预算时按精排顺序裁剪，不截断引用元数据。

没有有效命中时工具返回结构化 `no_relevant_knowledge`，Agent 必须说明未找到依据，不得伪造引用。

## 9. Agent 工具

### 9.1 `search_knowledge`

跨选定知识库执行混合、语义或关键词检索，返回排序片段、标题、可用时的页码、分数、`chunk_id` 和 `document_id`。这是默认主力工具。

### 9.2 `read_document`

根据 `document_id` 或 `chunk_id` 读取文档父块、邻块或分页内容，解决检索片段上下文不足的问题。

### 9.3 `list_documents`

按知识库、标题或文件名查询文档，解决“那份叫 X 的文件”类需求。

工具均为只读，结果有长度上限并标注截断。上传、删除、重试、重建不作为 Agent 工具暴露。`LangChainAgentStream` 通过构造函数注入工具集合，模型策略仍只负责创建 `BaseChatModel`。

## 10. HTTP 与 SSE 契约

### 10.1 知识管理 REST

```text
POST   /api/v1/knowledge-bases
GET    /api/v1/knowledge-bases
GET    /api/v1/knowledge-bases/{kb_id}
PATCH  /api/v1/knowledge-bases/{kb_id}
DELETE /api/v1/knowledge-bases/{kb_id}

GET    /api/v1/knowledge-bases/{kb_id}/documents
POST   /api/v1/knowledge-bases/{kb_id}/documents       multipart, returns 202
GET    /api/v1/documents/{document_id}
DELETE /api/v1/documents/{document_id}
POST   /api/v1/documents/{document_id}/retry
POST   /api/v1/documents/{document_id}/reindex

GET    /api/v1/knowledge/events/stream                 workspace progress SSE
```

删除知识库在存在文档时要求显式 `confirm=true`，执行异步级联清理。删除状态为 `deleting -> deleted`，所有清理动作可重复执行。

### 10.2 Chat 请求扩展

```json
{
  "conversation_id": "uuid",
  "message": "用户问题",
  "knowledge_scope": {
    "mode": "all_enabled",
    "knowledge_base_ids": []
  }
}
```

`mode` 为 `all_enabled` 或 `selected`。`selected` 必须提供至少一个存在且启用的知识库 ID。前端每轮携带当前会话范围；因为长期会话持久化不在本阶段，浏览器刷新或进程重启后的跨会话恢复不作保证。

### 10.3 Chat SSE 扩展

保留 `message`、`done`、`error`，新增：

- `status`：`retrieving`、`reading_sources`、`generating` 等稳定阶段；
- `sources`：在检索完成后、相关模型文本前发送引用清单。

来源项包含 `citation_id`、`chunk_id`、`document_id`、`document_version_id`、标题、文件名、可空页码范围、标题路径、短 excerpt 和排序信息。工具上下文使用相同 `citation_id`，提示模型以 `[1]` 等标记引用。前端即使模型未产生标记，也可以展示来源抽屉，但不会把“检索到”误标为“模型明确引用”。

## 11. 前端交互

### 11.1 全局工作台

- 桌面端左侧导航：对话、知识库、设置；
- 移动端折叠抽屉；
- 当前简洁浅色视觉令牌继续沿用。

### 11.2 知识库列表

- 名称搜索、文档数量、处理数量和启用状态；
- 新建、编辑、启停和删除；
- 停用知识库立即从默认 `all_enabled` 范围排除。

### 11.3 知识库详情

- 文档列表、状态筛选和搜索；
- 拖拽/选择多文件上传；
- 显示 parsing、chunking、embedding、indexing 的阶段和进度；
- 失败项显示用户安全错误与重试；
- 操作菜单提供删除和重建；
- 检索设置保存 chunking/retrieval 配置，修改后不自动重建既有文档，页面明确提示需要 reindex。

### 11.4 对话页面

- 默认“全部启用知识库”；
- 可按当前会话选择一个或多个知识库；
- 消息展示检索状态、引用标记和可展开来源抽屉；
- 来源显示文档、可用时的页码、标题路径和 excerpt，不向浏览器暴露真实存储路径。

## 12. 配置与部署

配置继续使用 Pydantic Settings 和 `AGENT_` 前缀，Secret 使用 `SecretStr`。新增配置分组包括：

```text
AGENT_DATABASE_URL
AGENT_REDIS_URL
AGENT_STORAGE_ROOT
AGENT_MAX_UPLOAD_BYTES

AGENT_MILVUS_URI
AGENT_MILVUS_TOKEN                optional
AGENT_MILVUS_COLLECTION

AGENT_EMBEDDING_PROVIDER          openai
AGENT_EMBEDDING_BASE_URL
AGENT_EMBEDDING_API_KEY
AGENT_EMBEDDING_MODEL
AGENT_EMBEDDING_DIMENSION
AGENT_EMBEDDING_BATCH_SIZE

AGENT_RERANK_PROVIDER             none | local_bge
AGENT_RERANK_MODEL
AGENT_RERANK_DEVICE
AGENT_RERANK_BATCH_SIZE
```

Docker Compose 包含 API、Worker、PostgreSQL、Redis、Milvus Standalone 及 Milvus 内部依赖 etcd/MinIO。应用文件卷与 Milvus 内部对象存储分开。

启动时必须校验：

- Alembic schema 为预期版本；
- PostgreSQL、Redis 和 Milvus 可连接；
- Milvus collection 字段、BM25 Function 和索引存在；
- 配置的 Embedding 维度与 active collection 一致；
- 存储根目录可读写且不是仓库根或用户主目录。

不匹配时启动失败，不静默删除或重建已有 collection。

## 13. 安全、错误与隐私

- 文件名不参与路径拼接；所有路径通过受控根目录和 UUID 解析；
- Worker 容器设置 CPU、内存、任务时间和并发限制；
- 文档内容视为不可信数据，用清晰边界包裹工具结果；系统提示禁止遵循文档中的指令；
- Agent 只有只读知识工具，降低检索内容诱导外部写操作的风险；
- ORM 和参数绑定用于 SQL；Milvus filter 仅由服务端 UUID/枚举构造；
- 外部错误返回稳定错误码、用户安全信息和 correlation id；内部异常进入结构化日志；
- API Key、Embedding、完整文档正文、磁盘路径和数据库凭据不得进入对外响应或默认日志；
- 临时上传文件在成功移动或失败后清理；
- 删除 API 明确确认范围，异步清理状态可恢复。

## 14. 可观测性与恢复

每个 HTTP 请求、文档版本、任务和 Milvus 批次带 correlation id。结构化记录：

- 各阶段开始、完成与耗时；
- 输入文件大小、解析元素数、父/子 chunk 数；
- Embedding 批次数和失败批次；
- Milvus 插入、校验和删除数量；
- 自动/手动重试原因与次数；
- 检索模式、候选数量、RRF/Rerank 数量和降级原因；
- 不记录完整正文和向量。

恢复任务扫描 heartbeat 超时的 `ingestion_jobs`。若幂等键尚未完成，则重新投递；超过重试上限后标记 failed。Milvus 写入成功而 PostgreSQL 未切换的记录不属于活动版本，可由清理任务删除。

## 15. 测试策略

### 15.1 单元测试

- 配置、密钥脱敏和启动校验；
- 文件扩展名/MIME/签名/大小和路径安全；
- Docling adapter 输出映射；
- 父子关系、标题 breadcrumb、页码和降级链；
- 状态机、幂等键、outbox 和错误分类；
- Dense/BM25 候选融合、活动版本过滤、父块扩展和上下文预算；
- 工具 schema、截断与 Prompt Injection 边界；
- SSE `status/sources/message/done/error` 解析。

### 15.2 集成测试

使用真实 PostgreSQL、Redis 和 Milvus 容器验证：

- 上传、outbox 投递、Worker 消费和 ready；
- 相同哈希冲突及显式新版本；
- 混合检索、KB filter 和来源 hydrate；
- 新版本切换期间旧版本持续可读；
- Milvus/Redis 短暂故障后的恢复；
- 删除补偿和重复清理；
- API/Worker 重启后的超时任务恢复。

外部 Chat、Embedding 和 Reranker 使用确定性替身，不调用真实付费服务。

### 15.3 Parser fixtures

四种格式各包含：中文正文、标题结构、表格/列表、页码可用场景和损坏文件。PDF 额外包含文本 PDF 与小型扫描 PDF。

### 15.4 Agent 与前端测试

- 应检索的问题调用工具；常识/闲聊不强制调用；
- 无证据时不生成来源；
- 知识库 CRUD、上传、进度、失败重试和删除确认；
- 会话级知识范围；
- 引用标记、来源抽屉和移动端导航；
- E2E：四种格式上传 -> ready -> 对话命中 -> 正确文件及页码或标题路径引用。

### 15.5 检索评测

维护固定评测集：问题、预期文档、预期 chunk 和不可回答问题。记录 Recall@8、MRR、引用正确率和无答案误答率。首个基线要求预期文档 Recall@8 不低于 90%；后续参数变化必须与基线对比，不凭主观感受调整。

## 16. 验收标准

1. 四种允许格式可以上传、异步处理并达到 ready；损坏文件进入 failed 且不会拖垮 API。
2. PostgreSQL 是状态和正文事实源；清空 Milvus 后可以从活动版本重建索引。
3. Dense + BM25 + RRF 路径在真实 Milvus 集成测试中运行，不以 mock 代替核心检索证明。
4. 父子块关系、页码和标题路径可以从上传文件追溯至最终引用。
5. Agent 可以自主 search/read/list，并遵守会话知识范围。
6. 无检索命中或检索降级时不伪造来源。
7. 页面可以管理知识库和文档、查看实时状态、重试失败任务并展开回答来源。
8. Redis/API/Worker 的受控重启不会永久丢失已提交任务。
9. 重建文档版本失败时旧活动版本继续可检索。
10. 新增测试及现有后端、前端、SSE 回归测试全部通过。
11. Ruff、format check、mypy、前端 lint/type-check/build 和适用 E2E 全部通过。
12. 固定检索评测集达到 Recall@8 >= 90%，并保存评测结果。

## 17. Wiki 下一阶段约束

Wiki 是从原始知识生成并持续维护的衍生知识层，不是本阶段 RAG 的简单开关。下一阶段开始前必须单独编写并确认设计与实施计划，至少覆盖：

- 文档库与 Wiki 库的 source -> synthesis 关系；
- Wiki 页面树、slug、双向链接和主题合并；
- `wiki_pages`、`wiki_page_versions`、`wiki_source_links`；
- 每条 Wiki 结论引用当前文档版本和 chunk；
- 新增/更新/删除原文后的增量刷新与冲突策略；
- 人工编辑、版本 diff、回滚和引用失效提示；
- Wiki 搜索与基础 RAG 检索的路由规则；
- 可选知识图谱的独立收益证明，不默认引入 Neo4j。

本阶段实现不得破坏上述演进路径，但不得提前实现未确认的 Wiki 功能。

## 18. 已决定事项

- 不调用 WeKnora；只参考其架构原则。
- 采用模块化单体 + 独立 Worker，不采用知识微服务。
- PostgreSQL + Milvus + Redis；不增加另一套全文数据库。
- Milvus 同时承担 Dense 与内置 BM25；PostgreSQL 不维护第二套全文索引。
- Docling 统一解析；LangChain splitter 只作为结构过长和降级工具。
- 采用父子块、多路召回、RRF 和可选 Cross-Encoder，而非最简向量 RAG。
- 默认检索全部启用知识库，用户可按会话缩小范围。
- 单工作区无登录，数据模型预留 `workspace_id`。
- 前端采用左侧工作台布局。
- 本阶段交付高级 RAG；Wiki 后续单独设计、计划和实施。
- 设计不存在待用户决定项；范围变化必须先更新本设计或新增后续设计。

## 19. 参考资料

- WeKnora 架构与文档管线：<https://github.com/Tencent/WeKnora/blob/main/website-docs/02-architecture/03-document-pipeline.md>
- WeKnora 自适应与父子分块：<https://github.com/Tencent/WeKnora/blob/main/docs/CHUNKING.md>
- WeKnora 知识域与 Wiki：<https://github.com/Tencent/WeKnora/blob/main/website-docs/01-getting-started/01-introduction.md>
- Docling 支持格式：<https://docling-project.github.io/docling/usage/supported_formats/>
- Docling Chunking：<https://docling-project.github.io/docling/concepts/chunking/>
- Milvus Full Text Search：<https://milvus.io/docs/full-text-search.md>
- Milvus RRF Ranker：<https://milvus.io/docs/rrf-ranker.md>
