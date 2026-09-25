import { expect, test } from '@playwright/test'

test('Wiki supports generation settings, review, history and source inspection', async ({ page }, testInfo) => {
  const base = { id: 'kb-1', name: '产品资料', description: '', enabled: true, document_count: 1, processing_count: 0 }
  const config = { knowledge_base_id: base.id, enabled: false, token_limit: 0, tokens_reserved: 0, tokens_charged: 0 }
  const listing = [{ id: 'page-1', knowledge_base_id: base.id, slug: 'summary/doc-1', page_type: 'summary', title: '操作手册', current_version_id: 'version-1' }]
  const versions = [
    { id: 'version-2', page_id: 'page-1', version_no: 2, title: '操作手册', content: '# 操作手册\n\n自动更新', summary: '', origin: 'pipeline', review_state: 'pending_review', base_published_version_id: 'version-1', created_at: '2026-09-22T00:00:00Z' },
    { id: 'version-1', page_id: 'page-1', version_no: 1, title: '操作手册', content: '# 操作手册\n\n原内容', summary: '', origin: 'user', review_state: 'published', base_published_version_id: null, created_at: '2026-09-21T00:00:00Z' },
  ]
  const detail = { ...listing[0], version_no: 1, content: versions[1].content, summary: '', origin: 'user', claims: [{ id: 'claim-1', claim_key: 'proof', text: '设备支持离线操作。', trust_state: 'verified', reason: null, sources: [{ document_id: 'doc-1', document_version_id: 'source-version-1', chunk_id: 'chunk-1', document_title: '操作手册原文', heading_path: ['操作'], page_start: 2, page_end: 2, valid: true }] }] }
  await page.route('**/api/v1/knowledge/events/stream', (route) => route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }))
  await page.route('**/api/v1/knowledge-bases**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    if (path === '/api/v1/knowledge-bases') return route.fulfill({ json: [base] })
    if (path === '/api/v1/knowledge-bases/kb-1/documents') return route.fulfill({ json: [] })
    if (path.endsWith('/wiki/config')) {
      if (method === 'PATCH') Object.assign(config, route.request().postDataJSON())
      return route.fulfill({ json: config })
    }
    if (path.endsWith('/wiki/pages')) return route.fulfill({ json: listing })
    if (path.endsWith('/wiki/jobs')) return route.fulfill({ json: [] })
    if (path.endsWith('/wiki/pages/page-1/versions')) return route.fulfill({ json: versions })
    if (path.endsWith('/wiki/pages/page-1/review')) {
      const body = route.request().postDataJSON() as { decision: string }
      versions[0].review_state = body.decision === 'publish' ? 'published' : 'rejected'
      if (body.decision === 'publish') { detail.current_version_id = 'version-2'; detail.content = versions[0].content; detail.version_no = 2 }
      return route.fulfill({ json: versions[0] })
    }
    if (path.endsWith('/wiki/pages/page-1')) return route.fulfill({ json: detail })
    return route.continue()
  })
  await page.goto('/knowledge/kb-1')
  await page.getByRole('tab', { name: 'Wiki' }).click()
  await expect(page.getByText('生成已关闭')).toBeVisible()
  await page.getByRole('button', { name: '开启 Wiki' }).click()
  await expect(page.getByText('生成已开启')).toBeVisible()
  await page.getByRole('navigation', { name: 'Wiki 页面' }).getByRole('button', { name: /操作手册/ }).click()
  await expect(page.getByText('设备支持离线操作。')).toBeVisible()
  await page.getByRole('button', { name: /操作手册原文/ }).click()
  await expect(page.getByText(/source-version-1/)).toBeVisible()
  await expect(page.getByText('自动更新')).toBeVisible()
  await page.getByRole('button', { name: '发布更新' }).click()
  await expect(page.getByText('更新已发布')).toBeVisible()
  await page.getByRole('button', { name: '关闭 Wiki' }).click()
  await expect(page.getByText('生成已关闭')).toBeVisible()
  await expect(page.getByText('设备支持离线操作。')).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
  expect(overflow).toBe(false)
  await page.screenshot({ path: testInfo.outputPath(`wiki-${testInfo.project.name}.png`), fullPage: true })
})
