import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, vi } from 'vitest'
import WikiPanel from '../../src/views/knowledge/WikiPanel.vue'

const api = vi.hoisted(() => ({
  getWikiConfig: vi.fn(),
  listWikiPages: vi.fn(),
  listWikiJobs: vi.fn(),
  searchWiki: vi.fn(),
  rebuildWikiAggregation: vi.fn(),
  retryWikiIndex: vi.fn(),
  backfillWikiIndex: vi.fn(),
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
  api.rebuildWikiAggregation.mockResolvedValue({ topic_pages: 1, summary_pages: 1 })
  api.retryWikiIndex.mockResolvedValue({ id: 'job-retry' })
  api.backfillWikiIndex.mockResolvedValue({ created_jobs: 1, existing_jobs: 0, skipped_versions: 0 })
  api.searchWiki.mockResolvedValue({
    pages: [{
      id: 'page-auth',
      knowledge_base_id: 'base',
      slug: 'topic/authentication',
      title: '身份认证',
      page_type: 'topic',
      score: 0.91,
      matched_fields: ['dense', 'keyword'],
      matched_claims: [{
        id: 'claim-auth',
        text: '普通员工需要两个独立认证因素。',
        position: 0,
        claim_type: 'topic_fact',
        score: 0.88,
        sources: [],
        also_in_pages: [],
      }],
      omitted_claim_count: 0,
    }],
    no_answer: false,
    no_answer_reason: null,
    degraded: false,
    degradation_reason: null,
    truncation_reason: null,
    next_cursor: 'cursor-2',
    collection_version: 'wiki_units_v1',
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
    claims_next_cursor: 'claim-cursor-2',
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
  expect(wrapper.text()).toContain('语义命中')
  expect(wrapper.text()).toContain('关键词命中')

  await wrapper.get('button[aria-label="加载更多 Wiki 搜索结果"]').trigger('click')
  await flushPromises()
  expect(api.searchWiki).toHaveBeenLastCalledWith('base', '登录安全要求', expect.objectContaining({ cursor: 'cursor-2' }))

  await wrapper.get('[data-search-page-id="page-auth"]').trigger('click')
  await flushPromises()
  expect(api.getWikiPage).toHaveBeenCalledWith('base', 'page-auth', { claimLimit: 20 })
  expect(wrapper.get('[data-claim-id="claim-auth"]').classes()).toContain('claim-highlight')

  api.getWikiPage.mockResolvedValueOnce({
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
      id: 'claim-auth-more',
      claim_key: 'authentication-more',
      text: '管理员登录需要 MFA。',
      position: 1,
      claim_type: 'topic_fact',
      trust_state: 'verified',
      reason: null,
      sources: [],
    }],
    claims_next_cursor: null,
  })
  await wrapper.get('button[aria-label="加载更多 Wiki 事实"]').trigger('click')
  await flushPromises()
  expect(api.getWikiPage).toHaveBeenLastCalledWith('base', 'page-auth', expect.objectContaining({ claimCursor: 'claim-cursor-2' }))
  expect(wrapper.text()).toContain('管理员登录需要 MFA。')
})

it('事实分页遇到页面版本变化时重新加载第一页', async () => {
  const wrapper = mountPanel()
  await flushPromises()

  await wrapper.get('input[aria-label="搜索 Wiki"]').setValue('登录安全要求')
  await wrapper.get('form[aria-label="Wiki 搜索"]').trigger('submit')
  await flushPromises()
  await wrapper.get('[data-search-page-id="page-auth"]').trigger('click')
  await flushPromises()

  api.getWikiPage
    .mockResolvedValueOnce({
      id: 'page-auth',
      knowledge_base_id: 'base',
      slug: 'topic/authentication',
      title: '身份认证',
      page_type: 'topic',
      current_version_id: 'version-auth-new',
      version_no: 2,
      content: '# 身份认证',
      summary: '认证要求',
      origin: 'pipeline',
      summary_sections: [],
      metadata: [],
      claims: [{
        id: 'claim-old-cursor-slice',
        claim_key: 'old-cursor-slice',
        text: '旧游标后的非第一页事实。',
        position: 21,
        claim_type: 'topic_fact',
        trust_state: 'verified',
        reason: null,
        sources: [],
      }],
      claims_next_cursor: null,
    })
    .mockResolvedValueOnce({
      id: 'page-auth',
      knowledge_base_id: 'base',
      slug: 'topic/authentication',
      title: '身份认证',
      page_type: 'topic',
      current_version_id: 'version-auth-new',
      version_no: 2,
      content: '# 身份认证',
      summary: '认证要求',
      origin: 'pipeline',
      summary_sections: [],
      metadata: [],
      claims: [{
        id: 'claim-new-first-page',
        claim_key: 'new-first-page',
        text: '新版第一页事实。',
        position: 0,
        claim_type: 'topic_fact',
        trust_state: 'verified',
        reason: null,
        sources: [],
      }],
      claims_next_cursor: null,
    })

  await wrapper.get('button[aria-label="加载更多 Wiki 事实"]').trigger('click')
  await flushPromises()

  expect(api.getWikiPage).toHaveBeenLastCalledWith('base', 'page-auth', { claimLimit: 20 })
  expect(wrapper.text()).toContain('新版第一页事实。')
  expect(wrapper.text()).not.toContain('旧游标后的非第一页事实。')
})

it('明确显示语义检索降级状态', async () => {
  api.searchWiki.mockResolvedValueOnce({
    pages: [],
    no_answer: false,
    no_answer_reason: null,
    degraded: true,
    degradation_reason: 'embedding_unavailable',
    truncation_reason: null,
    next_cursor: null,
    collection_version: null,
  })
  const wrapper = mountPanel()
  await flushPromises()

  await wrapper.get('input[aria-label="搜索 Wiki"]').setValue('认证')
  await wrapper.get('form[aria-label="Wiki 搜索"]').trigger('submit')
  await flushPromises()

  expect(wrapper.text()).toContain('语义检索暂不可用，当前结果已降级')
  expect(wrapper.text()).toContain('没有找到相关 Wiki 页面')
})

it('无可核验答案时显示拒答状态且不展示低相关候选', async () => {
  api.searchWiki.mockResolvedValueOnce({
    pages: [{
      id: 'page-vpn',
      knowledge_base_id: 'base',
      slug: 'topic/vpn',
      title: 'VPN 账号安全',
      page_type: 'topic',
      score: 0.12,
      matched_fields: ['keyword'],
      matched_claims: [],
      omitted_claim_count: 0,
    }],
    no_answer: true,
    no_answer_reason: 'below_relevance_threshold',
    degraded: false,
    degradation_reason: null,
    truncation_reason: null,
    next_cursor: null,
    collection_version: 'wiki_units_v1',
  })
  const wrapper = mountPanel()
  await flushPromises()

  await wrapper.get('input[aria-label="搜索 Wiki"]').setValue('认证')
  await wrapper.get('form[aria-label="Wiki 搜索"]').trigger('submit')
  await flushPromises()

  expect(wrapper.text()).toContain('没有找到足够相关且可核验的 Wiki 内容')
  expect(wrapper.text()).not.toContain('VPN 账号安全')
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

it('首页显示主题和文档摘要导航卡而不是 Markdown 目录', async () => {
  api.listWikiPages.mockResolvedValueOnce([
    { id: 'page-index', knowledge_base_id: 'base', slug: 'index/home', page_type: 'index', title: 'Wiki 目录', current_version_id: 'version-index' },
    { id: 'page-topic', knowledge_base_id: 'base', slug: 'topic/authentication', page_type: 'topic', title: '身份认证', current_version_id: 'version-topic' },
    { id: 'page-summary', knowledge_base_id: 'base', slug: 'summary/doc1', page_type: 'summary', title: '身份规范', current_version_id: 'version-summary' },
  ])
  api.getWikiPage.mockResolvedValueOnce({
    id: 'page-index', knowledge_base_id: 'base', slug: 'index/home', page_type: 'index', current_version_id: 'version-index', version_no: 1,
    title: 'Wiki 目录', content: '# Wiki 目录\n- [[topic/authentication|身份认证]]', summary: '知识库 Wiki 目录', origin: 'pipeline', summary_sections: [], metadata: [], claims: [],
  })

  const wrapper = mountPanel()
  await flushPromises()
  await wrapper.get('[data-page-id="page-index"]').trigger('click')
  await flushPromises()

  expect(wrapper.text()).toContain('主题入口')
  expect(wrapper.text()).toContain('文档摘要入口')
  expect(wrapper.text()).toContain('身份认证')
  expect(wrapper.text()).not.toContain('[[topic/authentication|身份认证]]')
})

it('主题页默认显示摘要段落并把原子事实收进折叠区', async () => {
  api.listWikiPages.mockResolvedValueOnce([
    { id: 'page-summary', knowledge_base_id: 'base', slug: 'summary/doc1', page_type: 'summary', title: '身份规范', current_version_id: 'version-summary' },
  ])
  api.getWikiPage.mockResolvedValueOnce({
    id: 'page-auth', knowledge_base_id: 'base', slug: 'topic/authentication', page_type: 'topic', current_version_id: 'version-auth', version_no: 2,
    title: '身份认证', content: '旧事实清单', summary: '身份认证主题', origin: 'pipeline',
    metadata: [{ key: 'document_count', label: '涉及文档', value: '1' }],
    summary_sections: [{ id: 'section-topic', title: '身份规范 · 概述', text: '组织要求使用唯一身份。', position: 0, trust_state: 'verified', reason: null, sources: [] }],
    claims: [{ id: 'claim-topic', claim_key: 'doc:summary-1', text: '每名使用者必须使用唯一身份。', position: 0, claim_type: 'topic_fact', trust_state: 'verified', reason: null, sources: [] }],
  })
  const wrapper = mountPanel()
  await flushPromises()
  await wrapper.get('input[aria-label="搜索 Wiki"]').setValue('身份认证')
  await wrapper.get('form[aria-label="Wiki 搜索"]').trigger('submit')
  await flushPromises()
  await wrapper.get('[data-search-page-id="page-auth"]').trigger('click')
  await flushPromises()

  expect(wrapper.text()).toContain('组织要求使用唯一身份。')
  const details = wrapper.get('details')
  expect(details.attributes('open')).toBeUndefined()
  await details.get('summary').trigger('click')
  expect(details.text()).toContain('每名使用者必须使用唯一身份。')
})

it('可从面板触发不调用模型的聚合页重建', async () => {
  const wrapper = mountPanel()
  await flushPromises()

  const rebuildButton = wrapper.findAll('button').find((item) => item.text() === '重建聚合页')
  expect(rebuildButton).toBeDefined()
  await rebuildButton!.trigger('click')
  await flushPromises()

  expect(api.rebuildWikiAggregation).toHaveBeenCalledWith('base')
  expect(wrapper.text()).toContain('聚合页已重建：1 个主题、1 个文档摘要')
})

it('显示索引状态并允许重试失败页面索引', async () => {
  api.listWikiPages.mockResolvedValueOnce([{
    id: 'page-auth',
    knowledge_base_id: 'base',
    slug: 'topic/authentication',
    page_type: 'topic',
    title: '身份认证',
    current_version_id: 'version-auth',
    index_state: 'failed',
    index_error_code: 'embedding_failed',
  }])
  const wrapper = mountPanel()
  await flushPromises()

  expect(wrapper.text()).toContain('索引失败')
  const retryButton = wrapper.findAll('button').find((item) => item.text() === '重试索引')
  expect(retryButton).toBeDefined()
  await retryButton!.trigger('click')
  await flushPromises()

  expect(api.retryWikiIndex).toHaveBeenCalledWith('base', 'page-auth')
})

it('可回填 Wiki 索引，并在已有重建任务运行时提示冲突', async () => {
  const wrapper = mountPanel()
  await flushPromises()

  const backfillButton = wrapper.findAll('button').find((item) => item.text() === '回填 Wiki 索引')
  expect(backfillButton).toBeDefined()
  await backfillButton!.trigger('click')
  await flushPromises()
  expect(api.backfillWikiIndex).toHaveBeenCalledWith('base')
  expect(wrapper.text()).toContain('Wiki 索引回填已启动')

  api.backfillWikiIndex.mockRejectedValueOnce(Object.assign(new Error('conflict'), { code: 'wiki_index_rebuild_in_progress' }))
  await backfillButton!.trigger('click')
  await flushPromises()

  expect(wrapper.text()).toContain('已有 Wiki 索引重建任务运行')
})
