import { afterEach, vi } from 'vitest'
import { KnowledgeApiError, knowledgeApi } from '../../src/services/knowledgeApi'

afterEach(() => vi.unstubAllGlobals())

it('uses the backend upload contract and preserves the confirmation code', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: { code: 'duplicate_document' } }), { status: 409 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ document_id: 'doc', job_id: 'job' }), { status: 202 }))
  vi.stubGlobal('fetch', fetchMock)
  const file = new File(['hello'], 'guide.txt', { type: 'text/plain' })

  await expect(knowledgeApi.upload('base', file)).rejects.toMatchObject({ code: 'duplicate_document' } satisfies Partial<KnowledgeApiError>)
  await knowledgeApi.upload('base', file, true)

  expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/knowledge-bases/base/documents?on_duplicate=reject')
  expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/knowledge-bases/base/documents?on_duplicate=new_version')
  expect((fetchMock.mock.calls[1][1] as RequestInit).body).toBeInstanceOf(FormData)
})

it('requires an explicit flag for deleting a nonempty knowledge base', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
  vi.stubGlobal('fetch', fetchMock)
  await knowledgeApi.deleteBase('base', true)
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/knowledge-bases/base?confirm=true', { method: 'DELETE' })
})

it('loads document detail, versions and preview with encoded query params', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: 'doc', active_version: null, latest_job: null }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], next_cursor: 'next cursor' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ document_id: 'doc', version_id: 'ver', items: [], next_cursor: null, preview_unavailable_reason: null }), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)

  await knowledgeApi.getDocument('doc')
  await knowledgeApi.listDocumentVersions('doc', { cursor: 'v 1', limit: 5 })
  await knowledgeApi.previewDocument('doc', {
    versionId: 'ver',
    cursor: '10:chunk',
    limit: 3,
    anchorChunkId: 'chunk id',
  })

  expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/documents/doc')
  expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/documents/doc/versions?cursor=v%201&limit=5')
  expect(fetchMock.mock.calls[2][0]).toBe('/api/v1/documents/doc/preview?version_id=ver&cursor=10%3Achunk&limit=3&anchor_chunk_id=chunk%20id')
})

it('rebuilds Wiki aggregation pages without a document generation request', async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ knowledge_base_id: 'base', topic_pages: 2, summary_pages: 8 }), { status: 200 }),
  )
  vi.stubGlobal('fetch', fetchMock)

  await expect(knowledgeApi.rebuildWikiAggregation('base')).resolves.toMatchObject({ topic_pages: 2 })
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/knowledge-bases/base/wiki/rebuild-aggregation',
    { method: 'POST' },
  )
})
