import { expect, test } from '@playwright/test'

test('Wiki supports generation settings, review, history and source inspection', async ({ page }, testInfo) => {
  const base = { id: 'kb-1', name: '产品资料', description: '', enabled: true, document_count: 1, processing_count: 0 }
  const config = { knowledge_base_id: base.id, enabled: false, token_limit: 0, tokens_reserved: 0, tokens_charged: 0 }
  const listing = [
    { id: 'page-1', knowledge_base_id: base.id, slug: 'summary/doc-1', page_type: 'summary', title: '操作手册', current_version_id: 'version-1', index_state: 'ready', index_error_code: null },
    { id: 'page-pending', knowledge_base_id: base.id, slug: 'topic/pending', page_type: 'topic', title: '待索引主题', current_version_id: 'version-pending', index_state: 'indexing', index_error_code: null },
    { id: 'page-failed', knowledge_base_id: base.id, slug: 'topic/failed', page_type: 'topic', title: '失败主题', current_version_id: 'version-failed', index_state: 'failed', index_error_code: 'index_count_mismatch' },
  ]
  const versions = [
    { id: 'version-2', page_id: 'page-1', version_no: 2, title: '操作手册', content: '# 操作手册\n\n自动更新', summary: '', origin: 'pipeline', review_state: 'pending_review', base_published_version_id: 'version-1', created_at: '2026-09-22T00:00:00Z' },
    { id: 'version-1', page_id: 'page-1', version_no: 1, title: '操作手册', content: '# 操作手册\n\n原内容', summary: '', origin: 'user', review_state: 'published', base_published_version_id: null, created_at: '2026-09-21T00:00:00Z' },
  ]
  const source = { document_id: 'doc-1', document_version_id: 'source-version-1', chunk_id: 'chunk-1', parent_chunk_id: 'parent-1', document_title: '操作手册原文', heading_path: ['操作'], page_start: 2, page_end: 2, excerpt: '原文：设备支持离线操作。', focus_start: 3, focus_end: 12, valid: true, invalid_reason: null }
  const detail = { ...listing[0], version_no: 1, content: versions[1].content, summary: '设备可在断网时继续完成离线操作。', origin: 'pipeline', metadata: [{ key: 'document_id', label: '文档编号', value: 'OPS-001' }], summary_sections: [{ id: 'section-1', title: '概述', text: '设备可在断网时继续完成离线操作。', position: 0, trust_state: 'verified', reason: null, sources: [source] }], claims: [{ id: 'claim-1', claim_key: 'proof', text: '设备支持离线操作。', position: 0, claim_type: 'summary_fact', trust_state: 'verified', reason: null, sources: [source] }], claims_next_cursor: 'claim-cursor-2' }
  const moreClaims = { ...detail, claims: [{ id: 'claim-2', claim_key: 'proof-2', text: '补充分页事实。', position: 1, claim_type: 'summary_fact', trust_state: 'verified', reason: null, sources: [source] }], claims_next_cursor: null }
  const firstSearchResult = { pages: [{ ...listing[0], score: 0.92, matched_fields: ['source_chunk'], matched_claims: [{ id: 'claim-1', text: '设备支持离线操作。', position: 0, claim_type: 'summary_fact', score: 0.89, sources: [], also_in_pages: [] }], omitted_claim_count: 0 }], no_answer: false, no_answer_reason: null, degraded: false, degradation_reason: null, truncation_reason: null, next_cursor: 'cursor-2', collection_version: 'wiki_units_v1' }
  const nextSearchResult = { pages: [{ id: 'page-2', knowledge_base_id: base.id, slug: 'topic/offline', page_type: 'topic', title: '离线运行', current_version_id: 'version-topic', index_state: 'ready', index_error_code: null, score: 0.81, matched_fields: ['keyword'], matched_claims: [{ id: 'claim-topic', text: '离线模式支持断网操作。', position: 0, claim_type: 'topic_fact', score: 0.77, sources: [], also_in_pages: [listing[0]] }], omitted_claim_count: 0 }], no_answer: false, no_answer_reason: null, degraded: false, degradation_reason: null, truncation_reason: null, next_cursor: null, collection_version: 'wiki_units_v1' }
  const noAnswerResult = { pages: [{ ...listing[0], title: 'VPN 账号安全', score: 0.1, matched_fields: ['keyword'], matched_claims: [], omitted_claim_count: 0 }], no_answer: true, no_answer_reason: 'below_relevance_threshold', degraded: false, degradation_reason: null, truncation_reason: null, next_cursor: null, collection_version: 'wiki_units_v1' }
  await page.route('**/api/v1/knowledge/events/stream', (route) => route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }))
  await page.route('**/api/v1/documents/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/v1/documents/doc-1') {
      return route.fulfill({ json: { id: 'doc-1', knowledge_base_id: base.id, title: '操作手册原文', filename: 'ops.md', mime_type: 'text/markdown', status: 'ready', active_version_id: 'source-version-1', created_at: '2026-09-20T00:00:00Z', updated_at: '2026-09-20T00:00:00Z', active_version: { id: 'source-version-1', version_no: 1, file_size: 128, status: 'ready', parser_name: 'markdown', created_at: '2026-09-20T00:00:00Z', ready_at: '2026-09-20T00:00:00Z', error_code: null, error_reference: null }, latest_job: null } })
    }
    if (url.pathname === '/api/v1/documents/doc-1/versions') {
      return route.fulfill({ json: { items: [{ id: 'source-version-1', version_no: 1, file_size: 128, status: 'ready', parser_name: 'markdown', created_at: '2026-09-20T00:00:00Z', ready_at: '2026-09-20T00:00:00Z', error_code: null, error_reference: null }], next_cursor: null } })
    }
    if (url.pathname === '/api/v1/documents/doc-1/preview') {
      return route.fulfill({ json: { document_id: 'doc-1', version_id: 'source-version-1', items: [{ chunk_id: 'parent-1', ordinal: 0, display_ordinal: 1, content: '前文。设备支持离线操作。后文。', heading_path: ['操作'], page_start: 2, page_end: 2, anchor_chunk_id: url.searchParams.get('anchor_chunk_id'), focus_start: 3, focus_end: 12 }], next_cursor: null, preview_unavailable_reason: null } })
    }
    return route.continue()
  })
  await page.route('**/api/v1/knowledge-bases**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const method = route.request().method()
    if (path === '/api/v1/knowledge-bases') return route.fulfill({ json: [base] })
    if (path === '/api/v1/knowledge-bases/kb-1/documents') return route.fulfill({ json: [] })
    if (path.endsWith('/wiki/config')) {
      if (method === 'PATCH') Object.assign(config, route.request().postDataJSON())
      return route.fulfill({ json: config })
    }
    if (path.endsWith('/wiki/search')) {
      if (url.searchParams.get('q')?.includes('宇宙')) return route.fulfill({ json: noAnswerResult })
      if (url.searchParams.get('cursor') === 'cursor-2') return route.fulfill({ json: nextSearchResult })
      return route.fulfill({ json: firstSearchResult })
    }
    if (path.endsWith('/wiki/pages')) return route.fulfill({ json: listing })
    if (path.endsWith('/wiki/jobs')) return route.fulfill({ json: [] })
    if (path.endsWith('/wiki/pages/page-1/versions')) return route.fulfill({ json: versions })
    if (path.endsWith('/wiki/pages/page-failed/index/retry')) return route.fulfill({ json: { job_id: 'job-retry', page_version_id: 'version-failed' } })
    if (path.endsWith('/wiki/pages/page-1/review')) {
      const body = route.request().postDataJSON() as { decision: string }
      versions[0].review_state = body.decision === 'publish' ? 'published' : 'rejected'
      if (body.decision === 'publish') { detail.current_version_id = 'version-2'; detail.content = versions[0].content; detail.version_no = 2 }
      return route.fulfill({ json: versions[0] })
    }
    if (path.endsWith('/wiki/pages/page-1')) return route.fulfill({ json: url.searchParams.get('claim_cursor') ? moreClaims : detail })
    return route.continue()
  })
  await page.goto('/knowledge/kb-1')
  await page.getByRole('tab', { name: 'Wiki' }).click()
  await expect(page.getByText('生成已关闭')).toBeVisible()
  await page.getByRole('button', { name: '开启 Wiki' }).click()
  await expect(page.getByText('生成已开启')).toBeVisible()
  await expect(page.getByText('索引中')).toBeVisible()
  await expect(page.getByText('索引失败')).toBeVisible()
  await page.getByRole('button', { name: '重试索引' }).click()
  await expect(page.getByText('Wiki 页面索引重试已提交')).toBeVisible()
  await page.getByRole('searchbox', { name: '搜索 Wiki' }).fill('离线如何操作')
  await page.getByRole('button', { name: '搜索', exact: true }).click()
  await expect(page.getByText('语义命中')).toBeVisible()
  await page.getByRole('button', { name: '加载更多' }).click()
  await expect(page.getByText('关键词命中')).toBeVisible()
  await expect(page.getByText('离线模式支持断网操作。')).toBeVisible()
  await page.locator('[data-search-page-id="page-1"]').click()
  await expect(page.getByText('文档编号')).toBeVisible()
  await expect(page.getByText('设备可在断网时继续完成离线操作。')).toBeVisible()
  await page.getByRole('button', { name: '加载更多 Wiki 事实' }).click()
  await expect(page.getByText('补充分页事实。')).toBeVisible()
  const summarySection = page.locator('[data-summary-section-id="section-1"]')
  await summarySection.getByRole('button', { name: '查看 1 个来源' }).click()
  await expect(summarySection.getByRole('link', { name: /操作手册原文/ })).toBeVisible()
  await expect(summarySection.getByText(/source-version-1/)).toBeVisible()
  await expect(summarySection.getByText('原文：设备支持离线操作。')).toBeVisible()
  await summarySection.getByRole('link', { name: /操作手册原文/ }).click()
  await expect(page.getByText('正文预览')).toBeVisible()
  await expect(page.locator('mark')).toContainText('设备支持离线操作。')
  await page.getByRole('tab', { name: 'Wiki' }).click()
  await page.getByRole('searchbox', { name: '搜索 Wiki' }).fill('宇宙密码')
  await page.getByRole('button', { name: '搜索', exact: true }).click()
  await expect(page.getByText('没有找到足够相关且可核验的 Wiki 内容')).toBeVisible()
  await expect(page.getByText('VPN 账号安全')).toHaveCount(0)
  await page.getByRole('button', { name: '清除 Wiki 搜索' }).click()
  await page.locator('[data-page-id="page-1"]').click()
  await expect(page.getByText('自动更新')).toBeVisible()
  await page.getByRole('button', { name: '发布更新' }).click()
  await expect(page.getByText('更新已发布')).toBeVisible()
  await page.getByRole('button', { name: '关闭 Wiki' }).click()
  await expect(page.getByText('生成已关闭')).toBeVisible()
  await expect(page.getByText('设备可在断网时继续完成离线操作。')).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
  expect(overflow).toBe(false)
  await page.screenshot({ path: testInfo.outputPath(`wiki-${testInfo.project.name}.png`), fullPage: true })
})
