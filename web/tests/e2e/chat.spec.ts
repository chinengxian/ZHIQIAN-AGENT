import { expect, test } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

test('streams remembered turns and isolates a refreshed session', async ({ page }, testInfo) => {
  const consoleErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: '今天想聊些什么？' })).toBeVisible()
  await expect(page.getByText(/API Key/i)).toHaveCount(0)

  await page.getByLabel('消息输入').fill('我喜欢黑咖啡')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('我喜欢黑咖啡', { exact: true })).toBeVisible()
  await expect(page.getByText('已记住：我喜欢黑咖啡', { exact: true })).toBeVisible()

  await page.getByLabel('消息输入').fill('我刚才说了什么？')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText(
    '你之前说：我喜欢黑咖啡；现在说：我刚才说了什么？',
    { exact: true },
  )).toBeVisible()
  await expect(page.getByLabel('消息输入')).toBeEnabled()

  await page.reload()
  await page.getByLabel('消息输入').fill('新会话')
  await page.getByLabel('发送消息').click()
  await expect(page.getByText('已记住：新会话', { exact: true })).toBeVisible()
  await expect(page.getByText(/你之前说：我喜欢黑咖啡/)).toHaveCount(0)

  const hasOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
  expect(hasOverflow).toBe(false)
  expect(consoleErrors).toEqual([])

  const accessibility = await new AxeBuilder({ page }).analyze()
  expect(accessibility.violations).toEqual([])

  await page.screenshot({
    path: testInfo.outputPath(`chat-${testInfo.project.name}.png`),
    fullPage: true,
  })
})
