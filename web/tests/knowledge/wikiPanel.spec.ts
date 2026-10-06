import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, vi } from 'vitest'
import WikiPanel from '../../src/views/knowledge/WikiPanel.vue'

const api = vi.hoisted(() => ({
  getWikiConfig: vi.fn(),
  listWikiPages: vi.fn(),
  listWikiJobs: vi.fn(),
  searchWiki: vi.fn(),
  getWikiPage: vi.fn(),
  listWikiVersions: vi.fn(),
}))

vi.mock('../../src/services/knowledgeApi', () => ({ knowledgeApi: api }))

beforeEach(() => {
  vi.clearAllMocks()
  api.getWikiConfig.mockResolvedValue({
    knowledge_base_id: 'base',
    enabled: true,
    token_limit: 1000,
    tokens_reserved: 0,
    tokens_charged: 0,
  })
  api.listWikiPages.mockResolvedValue([])
  api.listWikiJobs.mockResolvedValue([])
  api.searchWiki.mockResolvedValue({
    pages: [{
      id: 'page-auth',
      knowledge_base_id: 'base',
      slug: 'topic/authentication',
      title: '身份认证',
      page_type: 'topic',
      score: 0.91,
      matched_fields: ['source_chunk'],
      matched_claims: [{
        id: 'claim-auth',
        text: '普通员工需要两个独立认证因素。',
        position: 0,
        claim_type: 'topic_fact',
        score: 0.88,
        sources: [],
      }],
      omitted_claim_count: 0,
    }],
    degraded: false,
    degradation_reason: null,
    truncation_reason: null,
  })
})

it('按主题与来源语义搜索 Wiki，并显示命中的结论', async () => {
  const wrapper = mount(WikiPanel, { props: { knowledgeBaseId: 'base' } })
  await flushPromises()

  await wrapper.get('input[aria-label="搜索 Wiki"]').setValue('登录安全要求')
  await wrapper.get('form[aria-label="Wiki 搜索"]').trigger('submit')
  await flushPromises()

  expect(api.searchWiki).toHaveBeenCalledWith('base', '登录安全要求', {
    mode: 'hybrid',
    limit: 5,
    claimLimit: 12,
  })
  expect(wrapper.text()).toContain('身份认证')
  expect(wrapper.text()).toContain('普通员工需要两个独立认证因素。')
  expect(wrapper.text()).toContain('语义来源命中')
})

