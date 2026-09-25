import type { DocumentProgress, KnowledgeBase, KnowledgeDocument, WikiConfig, WikiJob, WikiPage, WikiPageSummary, WikiVersion } from '../types/knowledge'

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

export const knowledgeApi = {
  listBases: () => request<KnowledgeBase[]>('/knowledge-bases'),
  createBase: (name: string, description: string) => request<KnowledgeBase>('/knowledge-bases', json('POST', { name, description })),
  updateBase: (id: string, changes: Partial<Pick<KnowledgeBase, 'name' | 'description' | 'enabled'>>) => request<KnowledgeBase>(`/knowledge-bases/${id}`, json('PATCH', changes)),
  deleteBase: (id: string, confirm: boolean) => request<void>(`/knowledge-bases/${id}?confirm=${confirm}`, { method: 'DELETE' }),
  listDocuments: (id: string) => request<KnowledgeDocument[]>(`/knowledge-bases/${id}/documents`),
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
  getWikiPage: (id: string, pageId: string) => request<WikiPage>(`/knowledge-bases/${id}/wiki/pages/${pageId}`),
  listWikiVersions: (id: string, pageId: string) => request<WikiVersion[]>(`/knowledge-bases/${id}/wiki/pages/${pageId}/versions`),
  listWikiJobs: (id: string) => request<WikiJob[]>(`/knowledge-bases/${id}/wiki/jobs`),
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
