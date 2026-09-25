import { expect, test } from '@playwright/test'

const source = {
  citation_id: '[1]', chunk_id: 'chunk', document_id: 'doc', document_version_id: 'version',
  title: '部署指南', filename: 'guide.pdf', page_start: 2, page_end: 2,
  heading_path: ['安装'], excerpt: '先安装依赖。', score: 0.8, rank: 1,
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/knowledge-bases', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify([
      { id: 'kb-1', name: '产品手册', description: '', enabled: true, document_count: 1, processing_count: 0 },
      { id: 'kb-2', name: '停用库', description: '', enabled: false, document_count: 1, processing_count: 0 },
    ]) })
  })
})

test('selected scope and citation open the PDF source', async ({ page }, testInfo) => {
  let scope: unknown
  await page.route('**/api/v1/chat/stream', async (route) => {
    scope = JSON.parse(route.request().postData() ?? '{}').knowledge_scope
    await route.fulfill({ contentType: 'text/event-stream', body: [
      'event: status\ndata: {"stage":"retrieving"}\n\n',
      `event: sources\ndata: ${JSON.stringify({ items: [source] })}\n\n`,
      'event: message\ndata: {"content":"见 [1]"}\n\n',
      'event: done\ndata: {}\n\n',
    ].join('') })
  })
  await page.goto('/')
  await page.getByRole('button', { name: '选择知识范围' }).click()
  await page.getByRole('checkbox', { name: '产品手册' }).check()
  await expect(page.getByRole('checkbox', { name: '停用库' })).toHaveCount(0)
  await page.getByRole('button', { name: '完成' }).click()
  await page.getByLabel('消息输入').fill('如何安装？')
  await page.getByLabel('发送消息').click()
  await expect(page.getByRole('button', { name: '查看来源 1' })).toBeVisible()
  expect(scope).toEqual({ mode: 'selected', knowledge_base_ids: ['kb-1'] })
  await page.getByRole('button', { name: '查看来源 1' }).click()
  await expect(page.getByRole('dialog', { name: '引用来源' })).toContainText('第 2 页')
  await expect(page.getByRole('dialog', { name: '引用来源' })).toContainText('先安装依赖。')
  await page.screenshot({ path: testInfo.outputPath(`knowledge-source-${testInfo.project.name}.png`), fullPage: true })
  await page.getByRole('button', { name: '关闭来源' }).click()
})

test('no-hit response has no citation and generation can be stopped', async ({ page }) => {
  await page.addInitScript(() => {
    const originalFetch = window.fetch.bind(window)
    window.fetch = (input, init) => {
      if (String(input).endsWith('/api/v1/chat/stream')) {
        const encoder = new TextEncoder()
        return Promise.resolve(new Response(new ReadableStream({
          start(controller) {
            controller.enqueue(encoder.encode('event: status\ndata: {"stage":"retrieving"}\n\n'))
            controller.enqueue(encoder.encode('event: message\ndata: {"content":"未找到相关资料"}\n\n'))
            init?.signal?.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')))
          },
        }), { headers: { 'content-type': 'text/event-stream' } }))
      }
      return originalFetch(input, init)
    }
  })
  await page.goto('/')
  await page.getByLabel('消息输入').fill('不存在的问题')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('未找到相关资料')).toBeVisible()
  await expect(page.getByRole('button', { name: /个来源/ })).toHaveCount(0)
  await page.getByLabel('停止生成').click()
  await expect(page.getByText('已停止')).toBeVisible()
})
