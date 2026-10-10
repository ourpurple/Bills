import { test, expect, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/login')
  await page.getByLabel('用户名', { exact: true }).fill('e2e-admin')
  await page.locator('#login-password').fill('browser-test-only-2026')
  await page.getByRole('button', { name: '进入工作台' }).click()
  await expect(page.getByRole('heading', { name: '让账单，井然有序。' })).toBeVisible()
}
function item(page: Page, label: string) {
  return page
    .getByRole('dialog')
    .locator('.el-form-item')
    .filter({ has: page.locator('.el-form-item__label', { hasText: label }) })
}
async function fill(page: Page, label: string, value: string) {
  await item(page, label).locator('input,textarea').first().fill(value)
}
async function select(page: Page, label: string, option: string) {
  await item(page, label).locator('.el-select').click()
  await page.getByRole('option', { name: option, exact: false }).last().click()
}

test('manual account → draft → confirm → repayment → revise → overview', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await login(page)
  // The suite shares one isolated test database; other scenarios may already have bills.
  const before = await (await page.request.get('/api/v1/admin/overview')).json()
  const baseline = Number(
    before.totals.find((t: { currency: string }) => t.currency === 'CNY')?.amount ?? '0',
  )
  await page.getByRole('link', { name: '账户与卡片' }).click()
  await page.getByRole('tab', { name: '持卡人', exact: true }).click()
  await page.getByRole('button', { name: '新增持卡人' }).click()
  await fill(page, '名称', '浏览器测试')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await expect(page.getByRole('tab', { name: '账户分组', exact: true })).toHaveCount(0)
  await page.getByRole('tab', { name: '还款账户', exact: true }).click()
  await page.getByRole('button', { name: '新增账户' }).click()
  await fill(page, '名称', '合并测试账户')
  await select(page, '银行', '招商银行')
  await select(page, '持卡人', '浏览器测试')
  await expect(item(page, '所属分组')).toHaveCount(0)
  await page.screenshot({ path: 'test-results/account-create-direct.png', fullPage: true })
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await expect(page.getByRole('heading', { name: '合并测试账户' })).toBeVisible()
  await page.getByRole('button', { name: '添加卡片' }).first().click()
  await fill(page, '名称', '测试卡片')
  await fill(page, '卡片尾号', '1234')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await page.getByRole('link', { name: '账单管理' }).click()
  await page.getByRole('button', { name: '录入账单', exact: true }).click()
  await select(page, '还款账户', '合并测试账户')
  await fill(page, '账单日期', '2026-09-01')
  await fill(page, '本期应还', '1000.00')
  await fill(page, '最低还款', '100.00')
  await fill(page, '到期日', '2026-09-21')
  await item(page, '到期日').locator('input').press('Tab')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByRole('button', { name: '确认账单' })).toBeVisible()
  await page.getByRole('button', { name: '确认账单' }).click()
  await page.locator('.el-message-box input').fill('与原账单核对')
  await page.locator('.el-message-box').getByRole('button', { name: '确定' }).click()
  await expect(page.getByRole('button', { name: '记录还款', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '记录还款', exact: true }).click()
  await fill(page, '金额', '200.00')
  await page.getByRole('button', { name: '标记已还款' }).click()
  await expect(page.getByText('记录待还 ¥ 800.00', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '修订账单' }).click()
  await fill(page, '本期应还', '1200.00')
  await item(page, '录入 / 修订原因').locator('textarea').fill('金额修正测试')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByText('记录待还 ¥ 1,000.00', { exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await page.getByRole('link', { name: '账单概览', exact: true }).click()
  await expect(page.locator('.total-number')).toContainText(
    (baseline + 1000).toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }),
  )
  await page.screenshot({ path: 'test-results/overview-desktop.png', fullPage: true })
  expect(errors).toEqual([])
})

test('accounts select ownership directly, reuse internal group and preserve it on edit', async ({
  page,
}) => {
  const groupRequests: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('/admin/account-groups')) groupRequests.push(request.url())
  })
  await login(page)
  await page.getByRole('link', { name: '账户与卡片' }).click()
  await expect(page.getByRole('tab', { name: '账户分组', exact: true })).toHaveCount(0)
  await page.getByRole('tab', { name: '持卡人', exact: true }).click()
  await page.getByRole('button', { name: '新增持卡人' }).click()
  await fill(page, '名称', '直接归属测试')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await page.getByRole('tab', { name: '还款账户', exact: true }).click()
  await page.getByRole('button', { name: '新增账户' }).click()
  await fill(page, '名称', '直接归属一')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByText('请选择银行和持卡人', { exact: true })).toBeVisible()
  await expect(page.getByRole('dialog')).toBeVisible()
  await select(page, '银行', '招商银行')
  await select(page, '持卡人', '直接归属测试')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await page.getByRole('button', { name: '新增账户' }).click()
  await fill(page, '名称', '直接归属二')
  await select(page, '银行', '招商银行')
  await select(page, '持卡人', '直接归属测试')
  await select(page, '还款模式', '独立还款')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  const before = await (await page.request.get('/api/v1/admin/accounts')).json()
  const first = before.items.find((a: { name: string }) => a.name === '直接归属一')
  const second = before.items.find((a: { name: string }) => a.name === '直接归属二')
  expect(first.group_id).toBe(second.group_id)
  expect(first.id).not.toBe(second.id)
  expect(second.mode).toBe('independent')
  await page
    .locator('.account-card')
    .filter({ has: page.getByRole('heading', { name: '直接归属一', exact: true }) })
    .getByRole('button', { name: '编辑', exact: true })
    .click()
  await expect(item(page, '银行')).toHaveCount(0)
  await expect(item(page, '持卡人')).toHaveCount(0)
  await fill(page, '名称', '直接归属一已编辑')
  await page.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible()
  await expect(page.getByRole('heading', { name: '直接归属一已编辑' })).toBeVisible()
  const after = await (await page.request.get('/api/v1/admin/accounts')).json()
  const edited = after.items.find((a: { id: string }) => a.id === first.id)
  expect(edited.group_id).toBe(first.group_id)
  expect(edited.bank_id).toBe(first.bank_id)
  expect(edited.cardholder_id).toBe(first.cardholder_id)
  expect(groupRequests).toEqual([])
  await page.screenshot({ path: 'test-results/accounts-without-groups.png', fullPage: true })
})

test('authentication recovery and narrow screen layout', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/settings')
  await expect(page).toHaveURL(/login/)
  await login(page)
  await page.getByRole('link', { name: '安全与设置' }).click()
  await expect(page.getByRole('heading', { name: '你的数据，你来掌握。' })).toBeVisible()
  await page.getByRole('button', { name: '生成配对码' }).click()
  await expect(page.getByText('一次性配对码 · 10 分钟有效')).toBeVisible()
  await page.screenshot({ path: 'test-results/settings-mobile.png', fullPage: true })
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  )
  expect(overflow).toBe(false)
  await page.context().clearCookies()
  await page.reload()
  await expect(page).toHaveURL(/login/)
})
