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
