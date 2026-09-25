import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

import { expect, test } from '@playwright/test'

test.skip(process.env.RUN_KNOWLEDGE_INTEGRATION !== '1', '需要本机真实知识服务')

test('four formats reach ready and answer with real citations', async ({ page }, testInfo) => {
  test.setTimeout(240_000)
  const name = `引用闭环-${Date.now()}-${testInfo.project.name}`
  const api = 'http://127.0.0.1:8000/api/v1'
  let baseId: string | null = null
  const fixtures = [
    { filename: 'orbital.pdf', mimeType: 'application/pdf', token: 'OrbitalPDF' },
    { filename: 'integration.docx', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', token: 'integration' },
    { filename: 'markdown.md', mimeType: 'text/markdown', token: 'MarkdownToken' },
    { filename: 'plain.txt', mimeType: 'text/plain', token: 'PlainToken' },
  ]
  try {
    await page.goto('/knowledge')
    await page.getByRole('button', { name: '新建知识库' }).click()
    await page.getByRole('dialog').getByText('名称').locator('input').fill(name)
    await page.getByRole('dialog').getByRole('button', { name: '确认' }).click()
    await expect(page.getByRole('heading', { name })).toBeVisible()
    baseId = page.url().split('/').at(-1) ?? null
    for (const fixture of fixtures) {
      const response = page.waitForResponse((value) => value.request().method() === 'POST' && value.url().includes(`/knowledge-bases/${baseId}/documents`))
      await page.getByLabel('选择上传文件').setInputFiles({
        name: fixture.filename,
        mimeType: fixture.mimeType,
        buffer: await readFile(join(process.env.KNOWLEDGE_FIXTURE_DIR!, fixture.filename)),
      })
      const uploaded = await response
      expect(uploaded.status()).toBe(202)
      const accepted = await uploaded.json() as { document_id: string }
      await expect.poll(async () => {
        const detail = await (await page.request.get(`${api}/documents/${accepted.document_id}`)).json() as { status: string }
        return detail.status
      }, { timeout: 100_000 }).toBe('ready')
    }
    await page.goto('/')
    await page.getByRole('button', { name: '选择知识范围' }).click()
    await page.getByRole('checkbox', { name }).check()
    await page.getByRole('button', { name: '完成' }).click()
    for (const fixture of fixtures) {
      await page.getByLabel('消息输入').fill(fixture.token)
      await page.getByLabel('发送消息').click()
      const answer = page.locator('.message-row--assistant').last()
      await expect(answer).toContainText(fixture.token, { timeout: 30_000 })
      await expect(answer.getByRole('button', { name: '查看来源 1' })).toBeVisible()
      await answer.getByRole('button', { name: '查看来源 1' }).click()
      await expect(page.getByRole('dialog', { name: '引用来源' })).toContainText(fixture.token)
      await page.getByRole('button', { name: '关闭来源' }).click()
    }
    await page.getByLabel('消息输入').fill('UnseenKnowledgeKey')
    await page.getByLabel('发送消息').click()
    const noAnswer = page.locator('.message-row--assistant').last()
    await expect(noAnswer).toContainText('未找到相关资料')
    await expect(noAnswer.getByRole('button', { name: '查看来源 1' })).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false)
    await page.screenshot({ path: testInfo.outputPath(`knowledge-chat-${testInfo.project.name}.png`), fullPage: true })
  } finally {
    if (baseId) {
      const deletion = await page.request.delete(`${api}/knowledge-bases/${baseId}?confirm=true`)
      expect(deletion.status()).toBe(204)
      await expect.poll(async () => (await page.request.get(`${api}/knowledge-bases/${baseId}`)).status(), { timeout: 90_000 }).toBe(404)
    }
  }
})
