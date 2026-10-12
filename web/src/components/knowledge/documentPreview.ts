import type { DocumentPreviewChunk } from '../../types/knowledge'

/** 将预览块转换为用户可见的一基段号或页码。 */
export function previewChunkLabel(chunk: DocumentPreviewChunk) {
  if (chunk.page_start && chunk.page_end && chunk.page_start !== chunk.page_end) {
    return `第 ${chunk.page_start}-${chunk.page_end} 页`
  }
  if (chunk.page_start) return `第 ${chunk.page_start} 页`
  return `第 ${chunk.display_ordinal} 段`
}

/** 按后端给出的纯文本坐标拆分正文，模板用文本插值和 mark 渲染，避免 HTML 注入。 */
export function splitFocusedText(content: string, start: number | null, end: number | null) {
  // 后端坐标来自 Python 字符切片，前端需按 Unicode code point 切分，避免 emoji 等字符让 UTF-16 下标错位。
  const chars = Array.from(content)
  if (start === null || end === null || start < 0 || end <= start || end > chars.length) {
    return { before: content, focus: '', after: '' }
  }
  return {
    before: chars.slice(0, start).join(''),
    focus: chars.slice(start, end).join(''),
    after: chars.slice(end).join(''),
  }
}
