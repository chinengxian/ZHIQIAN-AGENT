import { afterEach, expect, it, vi } from 'vitest'
import { knowledgeApi } from '../../src/services/knowledgeApi'

afterEach(() => vi.unstubAllGlobals())

it('searches wiki with encoded hybrid options', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    pages: [],
    no_answer: false,
    no_answer_reason: null,
    degraded: false,
    degradation_reason: null,
    truncation_reason: null,
    next_cursor: null,
    collection_version: 'wiki_units_v1',
  }), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)

  await knowledgeApi.searchWiki('base', 'MFA + 双因素', {
    mode: 'hybrid',
    limit: 5,
    claimLimit: 12,
    cursor: 'cursor 2',
  })

  expect(fetchMock.mock.calls[0][0]).toBe(
    '/api/v1/knowledge-bases/base/wiki/search?q=MFA%20%2B%20%E5%8F%8C%E5%9B%A0%E7%B4%A0&mode=hybrid&limit=5&claim_limit=12&cursor=cursor%202',
  )
})

it('loads wiki page claims with encoded pagination options', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    id: 'page-auth',
    claims: [],
    claims_next_cursor: null,
  }), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)

  await knowledgeApi.getWikiPage('base', 'page-auth', {
    claimCursor: 'claim cursor 2',
    claimLimit: 20,
  })

  expect(fetchMock.mock.calls[0][0]).toBe(
    '/api/v1/knowledge-bases/base/wiki/pages/page-auth?claim_cursor=claim%20cursor%202&claim_limit=20',
  )
})

it('calls wiki index retry and backfill endpoints', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: 'job-retry' }), { status: 202 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ created_jobs: 2, existing_jobs: 1, skipped_versions: 0 }), { status: 202 }))
  vi.stubGlobal('fetch', fetchMock)

  await knowledgeApi.retryWikiIndex('base', 'page-auth')
  await knowledgeApi.backfillWikiIndex('base')

  expect(fetchMock.mock.calls[0]).toEqual([
    '/api/v1/knowledge-bases/base/wiki/pages/page-auth/index/retry',
    { method: 'POST' },
  ])
  expect(fetchMock.mock.calls[1]).toEqual([
    '/api/v1/knowledge-bases/base/wiki/index/backfill',
    { method: 'POST' },
  ])
})
