import { afterEach, expect, it, vi } from 'vitest'
import { knowledgeApi } from '../../src/services/knowledgeApi'

afterEach(() => vi.unstubAllGlobals())

it('searches wiki with encoded hybrid options', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    pages: [],
    degraded: false,
    degradation_reason: null,
    truncation_reason: null,
  }), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)

  await knowledgeApi.searchWiki('base', 'MFA + 双因素', {
    mode: 'hybrid',
    limit: 5,
    claimLimit: 12,
  })

  expect(fetchMock.mock.calls[0][0]).toBe(
    '/api/v1/knowledge-bases/base/wiki/search?q=MFA%20%2B%20%E5%8F%8C%E5%9B%A0%E7%B4%A0&mode=hybrid&limit=5&claim_limit=12',
  )
})
