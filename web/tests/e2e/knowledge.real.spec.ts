import { expect, test } from '@playwright/test'
import { resolve } from 'node:path'

test.skip(process.env.RUN_KNOWLEDGE_INTEGRATION !== '1', '需要本机真实知识服务')

test('manages a document through the real API, worker, and storage', async ({ page }, testInfo) => {
  test.setTimeout(180_000)
  const name = `页面联调-${Date.now()}`
  let baseId: string | null = null
  try {
    await page.goto('/knowledge')
    await page.getByRole('button', { name: '新建知识库' }).click()
    await page.getByRole('dialog').getByText('名称').locator('input').fill(name)
    await page.getByRole('dialog').getByRole('button', { name: '确认' }).click()
    await expect(page.getByRole('heading', { name })).toBeVisible()
    baseId = page.url().split('/').at(-1) ?? null

    const upload = page.waitForResponse((response) => response.url().endsWith('/documents?on_duplicate=reject') && response.request().method() === 'POST')
    await page.getByLabel('选择上传文件').setInputFiles({ name: 'guide.txt', mimeType: 'text/plain', buffer: Buffer.from('真实服务页面联调正文。') })
    const uploadResponse = await upload
    expect(uploadResponse.status()).toBe(202)
    const accepted = await uploadResponse.json() as { document_id: string }
    const documentUrl = `http://127.0.0.1:8000/api/v1/documents/${accepted.document_id}`
    await expect(page.locator('.document-status.ready')).toBeVisible({ timeout: 90_000 })
    await page.reload()
    await expect(page.locator('.document-status.ready')).toBeVisible()

    const beforeReindex = await (await page.request.get(documentUrl)).json() as { active_version_id: string }
    await page.getByLabel('重建 guide').click()
    await expect(page.getByText('重建已提交，旧版本在成功前继续可用')).toBeVisible()
    await expect.poll(async () => {
      const result = await (await page.request.get(documentUrl)).json() as { active_version_id: string }
      return result.active_version_id
    }, { timeout: 90_000 }).not.toBe(beforeReindex.active_version_id)
    await expect(page.getByLabel('重建 guide')).toBeVisible({ timeout: 90_000 })

    await page.getByLabel('选择上传文件').setInputFiles(resolve('..', 'tests', 'fixtures', 'documents', 'corrupt.pdf'))
    await expect(page.getByText('corrupt.pdf：文件内容与格式不符')).toBeVisible()
    await page.getByLabel('选择上传文件').setInputFiles({ name: 'broken.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\nnot a valid PDF\n') })
    await expect(page.getByText('处理失败', { exact: true })).toBeVisible({ timeout: 90_000 })
    await page.getByLabel('重试 broken').click()
    await expect(page.getByText('重试已提交')).toBeVisible()
    await expect(page.getByText('处理失败', { exact: true })).toBeVisible({ timeout: 90_000 })
    await page.getByLabel('删除 broken').click()
    await page.getByRole('dialog').getByRole('button', { name: '确认' }).click()
    await expect(page.getByText('broken.pdf')).toHaveCount(0, { timeout: 60_000 })

    await page.getByLabel('删除知识库').click()
    await expect(page.getByText(/其中 1 个文档将异步清理/)).toBeVisible()
    await page.getByRole('dialog').getByRole('button', { name: '确认' }).click()
    await expect(page.getByRole('link', { name: new RegExp(name) })).toHaveCount(0, { timeout: 60_000 })
    await expect.poll(async () => (await page.request.get(`http://127.0.0.1:8000/api/v1/knowledge-bases/${baseId}`)).status(), { timeout: 90_000 }).toBe(404)
    baseId = null
    await page.screenshot({ path: testInfo.outputPath('knowledge-real.png'), fullPage: true })
  } finally {
    if (baseId) {
      // 仅清理本测试新建的知识库，保留用户原有数据。
      await page.request.delete(`http://127.0.0.1:8000/api/v1/knowledge-bases/${baseId}?confirm=true`)
    }
  }
})
