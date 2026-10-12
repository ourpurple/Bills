import { test, expect } from '@playwright/test'

test('P2: inactive mailbox, upload, worker conversion, safe preview and narrow layout', async ({
  page,
}) => {
  const pageErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  const external: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('untrusted-preview.example')) external.push(request.url())
  })
  await page.goto('/login')
  await page.getByLabel('用户名', { exact: true }).fill('e2e-admin')
  await page.locator('#login-password').fill('browser-test-only-2026')
  await page.getByRole('button', { name: '进入工作台' }).click()
  await expect(page.getByRole('heading', { name: '让账单，井然有序。' })).toBeVisible()
  await page.getByRole('link', { name: '邮箱与导入' }).click()
  await expect(page.getByRole('heading', { name: '正在运行', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '新增邮箱', exact: true }).click()
  const dialog = page.getByRole('dialog')
  const field = (label: string) =>
    dialog
      .locator('.el-form-item')
      .filter({ has: page.locator('.el-form-item__label', { hasText: label }) })
  await field('邮箱名称').locator('input').fill('隔离演示邮箱')
  await field('邮箱地址').locator('input').fill('owner@example.test')
  await field('邮箱服务器').locator('input').fill('imap.example.test')
  await field('登录用户名').locator('input').fill('owner@example.test')
  await field('密码 / 授权码').locator('input').fill('synthetic-not-a-real-secret')
  await field('启用定时同步').locator('.el-switch').click()
  await dialog.getByRole('button', { name: '保存邮箱' }).click()
  await expect(dialog).not.toBeVisible()
  const box = page.locator('.mailbox-entry').filter({ hasText: '隔离演示邮箱' })
  await expect(box).toContainText('已停用')
  await expect(box.getByRole('button', { name: '立即同步' })).toBeDisabled()
  await box.getByRole('button', { name: '编辑邮箱' }).click()
  await expect(field('密码 / 授权码').locator('input')).toHaveValue('')
  await dialog.getByRole('button', { name: '取消', exact: true }).click()

  const before = await (await page.request.get('/api/v1/admin/statements')).json()
  await page.getByRole('tab', { name: '来源资料' }).click()
  expect(
    (await page.locator('.import-filters .el-select').first().boundingBox())?.width,
  ).toBeGreaterThan(150)
  const raw = Buffer.from(
    [
      'From: synthetic@example.test',
      'Subject: Synthetic P2 safe preview',
      'MIME-Version: 1.0',
      'Content-Type: text/html; charset=utf-8',
      '',
      '<html><body><h1>Safe source 123.45</h1><script>window.p2Unsafe=true</script>',
      '<img src="https://untrusted-preview.example/tracking"><p>Text only, not a statement.</p></body></html>',
    ].join('\r\n'),
  )
  const upload = { name: 'synthetic.eml', mimeType: 'message/rfc822', buffer: raw }
  await page.getByLabel('选择资料文件').setInputFiles(upload)
  const row = page.getByRole('row').filter({ hasText: 'Synthetic P2 safe preview' })
  await expect(row).toContainText('文本已准备', { timeout: 20000 })
  await row.getByRole('button', { name: '安全预览' }).click()
  const preview = page.getByRole('dialog', { name: '安全资料预览 · 不是账单解析结果' })
  await expect(preview.locator('pre')).toContainText('Safe source 123.45')
  await expect(preview.locator('pre')).not.toContainText('p2Unsafe')
  expect(
    await page.evaluate(() => (window as Window & { p2Unsafe?: boolean }).p2Unsafe),
  ).toBeUndefined()
  expect(external).toEqual([])
  expect(pageErrors).toEqual([])
  await preview.getByRole('button', { name: '关闭此对话框' }).click()
  await page.getByLabel('选择资料文件').setInputFiles(upload)
  await expect(page.getByText('该资料已经导入，不重复创建来源')).toBeVisible()
  await expect(row).toHaveCount(1)
  const after = await (await page.request.get('/api/v1/admin/statements')).json()
  expect(after.total).toBe(before.total)
  await page.getByRole('link', { name: '任务队列' }).click()
  await expect(page).toHaveURL(/\/tasks$/)
  await expect(page.getByRole('link', { name: '任务队列' })).toHaveClass(/active/)
  await page.reload()
  await expect(page.getByRole('heading', { name: '所有任务，统一查看。' })).toBeVisible()
  await expect(page.getByRole('row').filter({ hasText: '通用资料转换' }).first()).toContainText(
    '任务完成',
  )
  await page.getByRole('link', { name: '邮箱与导入' }).click()
  await expect(page.getByRole('tab', { name: '任务队列' })).toHaveCount(0)
  await page.getByRole('tab', { name: '来源资料' }).click()
  await page.screenshot({ path: '../docs/screenshots/imports.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  )
})
