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
  if (start === null || end === null || start < 0 || end <= start || end > content.length) {
    return { before: content, focus: '', after: '' }
  }
  return {
    before: content.slice(0, start),
    focus: content.slice(start, end),
    after: content.slice(end),
  }
}
