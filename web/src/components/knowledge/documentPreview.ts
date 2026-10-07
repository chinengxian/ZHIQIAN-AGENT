import type { DocumentPreviewChunk } from '../../types/knowledge'

/** 将预览块转换为用户可见的一基段号或页码。 */
export function previewChunkLabel(chunk: DocumentPreviewChunk) {
  if (chunk.page_start && chunk.page_end && chunk.page_start !== chunk.page_end) {
    return `第 ${chunk.page_start}-${chunk.page_end} 页`
  }
  if (chunk.page_start) return `第 ${chunk.page_start} 页`
  return `第 ${chunk.display_ordinal} 段`
}
