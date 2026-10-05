# Document Preview Display Ordinal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让文档正文预览按父块顺序稳定显示从 1 开始的连续段号，同时保留底层父子块原始序号。

**Architecture:** 后端在仅包含父块的窗口查询中计算一基 `display_ordinal`，然后再应用现有游标或锚点过滤，保证翻页和定位后的编号仍是文档内全局排名。API 明确返回该字段；前端通过一个纯标签函数使用展示序号，页码展示规则保持不变。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、SQLAlchemy 2、PostgreSQL、Vue 3、TypeScript、Vitest、pytest

---

## 文件边界

- `src/agent_api/knowledge/api/routes.py`：声明预览响应中的 `display_ordinal` 契约。
- `src/agent_api/knowledge/application/management.py`：计算父块的全局稳定展示排名。
- `tests/test_knowledge_api.py`：验证 HTTP 响应保留展示序号。
- `tests/test_knowledge_management_integration.py`：验证首屏、分页和锚点定位的全局连续编号。
- `web/src/types/knowledge.ts`：同步前端预览块类型。
- `web/src/components/knowledge/documentPreview.ts`：封装用户可见的页码／段号标签规则。
- `web/src/components/knowledge/DocumentDetailPanel.vue`：使用纯标签函数渲染元数据。
- `web/tests/knowledge/documentPreview.spec.ts`：验证从 1 开始的连续段号展示。

> 当前上述多数文件包含用户已有的未提交“文档详情”改动。实施期间只修改目标行并保留现有内容；为避免把用户改动混入提交，本计划不自动提交业务代码，最终以精确 diff 交付。

### Task 1: 扩展预览 HTTP 契约

**Files:**
- Modify: `tests/test_knowledge_api.py:162-180,378-405`
- Modify: `src/agent_api/knowledge/api/routes.py:182-192`

- [ ] **Step 1: 写入失败的 API 契约测试**

在 `FakeManagementService.preview_document()` 的返回项中加入字段，并在端点测试中断言它不会被 Pydantic 响应模型过滤：

```python
"items": [
    {
        "chunk_id": PARENT_CHUNK_ID,
        "ordinal": 7,
        "display_ordinal": 4,
        "content": "<script>alert(1)</script>\n正文",
        "heading_path": ["章节"],
        "page_start": 1,
        "page_end": 2,
        "anchor_chunk_id": anchor_chunk_id,
        "focus_start": 3,
        "focus_end": 5,
    }
]
```

```python
assert item["display_ordinal"] == 4
```

- [ ] **Step 2: 运行测试并确认按预期失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_api.py::test_document_preview_endpoint_forwards_version_cursor_and_anchor -q
```

Expected: FAIL，响应项中没有 `display_ordinal`。

- [ ] **Step 3: 最小化扩展响应模型**

在 `DocumentPreviewChunkResponse` 中加入：

```python
class DocumentPreviewChunkResponse(BaseModel):
    # 解析正文预览块；正文由前端按纯文本渲染，不能当 HTML 使用。
    chunk_id: UUID
    ordinal: int
    display_ordinal: int
    content: str
```

- [ ] **Step 4: 运行 API 测试并确认通过**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_api.py::test_document_preview_endpoint_forwards_version_cursor_and_anchor -q
```

Expected: PASS。

### Task 2: 计算稳定的父块全局展示序号

**Files:**
- Modify: `tests/test_knowledge_management_integration.py:280-430`
- Modify: `src/agent_api/knowledge/application/management.py:341-438`

- [ ] **Step 1: 扩展集成测试数据与断言**

在现有文档检查测试中，为活动版本加入三个原始序号不连续的父块及对应子块，例如父块原始序号为 `10、20、30`。测试以下行为：

```python
first_page = await management.preview_document(
    document_id,
    version_id=active_version.id,
    limit=1,
)
assert first_page["items"][0]["ordinal"] == 10
assert first_page["items"][0]["display_ordinal"] == 1
assert first_page["next_cursor"] is not None

second_page = await management.preview_document(
    document_id,
    version_id=active_version.id,
    cursor=first_page["next_cursor"],
    limit=1,
)
assert second_page["items"][0]["ordinal"] == 20
assert second_page["items"][0]["display_ordinal"] == 2

anchored = await management.preview_document(
    document_id,
    version_id=active_version.id,
    anchor_chunk_id=third_child_chunk_id,
)
assert anchored["items"][0]["ordinal"] == 30
assert anchored["items"][0]["display_ordinal"] == 3
assert anchored["items"][0]["anchor_chunk_id"] == third_child_chunk_id
```

- [ ] **Step 2: 运行集成测试并确认按预期失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_management_integration.py::test_document_inspection_uses_safe_versions_preview_and_ownership -q
```

Expected: FAIL，预览项中没有 `display_ordinal`。

- [ ] **Step 3: 在游标过滤前计算窗口排名**

用父块子查询先完成排名，再在外层应用游标或锚点条件：

```python
ranked_parent_chunks = (
    select(
        Chunk.id.label("chunk_id"),
        func.row_number()
        .over(order_by=(Chunk.ordinal, Chunk.id))
        .label("display_ordinal"),
    )
    .where(
        Chunk.document_version_id == version.id,
        Chunk.parent_chunk_id.is_(None),
    )
    .subquery()
)
context_query = (
    select(Chunk, ranked_parent_chunks.c.display_ordinal)
    .join(ranked_parent_chunks, ranked_parent_chunks.c.chunk_id == Chunk.id)
    .order_by(Chunk.ordinal, Chunk.id)
    .limit(limit + 1)
)
```

查询结果改用 `session.execute()` 读取 `(chunk, display_ordinal)`，并在响应项中加入：

```python
"display_ordinal": int(display_ordinal),
```

游标继续编码原始 `ordinal` 和块 ID，不改变现有分页协议。

- [ ] **Step 4: 运行集成测试并确认通过**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_management_integration.py::test_document_inspection_uses_safe_versions_preview_and_ownership -q
```

Expected: PASS。

- [ ] **Step 5: 运行知识管理与 API 回归测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_api.py tests/test_knowledge_management_integration.py -q
```

Expected: 全部 PASS。

### Task 3: 前端使用一基展示序号

**Files:**
- Create: `web/src/components/knowledge/documentPreview.ts`
- Create: `web/tests/knowledge/documentPreview.spec.ts`
- Modify: `web/src/types/knowledge.ts:55-65`
- Modify: `web/src/components/knowledge/DocumentDetailPanel.vue:1-70,228`

- [ ] **Step 1: 写入失败的纯函数测试**

```typescript
import { describe, expect, it } from 'vitest'
import { previewChunkLabel } from '../../src/components/knowledge/documentPreview'
import type { DocumentPreviewChunk } from '../../src/types/knowledge'

const chunk = (changes: Partial<DocumentPreviewChunk>): DocumentPreviewChunk => ({
  chunk_id: 'chunk-1',
  ordinal: 4,
  display_ordinal: 3,
  content: '正文',
  heading_path: [],
  page_start: null,
  page_end: null,
  anchor_chunk_id: null,
  focus_start: null,
  focus_end: null,
  ...changes,
})

describe('previewChunkLabel', () => {
  it('使用从 1 开始的展示序号而不是底层序号', () => {
    expect(previewChunkLabel(chunk({}))).toBe('第 3 段')
  })

  it('有页码时继续优先显示页码', () => {
    expect(previewChunkLabel(chunk({ page_start: 2, page_end: 4 }))).toBe('第 2-4 页')
  })
})
```

- [ ] **Step 2: 运行测试并确认按预期失败**

Run:

```powershell
Set-Location web
npm run test -- --run tests/knowledge/documentPreview.spec.ts
```

Expected: FAIL，`documentPreview` 模块不存在。

- [ ] **Step 3: 同步类型并实现最小标签函数**

在 `DocumentPreviewChunk` 中加入：

```typescript
display_ordinal: number
```

创建纯标签函数：

```typescript
import type { DocumentPreviewChunk } from '../../types/knowledge'

/** 将预览块转换为用户可见的一基段号或页码。 */
export function previewChunkLabel(chunk: DocumentPreviewChunk) {
  if (chunk.page_start && chunk.page_end && chunk.page_start !== chunk.page_end) {
    return `第 ${chunk.page_start}-${chunk.page_end} 页`
  }
  if (chunk.page_start) return `第 ${chunk.page_start} 页`
  return `第 ${chunk.display_ordinal} 段`
}
```

在 `DocumentDetailPanel.vue` 中导入 `previewChunkLabel`，删除原有 `pageLabel()`，模板改为：

```vue
<span>{{ previewChunkLabel(chunk) }}</span>
```

- [ ] **Step 4: 运行前端单测并确认通过**

Run:

```powershell
Set-Location web
npm run test -- --run tests/knowledge/documentPreview.spec.ts
```

Expected: 2 tests PASS。

- [ ] **Step 5: 运行前端类型、Lint 和构建检查**

Run:

```powershell
Set-Location web
npm run type-check
npm run lint
npm run build
```

Expected: 三条命令均退出码 0。

### Task 4: 完整验证与差异审查

**Files:**
- Verify only: all files listed above

- [ ] **Step 1: 运行后端定向测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_api.py tests/test_knowledge_management_integration.py -q
```

Expected: 全部 PASS。

- [ ] **Step 2: 运行前端定向测试和静态检查**

```powershell
Set-Location web
npm run test -- --run tests/knowledge/documentPreview.spec.ts tests/knowledge/knowledgeApi.spec.ts
npm run type-check
npm run lint
```

Expected: 全部 PASS，输出无新增错误。

- [ ] **Step 3: 审查精确差异**

```powershell
git diff -- src/agent_api/knowledge/api/routes.py src/agent_api/knowledge/application/management.py tests/test_knowledge_api.py tests/test_knowledge_management_integration.py web/src/types/knowledge.ts web/src/components/knowledge/DocumentDetailPanel.vue web/src/components/knowledge/documentPreview.ts web/tests/knowledge/documentPreview.spec.ts
```

Expected: 仅包含连续展示序号相关改动，并完整保留已有文档详情实现。
