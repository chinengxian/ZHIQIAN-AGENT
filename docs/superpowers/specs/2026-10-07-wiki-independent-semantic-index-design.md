# Wiki 独立语义索引设计

- 日期：2026-10-07
- 状态：confirmed
- 适用范围：REQ-006 后续路线 C
- 前置基线：Wiki v2 可读摘要、来源驱动混合检索、文档正文深链已经可用

## 1. 背景与问题

当前 Wiki 已经完成页面生成、摘要分段、主题聚合、来源追溯、人工编辑、版本审核、回滚、额度控制和文档正文定位。但检索仍以 PostgreSQL 页面元数据匹配和原文 chunk 召回后反向映射 Wiki 页面为主，缺少针对 Wiki 摘要段落和事实本身的独立语义索引。

固定评测结果为：

- Page Recall@5：0.3537；
- Claim Recall@8：0.5196；
- Citation Precision：0.8395；
- 同义查询一致性：0.6333；
- 无答案误命中率：1.0；
- 待核实和失效来源泄漏均为 0。

实际运行环境中，“不存在的宇宙密码”仍会返回 VPN、账号安全和身份访问控制内容，且接口不会标记为降级。这说明页面和引用闭环已经成立，但检索质量闭环尚未成立。

## 2. 目标

本阶段建立独立、可重建、可评测的 Wiki 语义索引，使搜索和 Agent 能够：

1. 直接检索摘要段落、摘要事实和主题事实；
2. 同时利用 Dense、BM25、标题和别名信号；
3. 在结果不相关时明确返回无答案；
4. 只返回当前页面版本、可信 Claim 和有效来源；
5. 支持稳定分页、跨页面事实去重和可解释命中原因；
6. 在页面内展示精确原文摘录，并能跳转正文高亮；
7. 在索引失败、重建和 Embedding 升级时安全恢复或回滚；
8. 通过固定质量门后再取代现有 Wiki 检索主链路。

## 3. 非目标

本阶段不包含：

- GraphRAG、知识图谱或实体关系推理；
- 跨知识库 Wiki 页面；
- 自动把聊天结论写回 Wiki；
- 对人工编辑的无来源内容进行模型补证；
- 重新生成现有摘要页面；
- PDF 或 Office 原生阅读器；
- 使用大模型判断每次查询是否有答案。

无答案判定使用可评测的确定性相关性策略，避免额外模型费用和不可复现行为。

## 4. 方案选择

已选择独立 Milvus Wiki collection，拒绝以下替代方案作为主路径：

- 复用原文 `knowledge_chunks` collection：会混合原文与 Wiki 生命周期、语义和调参边界；
- 仅增强 PostgreSQL 模糊搜索：无法解决中文同义表达、摘要事实语义召回和无答案抑制问题。

PostgreSQL 继续保存页面、版本、Claim、来源和索引状态，是唯一事实源；Milvus 只保存可重建的检索副本。

## 5. 架构

### 5.1 组件边界

新增组件职责如下：

- `MilvusWikiIndex`：创建、写入、替换、查询和删除独立 Wiki collection；
- `WikiIndexService`：把当前页面版本转换为索引单元，生成 Embedding，并驱动索引状态；
- `WikiIndependentRetrievalService`：执行 Wiki Dense、BM25 和元数据融合检索；
- `WikiRelevancePolicy`：统一执行阈值、拒答和降级规则；
- Wiki 索引 Worker：消费 Outbox 事件、恢复超时任务和清理旧索引；
- PostgreSQL 页面服务：继续负责版本、来源可信性和最终结果复核。

现有 `WikiRetrievalService` 在迁移期保留为 `legacy` 后端，独立检索实现作为 `independent` 后端接入同一 API 契约。

### 5.2 Wiki 知识单元

每个可检索知识单元对应一个 Claim，支持三种类型：

- `summary_section`：面向人阅读的摘要段落；
- `summary_fact`：单篇文档的原子事实；
- `topic_fact`：跨文档主题中的原子事实。

人工 `manual` Claim、失效来源 Claim 和 `needs_review` Claim 不进入索引。

Embedding 文本由以下内容确定性组成：

```text
页面标题
页面类型
主题别名
摘要段落标题（如有）
知识单元正文
来源标题路径
```

不得把数据库 ID、内部状态或错误信息拼入 Embedding 文本。

### 5.3 独立 collection schema

默认逻辑名称为 `wiki_units`，实际物理 collection 使用带版本名称，例如 `wiki_units_v1`。字段包括：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `unit_id` | VARCHAR 主键 | 使用 Claim ID，单版本内稳定唯一 |
| `knowledge_base_id` | VARCHAR | 搜索范围过滤 |
| `page_id` | VARCHAR | 页面归组 |
| `page_version_id` | VARCHAR | 当前版本复核和清理 |
| `claim_id` | VARCHAR | PostgreSQL Claim 定位 |
| `unit_type` | VARCHAR | `summary_section`、`summary_fact` 或 `topic_fact` |
| `content` | VARCHAR | BM25 输入及调试展示文本 |
| `dense_vector` | FLOAT_VECTOR | Wiki 知识单元语义向量 |
| `sparse_vector` | SPARSE_FLOAT_VECTOR | 由 Milvus BM25 函数生成 |

collection 建立 Dense HNSW 和 Sparse BM25 两个索引。所有 ID 由 PostgreSQL UUID 转换，不接受用户或模型拼接 Milvus 过滤表达式。

## 6. PostgreSQL 数据契约

`wiki_page_versions` 增加：

- `index_state`：`not_required / pending / indexing / ready / failed`；
- `embedding_fingerprint`：成功索引时使用的配置指纹；
- `indexed_at`：成功完成时间；
- `index_error_code`：失败时的稳定错误码。

自动生成的已发布版本和包含可信来源的回滚版本进入 `pending`。人工编辑版本使用 `not_required`，因为无来源人工内容不得进入 Agent 可信检索。待审核自动版本在审核通过前保持 `not_required`，通过后进入 `pending`。

新增 Wiki 索引任务或复用现有任务模型时，必须保存：

- `page_id`、`page_version_id`；
- `status`、`stage`、`progress`；
- `attempt_count`、`max_attempts`；
- `heartbeat_at`、`next_retry_at`；
- `embedding_fingerprint`；
- `error_code`、`error_reference`；
- 唯一幂等键。

幂等键格式为：

```text
wiki-index:{page_version_id}:{embedding_fingerprint}
```

## 7. 发布、索引与版本可见性

页面版本发布事务同时写入页面数据、索引状态和 Outbox 事件。索引不在 API 请求事务中同步完成。

索引流程为：

1. Worker 锁定页面版本，只允许 `pending → indexing`；
2. 批量读取该版本可信 Claim、来源和 chunk；
3. 生成索引文本和向量；
4. 按 `page_version_id` 替换 Milvus 数据；
5. flush 后核对实际数量；
6. 再次锁定页面，确认版本仍然有效；
7. 标记 `ready`，记录配置指纹和时间；
8. 异步删除旧页面版本的索引单元。

浏览器可以读取 `pending/indexing` 的当前页面，但搜索只接受 `index_state=ready` 的当前版本。Milvus 命中后必须回 PostgreSQL 验证：

```text
page.current_version_id == hit.page_version_id
version.index_state == ready
claim.trust_state == verified
source.document_version_id == document.active_version_id
chunk 内容哈希与来源记录一致
```

只要一个条件不满足，该知识单元就不可见。新版本尚未就绪时不能使用旧版本索引冒充当前结果。

## 8. 索引失败、恢复与并发

Embedding 或 Milvus 瞬时故障采用指数退避。达到最大次数后版本标记为 `failed`，页面仍可浏览，但不参与 Wiki 搜索。前端提供索引重试入口。

周期恢复任务重新投递长时间停留在 `indexing` 且心跳超时的任务。Worker 完成写入前若发现页面版本已经被替代，应删除刚写入的数据，且不得把版本标记为 `ready`。

知识库级全量重建使用 PostgreSQL advisory lock。同一知识库同时只允许一个全量回填或重建任务；单页索引继续按页面版本幂等执行。

文档更新或删除时，PostgreSQL 立即将相关 Claim 标记为 `needs_review`。Milvus 删除可以异步完成，因为搜索复核会立即阻止失效结果泄漏。

## 9. Embedding 配置升级

Embedding 模型、维度或文本组织规则发生变化时创建新的物理 collection：

```text
wiki_units_v1
  → 全量回填与评测
wiki_units_v2
  → collection alias 原子切换
保留 v1 观察并支持回滚
```

配置指纹至少包含 provider、模型、维度和 Wiki 索引文本协议版本。不同指纹的数据不能混用。旧 collection 只有在新版本通过质量门和稳定观察后才删除。

## 10. 检索与相关性策略

### 10.1 候选通道

每次查询并行执行：

1. Dense Wiki 知识单元召回；
2. BM25 Wiki 知识单元召回；
3. PostgreSQL 标题和别名精确、前缀匹配。

每路获取扩大候选池，再使用 RRF 融合。融合候选必须通过 PostgreSQL 当前版本和来源校验，不能直接返回 Milvus 数据。

### 10.2 可解释评分

候选保留：

- Dense cosine score；
- BM25 score 和排名；
- 标题、别名精确命中标记；
- RRF 分数；
- 可选 Cross-Encoder 分数；
- 命中通道。

新增 Wiki 专用评分协议：

```python
async def score(query: str, texts: list[str]) -> list[float]: ...
```

现有原文 RAG 的只返回排序接口保持兼容。Wiki 使用实际相关性分数执行拒答。

### 10.3 无答案判定

`WikiRelevancePolicy` 集中管理阈值：

- 标题或别名精确命中可进入候选；
- 语义查询必须达到经评测校准的 Dense 最低分；
- 启用 Cross-Encoder 时还必须达到 Reranker 最低分；
- 关键词模式必须达到最低词项覆盖率；
- 所有候选低于阈值时返回无答案。

阈值与 Embedding/Reranker 指纹绑定。更换模型后必须重新评测，不能沿用旧阈值。

无答案响应示例：

```json
{
  "pages": [],
  "no_answer": true,
  "no_answer_reason": "below_relevance_threshold",
  "degraded": false
}
```

“没有相关知识”和“检索基础设施故障”使用不同状态与错误码。

## 11. 去重、预算与分页

事实指纹由规范化文本、排序后的来源 chunk ID 和活动文档版本 ID 组成。相同事实只展示一次，并返回 `also_in_pages`。

主页面选择规则为：

1. 精确命中标题或别名时优先命中页面；
2. 多文档来源优先主题页；
3. 单文档来源优先摘要页；
4. 最后按相关性分数和稳定 ID 排序。

API 使用不透明搜索 cursor，绑定查询指纹、当前 collection 版本、最后分数和稳定 ID。查询或 collection 版本变化时返回 `wiki_search_cursor_expired`。

首屏默认 5 个页面和 12 条知识单元，但通过 `next_cursor` 和页面内事实分页访问剩余结果。固定数量只限制单次响应，不得永久隐藏被截断结果。

## 12. 来源摘录与正文高亮

摘要段落和事实的来源响应增加：

- `parent_chunk_id`；
- `excerpt`；
- `focus_start`、`focus_end`；
- 当前有效性和失效原因。

这些字段从 PostgreSQL chunk 动态生成，不重复存储在 Wiki 表中。服务层批量加载来源和 chunk，避免逐 Claim、逐来源查询。

Wiki 页面默认展示可读摘要。来源展开后显示文档、版本、标题路径、页码和精确摘录，并提供“查看原文并高亮”和“打开文档摘要”。

正文深链继续使用：

```text
/knowledge/{knowledge_base_id}/documents/{document_id}
  ?version={document_version_id}
  &chunk={chunk_id}
```

文档详情页使用 `focus_start/focus_end` 拆分纯文本并渲染 `<mark>`，不得使用 `v-html`。页面加载后自动滚动到目标父块；刷新深链仍能恢复版本和高亮。

## 13. 前端状态

前端需要明确展示：

- 正常结果、无答案、搜索降级和搜索失败；
- 索引 `pending/indexing/failed`；
- 加载更多页面和事实；
- 命中原因和相关性来源；
- 来源有效、历史失效或正文已清理；
- 索引失败后的重试操作。

搜索导航展示页面和最佳知识单元，不再平铺重复事实。所有文档内容、标题和摘录按纯文本渲染；来源按钮具备可访问状态，桌面和移动端不得出现横向溢出。

## 14. 配置与切换

新增配置：

```text
wiki_index_enabled
wiki_search_backend = legacy | shadow | independent
wiki_milvus_collection_alias
wiki_dense_threshold
wiki_rerank_threshold
wiki_keyword_coverage_threshold
```

默认先启用 `shadow`。Shadow 模式同时执行旧、新检索，但只向用户返回旧结果，并记录结果差异、耗时和质量信号。达到质量门后按知识库切到 `independent`。

## 15. 测试与验收

### 15.1 单元测试

覆盖 collection schema、索引单元构造、三路融合、阈值、拒答、去重、cursor、索引任务状态、来源摘录和安全高亮。

### 15.2 真实集成测试

使用独立 PostgreSQL 数据库和唯一 Milvus collection，覆盖：

- 发布、Outbox、索引和数量核对；
- 文档更新后旧来源立即不可见；
- 新版本索引期间不泄漏旧版本；
- 人工编辑不进入 Agent 检索；
- 待审核版本通过后才建立索引；
- 回滚、失败重试、超时恢复和并发重建；
- Embedding 指纹变化和 collection 切换。

### 15.3 浏览器 E2E

桌面和移动端覆盖同义查询、无答案、分页、来源展开、正文高亮、失效来源、索引中、索引失败与重试。

### 15.4 质量门

- Page Recall@5 ≥ 0.90；
- Claim Recall@8 ≥ 0.85；
- Citation Precision ≥ 0.95；
- 同义查询一致性 ≥ 0.85；
- 无答案误命中率 ≤ 0.05；
- 待核实、失效来源和旧页面版本泄漏均为 0。

评测输出机器可读 JSON 并进入 CI。任一核心指标未达标时不得切换到 `independent`。

### 15.5 性能目标

- 热启动搜索 P95 ≤ 1.5 秒；
- 未启用 Cross-Encoder 时 P95 ≤ 800 毫秒；
- 页面发布后索引最终可见时间 P95 ≤ 5 分钟；
- 1 万 Wiki 知识单元下分页稳定；
- 同一页面版本重复执行索引不产生重复单元。

## 16. 可观测性

记录查询指纹，不记录完整问题或正文。指标包括：

- 搜索耗时、候选量和无答案率；
- 阈值过滤率；
- Dense、BM25、别名命中率；
- Reranker 降级率；
- 索引积压及 `pending/indexing/failed` 数量；
- 来源点击率和 cursor 过期率。

## 17. 上线与历史回填

上线顺序：

1. 部署向后兼容的数据库字段和独立 collection；
2. 从现有已发布 Claim 和摘要段落回填索引，不调用模型、不增加 Wiki token；
3. 开启 Shadow 模式；
4. 运行固定质量评测；
5. 按知识库小范围切到 `independent`；
6. 全量切换并观察；
7. 稳定后单独清理旧检索主路径。

当前已有 8 个摘要页和 7 个主题页，可以直接从 PostgreSQL 回填。

## 18. 回滚

发生质量或稳定性问题时：

- 把 `wiki_search_backend` 切回 `legacy`，或临时关闭 Agent Wiki 搜索；
- 不回滚 PostgreSQL 页面、版本和来源数据；
- 保留新 collection 用于排查；
- Embedding 升级失败时把 collection alias 切回上一版本；
- 数据库新增字段保持向后兼容；
- 修复后重新执行幂等回填。

旧 collection 和旧检索代码只能在新链路通过质量门并完成稳定观察后删除。

## 19. 验收结论定义

只有同时满足以下条件，Wiki 检索闭环才可标记为完成：

1. 独立索引完成历史回填并覆盖所有当前可信页面版本；
2. 固定质量门全部通过；
3. “不存在的宇宙密码”等无答案查询返回空结果；
4. 旧版本、待核实和失效来源零泄漏；
5. 来源摘录和正文高亮在真实浏览器闭环通过；
6. 索引失败、重试、重建和回滚路径均有真实证据；
7. Shadow 与独立模式的切换不修改页面权威数据。
