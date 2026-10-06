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

function mountPanel() {
  return mount(WikiPanel, {
    props: { knowledgeBaseId: 'base' },
    global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
  })
}

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
  api.getWikiPage.mockResolvedValue({
    id: 'page-auth',
    knowledge_base_id: 'base',
    slug: 'topic/authentication',
    title: '身份认证',
    page_type: 'topic',
    current_version_id: 'version-auth',
    version_no: 1,
    content: '# 身份认证',
    summary: '认证要求',
    origin: 'pipeline',
    claims: [{
      id: 'claim-auth',
      claim_key: 'authentication-factor',
      text: '普通员工需要两个独立认证因素。',
      position: 0,
      claim_type: 'topic_fact',
      trust_state: 'verified',
      reason: null,
      sources: [],
    }],
  })
  api.listWikiVersions.mockResolvedValue([])
})

it('按主题与来源语义搜索 Wiki，并显示命中的结论', async () => {
  const wrapper = mountPanel()
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

  await wrapper.get('[data-search-page-id="page-auth"]').trigger('click')
  await flushPromises()
  expect(api.getWikiPage).toHaveBeenCalledWith('base', 'page-auth')
  expect(wrapper.get('[data-claim-id="claim-auth"]').classes()).toContain('claim-highlight')
})

it('明确显示语义检索降级状态', async () => {
  api.searchWiki.mockResolvedValueOnce({
    pages: [],
    degraded: true,
    degradation_reason: 'embedding_unavailable',
    truncation_reason: null,
  })
  const wrapper = mountPanel()
  await flushPromises()

  await wrapper.get('input[aria-label="搜索 Wiki"]').setValue('认证')
  await wrapper.get('form[aria-label="Wiki 搜索"]').trigger('submit')
  await flushPromises()

  expect(wrapper.text()).toContain('语义检索暂不可用，当前结果已降级')
  expect(wrapper.text()).toContain('没有找到相关 Wiki 页面')
})

it('搜索失败后可清除查询并恢复目录', async () => {
  api.listWikiPages.mockResolvedValueOnce([{
    id: 'page-directory',
    knowledge_base_id: 'base',
    slug: 'summary/directory',
    page_type: 'summary',
    title: '目录中的页面',
    current_version_id: 'version-directory',
  }])
  api.searchWiki.mockRejectedValueOnce(new Error('network'))
  const wrapper = mountPanel()
  await flushPromises()

  await wrapper.get('input[aria-label="搜索 Wiki"]').setValue('认证')
  await wrapper.get('form[aria-label="Wiki 搜索"]').trigger('submit')
  await flushPromises()
  expect(wrapper.text()).toContain('Wiki 搜索失败，请重试')

  await wrapper.get('button[aria-label="清除 Wiki 搜索"]').trigger('click')
  expect(wrapper.get('input[aria-label="搜索 Wiki"]').element).toHaveProperty('value', '')
  expect(wrapper.text()).toContain('目录中的页面')
})

