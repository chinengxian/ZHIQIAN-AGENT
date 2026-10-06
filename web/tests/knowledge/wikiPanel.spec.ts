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
    global: { stubs: { RouterLink: { props: ['to'], template: '<a :data-route="JSON.stringify(to)"><slot /></a>' } } },
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
    summary_sections: [],
    metadata: [],
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

it('展示结构化摘要、信息卡与可展开的来源链接', async () => {
  api.getWikiPage.mockResolvedValueOnce({
    id: 'page-summary',
    knowledge_base_id: 'base',
    slug: 'summary/auth',
    title: '身份与访问控制规范',
    page_type: 'summary',
    current_version_id: 'version-summary',
    version_no: 1,
    content: '本规范覆盖身份与访问控制。',
    summary: '本规范覆盖身份与访问控制。',
    origin: 'pipeline',
    metadata: [{ key: 'version', label: '版本', value: '3.0' }],
    summary_sections: [{
      id: 'section-overview',
      title: '概述',
      text: '本规范覆盖企业身份与访问控制。',
      position: 0,
      trust_state: 'verified',
      reason: null,
      sources: [{
        document_id: 'doc1',
        document_version_id: 'ver1',
        chunk_id: 'chunk1',
        document_title: '身份与访问控制规范',
        heading_path: ['身份分类'],
        page_start: null,
        page_end: null,
        valid: true,
      }],
    }],
    claims: [],
  })
  const wrapper = mountPanel()
  await flushPromises()

  await wrapper.get('input[aria-label="搜索 Wiki"]').setValue('身份认证')
  await wrapper.get('form[aria-label="Wiki 搜索"]').trigger('submit')
  await flushPromises()
  await wrapper.get('[data-search-page-id="page-auth"]').trigger('click')
  await flushPromises()

  expect(wrapper.text()).toContain('身份与访问控制规范')
  expect(wrapper.text()).toContain('版本')
  expect(wrapper.text()).toContain('3.0')
  expect(wrapper.text()).toContain('本规范覆盖企业身份与访问控制。')
  expect(wrapper.text()).toContain('查看 1 个来源')
  expect(wrapper.text()).not.toContain('[^claim-')

  await wrapper.get('[data-summary-section-id="section-overview"] button').trigger('click')
  const link = wrapper.get('[data-summary-section-id="section-overview"] a')
  expect(link.text()).toContain('身份与访问控制规范')
  expect(link.attributes('data-route')).toContain('/knowledge/base/documents/doc1')
})

it('为旧页面清理 Markdown 展示符号', async () => {
  api.getWikiPage.mockResolvedValueOnce({
    id: 'page-legacy',
    knowledge_base_id: 'base',
    slug: 'summary/legacy',
    title: '旧标题',
    page_type: 'summary',
    current_version_id: 'version-legacy',
    version_no: 1,
    content: '# 旧标题\n- 旧事实 [^claim-1-source-1]',
    summary: '旧事实',
    origin: 'pipeline',
    summary_sections: [],
    metadata: [],
    claims: [],
  })
  const wrapper = mountPanel()
  await flushPromises()

  await wrapper.get('input[aria-label="搜索 Wiki"]').setValue('身份认证')
  await wrapper.get('form[aria-label="Wiki 搜索"]').trigger('submit')
  await flushPromises()
  await wrapper.get('[data-search-page-id="page-auth"]').trigger('click')
  await flushPromises()

  expect(wrapper.text()).toContain('旧标题')
  expect(wrapper.text()).toContain('• 旧事实')
  expect(wrapper.text()).not.toContain('[^claim-1-source-1]')
})
