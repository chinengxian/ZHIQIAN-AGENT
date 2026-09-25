import { expect, test } from '@playwright/test'

test('manages a knowledge base and recovers document progress after reload', async ({ page }, testInfo) => {
  const bases = [{ id: 'base-1', name: '产品手册', description: '内部资料', enabled: true, document_count: 0, processing_count: 0 }]
  const documents: Record<string, unknown>[] = []
  const actions: string[] = []
  await page.route('**/api/v1/knowledge/events/stream', async (route) => {
    await route.fulfill({ status: 200, contentType: 'text/event-stream', body: 'event: document_progress\ndata: {"type":"document_progress","document_id":"doc-1","job_id":"job-1","stage":"parsing","status":"running","progress":35,"error_code":null,"error_reference":null}\n\n' })
  })
  await page.route('**/api/v1/knowledge-bases**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()
    if (path.endsWith('/knowledge-bases') && method === 'GET') return route.fulfill({ json: bases })
    if (path.endsWith('/knowledge-bases') && method === 'POST') {
      const body = request.postDataJSON() as { name: string; description: string }
      bases.push({ id: 'base-2', name: body.name, description: body.description, enabled: true, document_count: 0, processing_count: 0 })
      return route.fulfill({ status: 201, json: bases[1] })
    }
    if (path.endsWith('/documents') && method === 'GET') return route.fulfill({ json: documents })
    if (path.endsWith('/documents') && method === 'POST') {
      documents.push({ id: 'doc-1', knowledge_base_id: 'base-1', title: 'guide', filename: 'guide.txt', mime_type: 'text/plain', status: 'processing', active_version_id: null, job: { id: 'job-1', stage: 'parsing', status: 'running', progress: 35, error_code: null, error_reference: null } })
      bases[0].document_count = 1
      return route.fulfill({ status: 202, json: { document_id: 'doc-1', document_version_id: 'version-1', job_id: 'job-1', status: 'pending' } })
    }
    if (path.includes('/knowledge-bases/base-1') && method === 'PATCH') {
      Object.assign(bases[0], request.postDataJSON())
      return route.fulfill({ json: bases[0] })
    }
    if (path.includes('/knowledge-bases/base-2') && method === 'PATCH') {
      Object.assign(bases[1], request.postDataJSON())
      return route.fulfill({ json: bases[1] })
    }
    if (path.includes('/knowledge-bases/base-1') && method === 'DELETE') {
      actions.push(`${method} ${url.search}`)
      return route.fulfill({ status: 204 })
    }
    return route.continue()
  })
  await page.route('**/api/v1/documents/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    actions.push(`${request.method()} ${path}`)
    if (path.endsWith('/retry')) {
      documents[0].status = 'ready'
      documents[0].active_version_id = 'version-1'
      return route.fulfill({ status: 202, json: { document_id: 'doc-1', job_id: 'job-2', status: 'pending' } })
    }
    if (path.endsWith('/reindex')) return route.fulfill({ status: 202, json: { document_id: 'doc-1', job_id: 'job-3', status: 'pending' } })
    if (request.method() === 'DELETE') {
      documents[0].status = 'deleting'
      return route.fulfill({ status: 202, json: { document_id: 'doc-1', job_id: 'job-4', status: 'deleting' } })
    }
    return route.continue()
  })

  await page.goto('/knowledge')
  if (testInfo.project.name === 'mobile') await page.getByLabel('打开导航').click()
  await expect(page.getByRole('link', { name: '知识库' })).toBeVisible()
  if (testInfo.project.name === 'mobile') await page.getByLabel('关闭导航').click()
  await page.getByRole('button', { name: '新建知识库' }).click()
  await page.getByRole('dialog').getByText('名称').locator('input').fill('技术资料')
  await page.getByRole('dialog').getByRole('button', { name: '确认' }).click()
  await expect(page.getByRole('heading', { name: '技术资料' })).toBeVisible()
  await page.getByLabel('编辑知识库').click()
  await page.getByRole('dialog').getByText('名称').locator('input').fill('研发资料')
  await page.getByRole('dialog').getByRole('button', { name: '确认' }).click()
  await expect(page.getByRole('heading', { name: '研发资料' })).toBeVisible()
  await page.getByRole('link', { name: /产品手册/ }).click()
  await expect(page.getByRole('heading', { name: '产品手册' })).toBeVisible()
  await page.getByLabel('选择上传文件').setInputFiles({ name: 'guide.txt', mimeType: 'text/plain', buffer: Buffer.from('guide') })
  await expect(page.getByText('解析中 · 35%')).toBeVisible()
  await page.reload()
  await expect(page.getByText('解析中 · 35%')).toBeVisible()
  await page.getByLabel('停用知识库').click()
  await expect(page.getByText('已停用')).toBeVisible()
  documents[0].status = 'failed'
  documents[0].job = { id: 'job-1', stage: 'failed', status: 'failed', progress: 35, error_code: 'parse_failed', error_reference: 'ref-1' }
  await page.reload()
  await expect(page.getByText('parse_failed')).toBeVisible()
  await page.getByLabel('重试 guide').click()
  await expect(page.getByLabel('重建 guide')).toBeVisible()
  await page.getByLabel('重建 guide').click()
  await expect(page.getByText('重建已提交，旧版本在成功前继续可用')).toBeVisible()
  await page.getByLabel('删除 guide').click()
  await page.getByRole('dialog').getByRole('button', { name: '确认' }).click()
  await expect(page.getByText('删除已提交')).toBeVisible()
  await page.getByLabel('删除知识库').click()
  await expect(page.getByText(/其中 1 个文档将异步清理/)).toBeVisible()
  await page.getByRole('dialog').getByRole('button', { name: '确认' }).click()
  expect(actions).toContain('DELETE ?confirm=true')
  expect(actions).toContain('POST /api/v1/documents/doc-1/retry')
  expect(actions).toContain('POST /api/v1/documents/doc-1/reindex')
  const hasOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
  expect(hasOverflow).toBe(false)
  await page.screenshot({ path: testInfo.outputPath(`knowledge-${testInfo.project.name}.png`), fullPage: true })
})
