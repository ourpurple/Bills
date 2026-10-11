import { test, expect } from '@playwright/test'

test('P3: configure → model candidate → review → publish → details → revise keeps repayments', async ({
  page,
}) => {
  test.setTimeout(90000)
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  const external: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('model-preview.example')) external.push(r.url())
  })
  await page.goto('/login')
  await page.getByLabel('用户名', { exact: true }).fill('e2e-admin')
  await page.locator('#login-password').fill('browser-test-only-2026')
  await page.getByRole('button', { name: '进入工作台' }).click()
  await expect(page.getByRole('heading', { name: '让账单，井然有序。' })).toBeVisible()
  const session = await (await page.request.get('/api/v1/auth/me')).json()
  const headers = { 'X-CSRF-Token': session.csrf_token, Origin: 'http://localhost:5173' }
  const create = async (path: string, data: unknown) => {
    const response = await page.request.post('/api/v1/admin/' + path, { headers, data })
    expect(response.status()).toBe(201)
    return response.json()
  }
  const bank = (await (await page.request.get('/api/v1/admin/banks')).json()).items.find(
    (b: { code: string }) => b.code === 'cmb',
  )
  const holder = await create('cardholders', { name: '模型验收持卡人' })
  const group = await create('account-groups', {
    name: '模型验收分组',
    bank_id: bank.id,
    cardholder_id: holder.id,
  })
  const account = await create('accounts', { name: '模型验收账户', group_id: group.id })
  await create('cards', { name: '合成卡片', account_id: account.id, last_four: '5678' })
  await page.getByRole('link', { name: '模型与核对' }).click()
  await expect(page.getByRole('heading', { name: '模型整理，你来定稿。' })).toBeVisible()
  await page.getByRole('button', { name: '配置模型', exact: true }).click()
  const configuration = page.getByRole('dialog', { name: '模型配置 · 密钥不回显' })
  const field = (label: string) =>
    configuration
      .locator('.el-form-item')
      .filter({ has: page.locator('.el-form-item__label', { hasText: label }) })
  await field('HTTP / HTTPS 基础地址').locator('input').fill('http://models.example.test:8080/v1')
  await field('模型名称').locator('input').fill('synthetic-review-v1')
  await field('API Key').locator('input').fill('browser-synthetic-not-real')
  await configuration.locator('.el-checkbox').filter({ hasText: '启用模型' }).click()
  await configuration.getByRole('button', { name: '保存模型配置' }).click()
  await expect(configuration).not.toBeVisible()
  await expect(page.getByText('自动发送关闭', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '配置模型', exact: true }).click()
  await expect(field('HTTP / HTTPS 基础地址').locator('input')).toHaveValue(
    'http://models.example.test:8080/v1',
  )
  await expect(field('API Key').locator('input')).toHaveValue('')
  await configuration.getByRole('button', { name: '取消', exact: true }).click()
  await page.getByRole('link', { name: '邮箱与导入' }).click()
  await page.getByRole('tab', { name: '来源资料' }).click()
  const raw = Buffer.from(
    [
      'From: synthetic@example.test',
      'Subject: Synthetic P3 model review',
      'MIME-Version: 1.0',
      'Content-Type: text/html; charset=utf-8',
      '',
      '<html><body>招商银行 模型验收持卡人 5678 2026-09-01 2026-09-21 人民币 1000.00 900.00 100.00 合成商店 2026-08-20 30.00<script>window.p3Unsafe=true</script><img src="https://model-preview.example/tracker"></body></html>',
    ].join('\r\n'),
  )
  await page
    .getByLabel('选择资料文件')
    .setInputFiles({ name: 'p3-synthetic.eml', mimeType: 'message/rfc822', buffer: raw })
  const sourceRow = page.getByRole('row').filter({ hasText: 'Synthetic P3 model review' })
  await expect(sourceRow).toContainText('文本已准备', { timeout: 20000 })
  await sourceRow.getByRole('button', { name: '模型识别', exact: true }).click()
  await expect(page).toHaveURL(/models\?run=/)
  const candidateRow = page.getByRole('row').filter({ hasText: '模型验收持卡人' })
  await expect(candidateRow).toContainText('待核对', { timeout: 20000 })
  await expect(page.locator('.model-filter .el-select')).toContainText('全部候选')
  await page.locator('.model-filter .el-select').click()
  await page.getByRole('option', { name: '待核对', exact: true }).click()
  await expect(page.locator('.model-filter .el-select')).toContainText('待核对')
  await expect(candidateRow).toContainText('待核对')
  const filterBox = await page.locator('.model-filter .el-select__wrapper').boundingBox()
  expect(filterBox?.width).toBeGreaterThan(160)
  await page.screenshot({ path: '../docs/screenshots/models.png', fullPage: true })
  await candidateRow.getByRole('button', { name: '核对', exact: true }).click()
  const review = page.getByRole('dialog', { name: '候选核对档案' })
  await review.getByRole('tab', { name: '安全原文' }).click()
  await expect(review.locator('pre')).toContainText('合成商店')
  expect(external).toEqual([])
  expect(
    await page.evaluate(() => (window as Window & { p3Unsafe?: boolean }).p3Unsafe),
  ).toBeUndefined()
  await review.getByRole('tab', { name: '摘要与核对' }).click()
  const amount = review
    .locator('.el-form-item')
    .filter({ has: page.locator('.el-form-item__label', { hasText: '本期应还' }) })
    .locator('input')
  await amount.fill('1100.00')
  await expect(review.getByRole('button', { name: '确认发布账单' })).toBeDisabled()
  await review.getByLabel('核对说明').fill('合成模型候选人工修正')
  await review.getByRole('button', { name: '保存核对修改' }).click()
  await expect(review.getByText('候选 v2', { exact: false })).toBeVisible()
  await review.getByRole('tab', { name: '交易详单 · 2' }).click()
  await expect(review.locator('.model-transaction')).toHaveCount(2)
  await review.getByRole('button', { name: '确认发布账单' }).click()
  await page.locator('.el-message-box').getByRole('button', { name: '确定' }).click()
  await expect(page).toHaveURL(/statements\?detail=/)
  const archive = page.getByRole('dialog', { name: '账单档案' })
  await expect(archive).toContainText('记录待还 ¥ 1,100.00')
  await archive.getByRole('tab', { name: '交易详单' }).click()
  await expect(archive).toContainText('完整详单')
  await expect(archive.getByRole('row').filter({ hasText: '合成商店' })).toHaveCount(2)
  const statementId = new URL(page.url()).searchParams.get('detail')!
  const repayment = await page.request.post(`/api/v1/admin/statements/${statementId}/repayments`, {
    headers: { ...headers, 'Idempotency-Key': 'p3-repayment' },
    data: { amount: '100.00', currency: 'CNY', occurred_at: '2026-09-10T10:00:00+08:00' },
  })
  expect(repayment.status()).toBe(201)
  await archive.getByRole('button', { name: '关闭此对话框' }).click()
  await page.getByRole('link', { name: '模型与核对' }).click()
  await page.getByRole('button', { name: '配置模型', exact: true }).click()
  await field('HTTP / HTTPS 基础地址').locator('input').fill('http://models.example.test:8317/v1')
  await expect(field('API Key').locator('input')).toHaveValue('')
  await field('模型名称').locator('input').fill('synthetic-review-v2')
  await configuration.getByRole('button', { name: '保存模型配置' }).click()
  await expect(configuration).not.toBeVisible()
  await page.getByRole('link', { name: '邮箱与导入' }).click()
  await page.getByRole('tab', { name: '来源资料' }).click()
  await sourceRow.getByRole('button', { name: '重新识别', exact: true }).click()
  await page.locator('.el-message-box').getByRole('button', { name: '确定' }).click()
  await expect(page).toHaveURL(/models\?run=/)
  await expect(candidateRow).toContainText('待核对', { timeout: 20000 })
  await candidateRow.getByRole('button', { name: '核对', exact: true }).click()
  await expect(review).toContainText('已有账单 · v1')
  await review.getByLabel('核对说明').fill('按新模型结果修订，同一账单保留还款')
  await review.getByRole('button', { name: '确认修订并发布' }).click()
  await page.locator('.el-message-box').getByRole('button', { name: '确定' }).click()
  await expect(page).toHaveURL(new RegExp(`statements\\?detail=${statementId}`))
  await expect(archive).toContainText('记录待还 ¥ 800.00')
  await expect(archive).toContainText('已标记 ¥ 100.00')
  await archive.getByRole('button', { name: '关闭此对话框' }).click()
  await page.getByRole('link', { name: '模型与核对' }).click()
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  )
  await page.setViewportSize({ width: 1280, height: 900 })
  const currentConfig = (await (await page.request.get('/api/v1/admin/model-config')).json()).config
  expect(currentConfig.base_url).toBe('http://models.example.test:8317/v1')
  await page.getByRole('button', { name: '删除模型', exact: true }).click()
  await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click()
  await expect(page.getByRole('button', { name: '删除模型', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '配置模型', exact: true }).click()
  await configuration.getByRole('button', { name: '删除模型', exact: true }).click()
  await page
    .locator('.el-message-box')
    .getByRole('button', { name: '确认删除', exact: true })
    .click()
  await expect(configuration).not.toBeVisible()
  await expect(page.getByRole('heading', { name: '先配置你的模型服务' })).toBeVisible()
  await expect(page.getByRole('button', { name: '删除模型', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '测试连接', exact: true })).toBeDisabled()
  expect(
    (await (await page.request.get(`/api/v1/admin/statements/${statementId}`)).json()).id,
  ).toBe(statementId)
  await page.getByRole('button', { name: '配置模型', exact: true }).click()
  await expect(field('HTTP / HTTPS 基础地址').locator('input')).toHaveValue('')
  await expect(field('模型名称').locator('input')).toHaveValue('')
  await expect(field('API Key').locator('input')).toHaveValue('')
  await field('HTTP / HTTPS 基础地址').locator('input').fill('http://models.example.test:8080/v1')
  await field('模型名称').locator('input').fill('synthetic-readded')
  await field('API Key').locator('input').fill('browser-synthetic-new-key')
  await configuration.getByRole('button', { name: '保存模型配置' }).click()
  await expect(configuration).not.toBeVisible()
  await expect(page.getByRole('heading', { name: 'synthetic-readded' })).toBeVisible()
  expect(errors).toEqual([])
})
