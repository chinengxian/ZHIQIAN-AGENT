import type { DocumentDetail, DocumentPreview, DocumentProgress, DocumentVersionPage, KnowledgeBase, KnowledgeDocument, WikiConfig, WikiJob, WikiPage, WikiPageSummary, WikiSearchResult, WikiVersion } from '../types/knowledge'

const base = '/api/v1'

export class KnowledgeApiError extends Error {
  constructor(message: string, public code: string, public requiresConfirmation = false) {
    super(message)
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, options)
  if (!response.ok) {
    let detail: { code?: string; requires_confirmation?: boolean } = {}
    try {
      const body = await response.json() as { detail?: typeof detail }
      detail = body.detail ?? {}
    } catch { /* 错误体可能不是 JSON，统一显示安全提示。 */ }
    throw new KnowledgeApiError('操作未完成，请检查后重试', detail.code ?? 'http_error', detail.requires_confirmation ?? false)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

const json = (method: string, payload: object): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
const query = (values: Record<string, string | number | null | undefined>) => {
  const pairs = Object.entries(values)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
  return pairs.length ? `?${pairs.join('&')}` : ''
}

export const knowledgeApi = {
  listBases: () => request<KnowledgeBase[]>('/knowledge-bases'),
  createBase: (name: string, description: string) => request<KnowledgeBase>('/knowledge-bases', json('POST', { name, description })),
  updateBase: (id: string, changes: Partial<Pick<KnowledgeBase, 'name' | 'description' | 'enabled'>>) => request<KnowledgeBase>(`/knowledge-bases/${id}`, json('PATCH', changes)),
  deleteBase: (id: string, confirm: boolean) => request<void>(`/knowledge-bases/${id}?confirm=${confirm}`, { method: 'DELETE' }),
  listDocuments: (id: string) => request<KnowledgeDocument[]>(`/knowledge-bases/${id}/documents`),
  getDocument: (id: string) => request<DocumentDetail>(`/documents/${id}`),
  listDocumentVersions: (id: string, options: { cursor?: string | null; limit?: number } = {}) =>
    request<DocumentVersionPage>(`/documents/${id}/versions${query({ cursor: options.cursor, limit: options.limit })}`),
  previewDocument: (id: string, options: { versionId?: string | null; cursor?: string | null; limit?: number; anchorChunkId?: string | null } = {}) =>
    request<DocumentPreview>(`/documents/${id}/preview${query({ version_id: options.versionId, cursor: options.cursor, limit: options.limit, anchor_chunk_id: options.anchorChunkId })}`),
  upload: (id: string, file: File, newVersion = false) => {
    const body = new FormData()
    body.append('file', file)
    return request<{ document_id: string; job_id: string }>(`/knowledge-bases/${id}/documents?on_duplicate=${newVersion ? 'new_version' : 'reject'}`, { method: 'POST', body })
  },
  retry: (id: string) => request(`/documents/${id}/retry`, { method: 'POST' }),
  reindex: (id: string) => request(`/documents/${id}/reindex`, { method: 'POST' }),
  deleteDocument: (id: string) => request(`/documents/${id}`, { method: 'DELETE' }),
  getWikiConfig: (id: string) => request<WikiConfig>(`/knowledge-bases/${id}/wiki/config`),
  updateWikiConfig: (id: string, changes: Partial<Pick<WikiConfig, 'enabled' | 'token_limit'>>) => request<WikiConfig>(`/knowledge-bases/${id}/wiki/config`, json('PATCH', changes)),
  listWikiPages: (id: string, cursor?: string) => request<WikiPageSummary[]>(`/knowledge-bases/${id}/wiki/pages?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`),
  rebuildWikiAggregation: (id: string) => request<{ knowledge_base_id: string; topic_pages: number; summary_pages: number }>(`/knowledge-bases/${id}/wiki/rebuild-aggregation`, { method: 'POST' }),
  getWikiPage: (id: string, pageId: string, options: { claimCursor?: string | null; claimLimit?: number } = {}) =>
    request<WikiPage>(`/knowledge-bases/${id}/wiki/pages/${pageId}${query({ claim_cursor: options.claimCursor, claim_limit: options.claimLimit })}`),
  listWikiVersions: (id: string, pageId: string) => request<WikiVersion[]>(`/knowledge-bases/${id}/wiki/pages/${pageId}/versions`),
  listWikiJobs: (id: string) => request<WikiJob[]>(`/knowledge-bases/${id}/wiki/jobs`),
  searchWiki: (
    id: string,
    searchQuery: string,
    options: { mode?: 'hybrid' | 'semantic' | 'keyword'; limit?: number; claimLimit?: number; cursor?: string | null } = {},
  ) => request<WikiSearchResult>(`/knowledge-bases/${id}/wiki/search${query({
    q: searchQuery,
    mode: options.mode ?? 'hybrid',
    limit: options.limit ?? 5,
    claim_limit: options.claimLimit ?? 12,
    cursor: options.cursor,
  })}`),
  retryWikiIndex: (id: string, pageId: string) => request<{ job_id: string; page_version_id: string }>(`/knowledge-bases/${id}/wiki/pages/${pageId}/index/retry`, { method: 'POST' }),
  backfillWikiIndex: (id: string) => request<{ created_jobs: number; existing_jobs: number; skipped_versions: number }>(`/knowledge-bases/${id}/wiki/index/backfill`, { method: 'POST' }),
  editWikiPage: (id: string, pageId: string, baseVersion: string, title: string, content: string) => request<WikiPage>(`/knowledge-bases/${id}/wiki/pages/${pageId}`, json('PUT', { base_version: baseVersion, title, content })),
  revertWikiPage: (id: string, pageId: string, baseVersion: string, targetVersionId: string) => request<WikiPage>(`/knowledge-bases/${id}/wiki/pages/${pageId}/revert`, json('POST', { base_version: baseVersion, target_version_id: targetVersionId })),
  reviewWikiPage: (id: string, pageId: string, candidateVersionId: string, baseVersion: string, decision: 'publish' | 'reject') => request<WikiVersion>(`/knowledge-bases/${id}/wiki/pages/${pageId}/review`, json('POST', { candidate_version_id: candidateVersionId, base_version: baseVersion, decision })),
  events: (onProgress: (progress: DocumentProgress) => void, onReconnect: () => void) => {
    const source = new EventSource(`${base}/knowledge/events/stream`)
    source.addEventListener('document_progress', (event) => {
      try { onProgress(JSON.parse((event as MessageEvent).data) as DocumentProgress) } catch { onReconnect() }
    })
    source.addEventListener('open', onReconnect)
    return source
  },
}
