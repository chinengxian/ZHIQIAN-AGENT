import { describe, expect, it } from 'vitest'

import { previewChunkLabel, splitFocusedText } from '../../src/components/knowledge/documentPreview'
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

describe('splitFocusedText', () => {
  it('按安全文本片段渲染高亮，不注入 HTML', () => {
    const content = '<img src=x onerror=alert(1)>认证要求'
    const parts = splitFocusedText(content, 0, 28)

    expect(parts.before).toBe('')
    expect(parts.focus).toContain('<img')
    expect(parts.after).toBe('认证要求')
  })

  it('坐标无效时回退为普通文本', () => {
    expect(splitFocusedText('认证要求', 5, 1)).toEqual({ before: '认证要求', focus: '', after: '' })
  })
})
