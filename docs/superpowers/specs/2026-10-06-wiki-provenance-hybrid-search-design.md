# Wiki 来源驱动混合检索改造设计

- 日期：2026-10-06
- 状态：approved（用户于 2026-10-06 确认）
- 当前实施路线：B，来源驱动混合检索
- 下一阶段目标：C，独立 Wiki 语义索引
- 关联需求：`docs/requirements/REQ-006.md`
- 范围依据：用户于 2026-10-06 确认“先按 B 进行改造，并将 C 定为下一步的目标”

## 目标

把现有 Wiki 从“ILIKE 命中页面后返回固定前 8 条 claim”改造成可用于 Agent 问答的来源驱动混合检索：

1. 同义词、语义相近但词面不同的问题可通过现有 Dense + BM25 原文索引召回。
2. 标题、slug、页面摘要和主题别名仍可提供精确、低延迟的页面召回。
3. 原文 chunk 命中可通过 `wiki_claim_sources` 反向定位可信 claim 与 Wiki 页面。
4. 页面与 claim 都按问题相关性排序，数量限制只在排序、去重之后按输出预算执行。
5. 摘要和主题中的事实性内容逐条关联真实 chunk；无法验证的内容不得进入 Agent 可信结果。
6. 搜索、降级、引用失效和截断行为可观测，并由固定 Wiki 问答集验收。

## 非目标

- 本阶段不建立独立的 Wiki Milvus collection，不为 Wiki 页面或 claim 单独写向量。
- 本阶段不引入 Neo4j、GraphRAG、实体关系推理或跨知识库 Wiki。
- 本阶段不扩大人工编辑内容的信任边界；没有有效来源的人工内容仍为 `needs_review`。
- 本阶段不把查询改写作为语义召回的必要条件；查询改写只能是多轮语境增强，不能替代 Dense 检索。
- 不改变现有原文 `search_knowledge` 的引用语义，Wiki 结果仍必须落回原始 chunk citation。

## 当前缺陷

### 页面召回

- `WikiService.search_pages()` 与 `agent_pages()` 使用 `%query%` 的 `ILIKE`，按标题排序，没有相关度分数。
- Agent 跨知识库查询逐库取结果后直接截断，结果受知识库遍历顺序影响。
- 搜索响应没有命中字段、命中 claim、分数、降级状态或截断原因。

### claim 选择

- claim 按 `claim_key` 字符串排序后固定取前 8 条，查询内容不参与选择。
- `wiki_read_page` 同样固定取前 8 条，长页面无法分页或按问题定位。
- 页面、claim、来源和来源有效性逐条读取，存在明显 N+1 查询。

### 生成与来源

- 摘要生成会遍历所有 child chunk，但每个输入片段被截断，层级汇总后丢失 chunk 归属。
- 当前 verified claim 实际是每个 child chunk 的前 350 字，不是带来源的原子结论。
- 主题仅按规范化后的同名字符串合并，主题页复制摘要页全部 claim，没有主题级筛选。

## 总体架构

```text
用户问题
  ├─ 页面元数据召回
  │    标题 / slug / 摘要 / 主题别名
  └─ 原文混合召回
       现有 Milvus Dense + BM25 → child chunk hits
                    ↓
       wiki_claim_sources 反向映射可信 claim / 页面
                    ↓
        页面候选按 RRF 融合并全局排序
                    ↓
       页面 rerank（可选模型，失败可降级）
                    ↓
       批量加载候选页面的当前 verified claims
                    ↓
       claim + 支持 chunk 联合 rerank
                    ↓
       MMR 去冗 + 页面多样性 + token 预算
                    ↓
       Agent 结果：Wiki 导航信息 + 原文 citation
```

新增 `WikiRetrievalService` 作为唯一的 Agent Wiki 检索应用层。`WikiService` 继续负责配置、页面、版本、编辑与来源有效性，不再同时承担排序算法。原文检索通过现有 `KnowledgeRetrievalService` 或更窄的检索端口复用 Milvus、embedder 和 reranker。

## 检索流程

### 1. 输入与范围

- 拒绝空白查询，查询最大长度继续为 200 字符。
- 作用域使用当前会话解析后的 `knowledge_base_ids`，只接受启用且 Wiki 开启的知识库。
- 所有知识库一次性进入候选召回与全局排序，不允许逐库截断后拼接。
- 查询规范化只处理 Unicode NFKC、大小写、空白与标点；多轮查询改写后续可通过独立端口注入。

### 2. 页面元数据召回

PostgreSQL 候选覆盖当前发布版本的：

- `WikiPage.title`
- `WikiPage.slug`
- `WikiPageVersion.summary`
- 页面别名
- 主题规范名

首期使用 PostgreSQL `pg_trgm` 生成元数据候选：迁移执行 `CREATE EXTENSION IF NOT EXISTS pg_trgm`，并为页面标题、版本摘要和规范化别名建立 GIN trigram 索引。部署前检查目标数据库账号是否具有创建扩展的权限；不满足时迁移必须失败并给出操作说明，不能静默退回未索引的全表 `ILIKE`。

标题或别名规范化后的完全匹配优先，其次使用 `similarity()` 分数召回标题、别名和摘要；slug 使用规范化后的精确或前缀匹配。各字段保留独立命中信号供 RRF 与最终解释使用。`ILIKE` 只允许作为小候选集合内的展示高亮判断，不能再承担全表召回或最终排序。

### 3. 来源 chunk 语义召回

- 调用现有原文混合检索，以扩大后的候选深度获取 child chunk hit，默认候选池 40，上限 100。
- 保留 Milvus 返回的 rank 与 score，不在这一层截断为最终 8 条。
- 通过 `wiki_claim_sources.chunk_id` 反查当前页面版本的 claim；旧版本、失效来源和 `needs_review` claim 不进入可信候选。
- 同一 claim 的多个命中来源合并，保留最高分与来源命中列表。
- 同一页面的候选分由最相关 claim、命中数量和元数据召回信号共同构成，不因 claim 数量多而无上限累加。

为支持反向查询，数据库增加 `wiki_claim_sources(chunk_id)` 索引；涉及当前页的连接必须约束 `wiki_pages.current_version_id = wiki_claims.page_version_id`。

### 4. 融合与重排

- 页面元数据通道和来源 chunk 通道用 RRF 融合，不直接比较不同通道的原始分数。
- 标题或别名完全匹配作为受控加权信号，但不能绕过来源有效性约束。
- 有 reranker 时，页面 passage 使用“标题 + 别名 + 页面摘要 + 最相关 claim”，不把整页 Markdown 发送给模型。
- reranker 失败时按 RRF 顺序降级，响应 `degraded=true` 和稳定原因码，不把整个搜索伪装成不可用。
- 页面选定后，对候选 claim 使用“claim 文本 + 命中来源的父块上下文”二次重排。
- 最终使用 MMR 降低重复 claim；至少保留一个最高相关结果，不强制每页返回相同数量。

### 5. 输出预算

- `limit` 表示最终页面上限，不再代表数据库最先返回的页面数。
- claim 不设固定“每页前 8 条”；按相关度、来源有效性、去冗和总 token/字符预算选择。
- 默认搜索结果最多 5 个页面、总计最多 12 个 claim、总字符预算 12,000；三个限制任一达到即停止。
- 每页默认最多 4 个 claim，仅作为多样性约束；若只有一个高度相关页面，可以使用剩余预算。
- 响应返回 `omitted_claim_count` 与 `truncation_reason`，让 Agent 知道是否需要继续读取页面。

## 摘要与主题的来源协议

### 结构化生成结果

`WikiGeneration` 改为保留来源编号：

```json
{
  "summary_claims": [
    {
      "text": "普通员工自 2026-07-01 起不再按固定周期强制改密。",
      "source_indices": [3, 8]
    }
  ],
  "topics": [
    {
      "name": "身份认证",
      "aliases": ["MFA", "多因素认证", "双因素校验"],
      "source_indices": [2, 3, 8]
    }
  ]
}
```

- `source_indices` 引用当前批次中带稳定编号的 child chunk，模型不得输出数据库 ID。
- 长文档 Map 阶段按批次生成原子 claim、主题候选和来源编号。
- Reduce 阶段只允许组合 Map 阶段已有 claim，并合并其来源集合；不能生成没有来源的新事实。
- 服务端拒绝越界编号、空来源和未知编号，保存前再次校验活动版本与内容哈希。
- 生成的摘要正文由 verified summary claims 确定性组装；无来源的补充文字只可作为待核实说明。

### 主题身份

- 主题候选新增规范名和别名；先执行 NFKC、大小写、空白和标点归一化。
- 规范名或别名精确命中时复用主题身份。
- 语义相近但未精确命中的候选，本阶段保守地保留为不同主题；路线 C 再引入向量候选与歧义判定。
- 坚持“相关不等于同一主题”；共享一个别名不足以自动合并两个不同主题。
- 主题页只复制或综合明确引用该主题的 verified claims，不再复制摘要页全部 claim。

## 数据模型

本阶段新增：

1. `wiki_page_aliases`
   - `page_id`
   - `alias`
   - `normalized_alias`
   - `(page_id, normalized_alias)` 唯一
   - `(normalized_alias)` 查询索引
2. `WikiClaim.position`
   - 保存摘要或主题页内的稳定展示顺序，不能再依赖字符串 `claim_key` 排序。
3. `WikiClaim.claim_type`
   - `summary_fact`、`topic_fact`、`source_excerpt`、`manual`
   - 旧数据迁移为 `source_excerpt`，避免把旧截断片段冒充新的摘要事实。
4. 索引
   - `wiki_claim_sources(chunk_id)`
   - `wiki_claim_sources(document_version_id, chunk_kind, chunk_ordinal)`
   - 当前页面和 claim 连接所需索引按真实查询计划补齐。

迁移只增加字段、表和索引，不删除现有 claim。上线后旧页面仍可浏览；只有重新生成或后台重建完成的页面才拥有新的 grounded summary claims。

## API 与 Agent 契约

### Wiki 搜索 API

`GET /api/v1/knowledge-bases/{id}/wiki/search` 保持兼容路径，响应扩展为：

```json
{
  "pages": [
    {
      "id": "...",
      "slug": "topic/identity-authentication",
      "title": "身份认证",
      "page_type": "topic",
      "score": 0.87,
      "matched_fields": ["alias", "source_chunk"],
      "matched_claims": [],
      "omitted_claim_count": 3
    }
  ],
  "degraded": false,
  "degradation_reason": null,
  "truncation_reason": "token_budget"
}
```

新增查询参数：

- `mode=hybrid|semantic|keyword`，默认 `hybrid`
- `limit`，最终页面数，默认 5，上限 20
- `claim_limit`，最终 claim 总数，默认 12，上限 50

### Agent 工具

- `wiki_search` 调用 `WikiRetrievalService`，返回相关页面、相关 claim、有效原文 citation 和降级信息。
- `wiki_read_page` 增加可选 `query`、`cursor` 和 `limit`；有 query 时按相关性读取，无 query 时按 `position` 分页。
- `search_knowledge` 保持原文搜索能力。本阶段不合并工具名称，但更新工具描述，明确推荐顺序为 `wiki_search → wiki_read_page → read_document`。
- Agent citation 仍注册为原文 `Source`，Wiki page ID 只作为导航元数据，不能替代原文 citation。

### 前端

- `WikiPanel` 增加搜索输入、loading、空结果、降级提示和命中摘要。
- 搜索结果点击后打开现有页面详情，并突出显示命中 claim。
- 默认目录浏览不依赖搜索服务；搜索失败不影响已发布页面浏览。

## 来源有效性与错误处理

- 搜索查询只批量校验最终候选来源，不能对数据库全部 claim 逐条执行查询。
- 文档活动版本切换或删除后，旧 claim 即使仍在搜索索引中，也必须在读取时过滤。
- Wiki 关闭、知识库关闭、跨工作区、跨知识库页面和失效版本继续 fail closed。
- embedding 或 Milvus 失败：退化到页面词法召回并标记原因。
- reranker 失败：退化到 RRF 排序并标记原因。
- PostgreSQL 页面召回失败：不得只靠向量结果绕过页面归属和来源校验；返回稳定的检索不可用状态。
- 人工编辑没有来源时继续 `needs_review`，不进入 Agent 可信检索。

## 可观测性

每次 Wiki 搜索记录：

- 查询模式、知识库数量和查询指纹，不记录完整敏感查询文本。
- 各召回通道候选数、耗时和是否失败。
- RRF 后页面数、rerank 前后数量、最终 claim 数。
- 失效来源过滤数、待核实过滤数、MMR 去重数和预算截断数。
- `degraded` 原因、总耗时及 reranker 耗时。

不得记录模型密钥、完整文档正文或完整来源 chunk。

## 验证策略

### 单元测试

- 页面元数据召回：标题、slug、摘要、别名、空查询和特殊字符。
- chunk 反向映射：单来源、多来源、重复来源、旧页面版本、失效版本和跨库拒绝。
- RRF：不同通道分数不可直接比较，全局排序不受知识库输入顺序影响。
- claim 选择：相关 claim 位于第 9 条以后仍能返回；`chunk-10` 不再错误领先 `chunk-2`。
- reranker/Milvus 失败降级及稳定原因码。
- MMR、页面多样性、总字符预算和 omitted 计数。
- 新生成协议的来源越界、空来源、Map/Reduce 来源继承与内容哈希校验。
- 主题别名归一化及“相关不等于同一主题”。

### 数据库与集成测试

- 真实 PostgreSQL + Milvus：同义词查询通过原文向量召回命中 Wiki 主题页。
- 真实活动版本切换：旧 claim 不再返回，新页面完成前不会引用旧事实。
- 超过 20 个 chunk 的文档：查询命中尾部事实，不依赖 claim 顺序。
- 两个知识库顺序互换时，全局 TopK 相同。
- 新旧 Wiki 页面混合期间，旧 `source_excerpt` 可浏览但不冒充 grounded summary claim。

### 固定质量评测

将 `tests/fixtures/wiki/enterprise-it-operations/TEST-QUESTIONS.md` 转成机器可读数据，每题至少增加两个同义改写，并标注：

- 目标页面或主题
- 目标文档
- 目标标题路径或 chunk
- 必须出现的事实要点
- 禁止出现的错误泛化
- 是否应明确无答案

建议门槛：

| 指标 | 门槛 |
| --- | --- |
| Page Recall@5 | ≥ 0.90 |
| 目标 claim Recall@8 | ≥ 0.85 |
| 有效引用精确率 | ≥ 0.95 |
| `needs_review` / 失效来源进入 Agent 结果 | 0 |
| 无答案题可信误命中率 | ≤ 0.05 |
| 同义改写相对原问题的结果一致率 | ≥ 0.85 |

固定数值在首次基线运行后允许经用户审阅调整，但不得为通过测试而降低“失效来源为 0”这一硬门槛。

## 发布与回滚

1. 先上线新增表、字段和索引；旧代码不依赖新字段。
2. 上线 `WikiRetrievalService` 与新 API，保留旧路径的兼容响应转换开关。
3. 上线新生成协议；只对新任务生效，并提供显式的 Wiki 重建任务。
4. 前端和 Agent 工具切换到新响应后，移除旧的 `ILIKE + trusted[:8]` 路径。
5. 回滚应用时保留新增数据；旧页面和旧 claim 仍可读取，不执行破坏性降级迁移。

## 路线 C：下一阶段目标

当路线 B 的固定评测显示以下任一条件时，进入路线 C：

- 语义相关页面无法通过来源 chunk 映射召回，Page Recall@5 仍低于门槛。
- 主题综合内容与任一单独来源 chunk 的措辞差异过大，导致主题页长期排不到前列。
- 页面数量或 claim 数量增长后，PostgreSQL 元数据召回和反向连接无法满足延迟目标。

路线 C 建立独立 Wiki 搜索单元索引：

- 索引单位为页面摘要和 verified claim，不索引整页重复 Markdown。
- 每个单元保存 `knowledge_base_id`、`page_id`、`page_version_id`、`claim_id`、类型、Dense 向量和 BM25 文本。
- 页面发布指针切换后原子地切换可见版本；删除、回滚和来源失效都有幂等删除/替换任务。
- 原文 chunk、Wiki 单元和页面元数据三路召回继续通过统一融合与 rerank，不让 Wiki 索引变成第二套不可解释答案源。
- 是否增加实体关系图或 GraphRAG 必须另立需求，并用多跳问题的增益与维护成本决定。

## 完成定义

路线 B 只有同时满足以下条件才算完成：

1. Agent Wiki 检索不再使用 `ILIKE` 后固定截取前 8 条 claim。
2. 同义词问题能通过来源 chunk 语义召回定位正确 Wiki 页面。
3. 摘要和主题的可信 claim 都有真实、当前、可回读的 chunk 引用。
4. 搜索结果有分数、命中原因、降级状态和截断原因。
5. 前端可搜索并定位命中 claim。
6. 固定 Wiki 质量评测达到门槛，原有 RAG 回归、来源失效、编辑、审核和回滚测试继续通过。
7. 路线 C 的触发条件、数据边界和迁移方向保留在项目文档中，不在路线 B 内提前实现。
