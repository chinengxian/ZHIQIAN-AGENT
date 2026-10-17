# 文档详情与正文预览技术设计

- 状态：approved
- 关联需求：`docs/requirements/REQ-007.md`（AC-001～AC-006）
- 范围依据：用户于 2026-10-05 明确要求补充前端文档查看能力；本设计将其限定为元数据、解析正文预览和 Wiki 来源定位。

## 现状与缺口

- `GET /api/v1/documents/{document_id}` 与列表接口共用 `DocumentResponse`，仅包含标题、文件名、MIME、状态、活动版本 ID 和最近任务。
- PostgreSQL 已保存 `DocumentVersion` 的版本号、文件大小、解析状态与时间，以及 `Chunk` 的正文、标题路径、页码、顺序和父子关系，不需要新增数据库迁移。
- `KnowledgeView.vue` 的文档行只支持进度、重试、重建和删除，没有详情入口。
- `WikiPanel.vue` 的来源按钮只展开版本 ID、chunk ID 和位置文字，无法进入原文上下文。

## API 契约

### 文档详情

保留 `GET /api/v1/documents/{document_id}`，将其响应模型改为向后兼容的 `DocumentDetailResponse`。除现有字段外增加：

- `created_at`、`updated_at`；
- `active_version`：版本 ID、版本号、文件大小、状态、创建/就绪时间、解析器名称；
- `latest_job`：任务 ID、关联版本 ID、阶段、状态、进度、稳定错误码和参考号。

不返回 `file_path`、`sha256`、Embedding provider/model/fingerprint 或内部异常。现有消费者继续读取原字段，不需要同步停机升级。

### 版本列表

新增：

```text
GET /api/v1/documents/{document_id}/versions?cursor=&limit=
```

按 `version_no DESC, id DESC` 稳定分页，返回版本 ID、版本号、状态、文件大小、创建/就绪时间、解析器名称及用户安全的失败状态。`limit` 默认 20、最大 50。

### 解析正文预览

新增：

```text
GET /api/v1/documents/{document_id}/preview
    ?version_id=
    &cursor=
    &limit=
    &anchor_chunk_id=
```

- `version_id` 省略时使用活动版本；显式传入时必须属于该文档。
- 预览以 parent chunk 为上下文块，按 `ordinal, id` 稳定分页；无 parent 的内容按自身块返回。
- `anchor_chunk_id` 属于 child 时解析到其 parent，同时返回 `focus_start`、`focus_end` 和 `anchor_chunk_id`，供前端滚动和高亮；锚点优先决定首屏，后续翻页继续使用游标。
- 响应块包含 `chunk_id`、`ordinal`、`content`、`heading_path`、`page_start`、`page_end` 和可空 focus，不返回 `embedding_text`。
- `limit` 默认 10、最大 30；应用层同时限制单响应正文字符数。达到字符上限时返回下一游标，不截断成无法解释的成功正文。
- 版本没有可保留的分块时返回结构化空结果和 `preview_unavailable_reason`，例如 `processing`、`failed`、`content_removed`；不回退到另一版本。

所有接口继续使用固定工作区归属检查。版本、锚点跨文档或资源不存在返回稳定 404；参数不合法返回稳定 400。错误响应不得包含 SQL、磁盘路径或正文。

## 前端路由与交互

新增深链：

```text
/knowledge/{knowledge_base_id}/documents/{document_id}
    ?version={document_version_id}
    &chunk={chunk_id}
```

- 文档标题和“查看详情”操作进入该路由；页面刷新、前进后退都从 URL 恢复选择。
- 桌面端在知识库详情区展示文档详情，移动端使用同一内容的全宽布局；提供明确的“返回文档列表”。
- 顶部信息区展示文件、格式、状态、活动版本、大小、上传/就绪时间和最近任务。
- 正文区按版本加载分页预览，显示标题路径、页码或“第 N 段”；下一页采用显式按钮或可访问的渐进加载。
- 从 Wiki 来源进入时，使用来源的 `document_id`、`document_version_id` 和 `chunk_id` 生成深链。有效锚点滚动并短暂高亮；失效来源保留其元数据并显示“来源失效/正文不可用”。
- 正文一律使用 Vue 文本插值渲染，不使用未清洗的 `v-html`。

## 状态与错误

- 文档切换、路由切换或组件卸载时取消/忽略旧请求，避免旧响应覆盖新文档。
- `pending`/`processing`：展示进度与“正文将在处理完成后可用”。
- `failed`：展示稳定错误码、参考号和现有重试入口。
- `deleting`/404：展示资源正在删除或已不存在，并允许返回列表。
- 空分块：区分确实无正文和旧版本内容已清理，不显示空白页。

## 安全与性能

- 复用 `ManagementService` 的工作区/知识库归属边界，查询时同时约束 document、version、chunk 关系。
- 正文视为不可信纯文本；不解析 HTML，不自动加载图片、链接、iframe 或附件。
- 预览只读取 PostgreSQL，不访问受控原文件路径，也不让浏览器获取真实存储位置。
- 游标、条数和响应字符数都设置上限；查询只取当前页面所需列，并复用现有 `ix_chunks_version_parent` 索引。

## 验证策略

- 后端单元/API：详情字段、版本分页、活动版本默认值、处理中/失败空状态、响应字段脱敏。
- 数据库集成：parent/child 定位、锚点前后文、游标稳定性、跨文档/跨库拒绝、旧版本内容清理。
- 前端单元：API 参数编码、路由恢复、纯文本渲染、异步竞态和各状态提示。
- 浏览器 E2E：文档列表进入详情、正文翻页、刷新恢复、Wiki 来源跳转、失效来源、桌面/移动无溢出。
- 回归：现有知识库上传、重试、重建、删除和 Wiki 浏览/审核用例继续通过。

## AC 映射

| AC | 设计覆盖 |
| --- | --- |
| AC-001 | 详情响应、信息区与脱敏字段 |
| AC-002 | parent 上下文、稳定游标、响应上限 |
| AC-003 | Wiki 深链、版本固定与 chunk 锚点 |
| AC-004 | 不回退版本、失效/清理空状态 |
| AC-005 | document/version/chunk 归属校验与稳定错误 |
| AC-006 | URL 状态、返回导航、桌面/移动 E2E |

