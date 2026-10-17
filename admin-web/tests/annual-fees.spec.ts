import { test, expect, type Page } from '@playwright/test'
import type { Account, Card } from '../src/types'
import { nextAnnualFee } from '../src/utils/annual-fees'

const today = new Date(2026, 9, 17, 12)
const makeCard = (
  id: string,
  name: string,
  month: number | null,
  day: number | null,
  active = true,
): Card => ({
  id,
  name,
  last_four: id.padStart(4, '0'),
  active,
  credit_limit: '50000.00',
  annual_fee_month: month,
  annual_fee_day: day,
  annual_fee_benefits: '首年免年费\n每年刷满 6 笔免次年年费',
})
const fixtures: Account[] = [
  {
    id: 'a',
    group_id: 'g',
    name: '中行主账户',
    mode: 'merged',
    active: true,
    effective_active: true,
    statement_day: 12,
    bank_id: 'boc',
    bank_name: '中国银行',
    bank_code: 'boc',
    cardholder_id: 'h1',
    cardholder_name: '小牛',
    group_name: '中行',
    cards: [
      makeCard('1', '环球通', 10, 17),
      makeCard('2', '次年收取卡', 10, 16),
      makeCard('3', '白金卡', 11, 16),
      makeCard('4', '标准卡', 11, 17),
      makeCard('5', '待完善卡', null, null),
      makeCard('6', '停用旧卡', 10, 18, false),
      makeCard('7', '闰日卡', 2, 29),
    ],
  },
  {
    id: 'b',
    group_id: 'g2',
    name: '已停用账户',
    mode: 'independent',
    active: false,
    effective_active: false,
    statement_day: null,
    bank_id: 'cmb',
    bank_name: '招商银行',
    bank_code: 'cmb',
    cardholder_id: 'h2',
    cardholder_name: '小杨',
    group_name: '招行',
    cards: [makeCard('8', '上级停用卡', 10, 18)],
  },
]
async function mockPage(page: Page) {
  const data = structuredClone(fixtures)
  await page.clock.setFixedTime(today)
  await page.route('**/api/v1/auth/me', (route) =>
    route.fulfill({ json: { username: 'test-admin', csrf_token: 'test-csrf' } }),
  )
  // Separate pages ensure every account is loaded, including inactive ones.
  await page.route('**/api/v1/admin/accounts?*', (route) => {
    const p = Number(new URL(route.request().url()).searchParams.get('page'))
    return route.fulfill({ json: { items: data.slice(p - 1, p), total: data.length } })
  })
  await page.goto('/annual-fees')
  await expect(page.locator('.fee-row')).toHaveCount(8)
  return data
}
async function select(page: Page, name: string, option: string) {
  const dialog = page.getByRole('dialog', { name: '管理年费', exact: true })
  const scope = (await dialog.isVisible()) ? dialog : page
  await scope
    .locator('.el-select')
    .filter({ has: page.getByRole('combobox', { name, exact: true }) })
    .click()
  await page.getByRole('option', { name: option, exact: true }).click()
}

test('recurring annual fee dates handle today, year rollover, leap years and calendar-day boundaries', () => {
  expect(nextAnnualFee(null, null, today)).toBeNull()
  expect(nextAnnualFee(10, 17, today)).toEqual({ date: '2026-10-17', days: 0, adjusted: false })
  expect(nextAnnualFee(10, 16, today)?.date).toBe('2027-10-16')
  expect(nextAnnualFee(11, 16, today)?.days).toBe(30)
  expect(nextAnnualFee(11, 17, today)?.days).toBe(31)
  expect(nextAnnualFee(1, 1, new Date(2026, 11, 31, 23, 59))?.days).toBe(1)
  expect(nextAnnualFee(2, 29, today)).toEqual({ date: '2027-02-28', days: 134, adjusted: true })
  expect(nextAnnualFee(2, 29, new Date(2028, 1, 28))).toEqual({
    date: '2028-02-29',
    days: 1,
    adjusted: false,
  })
  expect(nextAnnualFee(2, 29, new Date(2028, 2, 1))?.date).toBe('2029-02-28')
  expect(nextAnnualFee(3, 9, new Date(2026, 2, 8, 23, 59))?.days).toBe(1)
})

test('annual fee navigation, all cards, counts, filters and inactive states', async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1600, height: 1100 })
  await mockPage(page)
  const nav = page.getByRole('navigation', { name: '主导航' })
  const links = await nav.getByRole('link').allTextContents()
  expect(links.indexOf('年费管理')).toBe(links.indexOf('账单管理') + 1)
  await expect(nav.getByRole('link', { name: '年费管理' })).toHaveAttribute('aria-current', 'page')
  await expect(page.locator('.fee-stat').nth(0).locator('strong')).toHaveText('8张')
  await expect(page.locator('.fee-stat').nth(1).locator('strong')).toHaveText('2张')
  await expect(page.locator('.fee-stat').nth(2).locator('strong')).toHaveText('1张')
  await expect(page.locator('.fee-stat').nth(3).locator('strong')).toHaveText('2张')
  const first = page.locator('.fee-row').first()
  await expect(first).toHaveAttribute('aria-label', '环球通 · 0001')
  await expect(first).toContainText('今天')
  await expect(page.getByRole('article', { name: '次年收取卡 · 0002', exact: true })).toContainText(
    '2027-10-16',
  )
  await expect(page.getByRole('article', { name: '闰日卡 · 0007', exact: true })).toContainText(
    '非闰年按 2 月 28 日估算',
  )
  await expect(
    page.getByRole('article', { name: '停用旧卡 · 0006', exact: true }).locator('.el-tag'),
  ).toHaveClass(/el-tag--danger/)
  await expect(page.getByRole('article', { name: '上级停用卡 · 0008', exact: true })).toContainText(
    '上级已停用',
  )
  await page.screenshot({ path: testInfo.outputPath('annual-fees-desktop.png'), fullPage: true })
  await select(page, '年费日期', '未来 30 天')
  await expect(page.locator('.fee-row')).toHaveCount(2)
  await page.getByRole('button', { name: '重置筛选' }).click()
  await select(page, '年费日期', '未设置日期')
  await expect(page.locator('.fee-row')).toHaveCount(1)
  await expect(page.locator('.fee-row')).toContainText('待完善卡')
  await page.getByRole('button', { name: '重置筛选' }).click()
  await select(page, '银行', '招商银行')
  await select(page, '持卡人', '小杨')
  await select(page, '卡片状态', '已停用（含上级）')
  await expect(page.locator('.fee-row')).toHaveCount(1)
  await expect(page.locator('.fee-row')).toContainText('上级停用卡')
  await page.getByRole('button', { name: '重置筛选' }).click()
  await page.getByLabel('搜索卡片').fill('0001')
  await expect(page.locator('.fee-row')).toHaveCount(1)
  await page.getByLabel('搜索卡片').fill('不存在')
  await expect(page.getByText('没有符合筛选条件的卡片')).toBeVisible()
  await page.getByRole('button', { name: '重置筛选' }).click()
  await expect(page.locator('.fee-row')).toHaveCount(8)
})

for (const width of [390, 320]) {
  test(`annual fee page and editor fit mobile (${width}px)`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 844 })
    await mockPage(page)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    const row = page.getByRole('article', { name: '环球通 · 0001', exact: true })
    const bounds = (await row.boundingBox())!
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width)
    await expect(row.getByText('年费优惠信息', { exact: true })).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath('annual-fees-mobile.png'), fullPage: true })
    await row.getByRole('button', { name: '管理年费 环球通 0001' }).click()
    const dialog = page.getByRole('dialog', { name: '管理年费', exact: true })
    await expect(dialog.locator('.el-select').nth(1)).toContainText('17 日')
    const panel = dialog.locator('.annual-fee-dialog')
    const db = (await panel.boundingBox())!
    expect(db.x).toBeGreaterThanOrEqual(0)
    expect(db.x + db.width).toBeLessThanOrEqual(width)
    expect(db.y + db.height).toBeLessThanOrEqual(844)
    expect(await panel.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true)
    await panel.screenshot({ path: testInfo.outputPath('annual-fee-editor-mobile.png') })
    await dialog.getByRole('button', { name: '取消', exact: true }).click()
    await expect(dialog).not.toBeVisible()
  })
}

test('load error is visible, retry works and an empty collection is explained', async ({
  page,
}) => {
  await page.route('**/api/v1/auth/me', (route) =>
    route.fulfill({ json: { username: 'test-admin', csrf_token: 'csrf' } }),
  )
  let fail = true
  await page.route('**/api/v1/admin/accounts?*', (route) =>
    route.fulfill(
      fail
        ? { status: 500, json: { error: { message: '加载失败，请重试' } } }
        : { json: { items: [], total: 0 } },
    ),
  )
  await page.goto('/annual-fees')
  await expect(page.getByRole('alert')).toContainText('加载失败，请重试')
  fail = false
  await page.getByRole('button', { name: '重新加载' }).click()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(page.getByText('暂无卡片，请先在“账户与卡片”中添加')).toBeVisible()
})

test('failed saves preserve edits and allow retry without changing unrelated card fields', async ({
  page,
}) => {
  const data = await mockPage(page)
  let fail = true
  await page.route('**/api/v1/admin/cards/1/annual-fee', async (route) => {
    const changes = route.request().postDataJSON()
    expect(changes).toEqual({
      annual_fee_month: 10,
      annual_fee_day: 17,
      annual_fee_benefits: '失败后仍保留的年费优惠',
    })
    await route.fulfill(
      fail
        ? { status: 500, json: { error: { message: '保存失败，请重试' } } }
        : { json: { ...data[0].cards[0], ...changes } },
    )
  })
  const row = page.getByRole('article', { name: '环球通 · 0001', exact: true })
  await row.getByRole('button', { name: '管理年费 环球通 0001' }).click()
  const dialog = page.getByRole('dialog', { name: '管理年费', exact: true })
  await dialog.getByLabel('年费优惠信息', { exact: true }).fill('失败后仍保留的年费优惠')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByText('保存失败，请重试', { exact: true })).toBeVisible()
  await expect(dialog.getByLabel('年费优惠信息', { exact: true })).toHaveValue(
    '失败后仍保留的年费优惠',
  )
  await expect(row.locator('.fee-benefits p')).toHaveText(data[0].cards[0].annual_fee_benefits!)
  fail = false
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  await expect(row.locator('.fee-benefits p')).toHaveText('失败后仍保留的年费优惠')
})
test('annual fee edits persist, synchronize with card details, cancel safely and clear dates', async ({
  page,
}) => {
  await page.goto('/login')
  await page.getByLabel('用户名', { exact: true }).fill('e2e-admin')
  await page.locator('#login-password').fill('browser-test-only-2026')
  await page.getByRole('button', { name: '进入工作台' }).click()
  await expect(page.getByRole('heading', { name: '让账单，井然有序。' })).toBeVisible()
  const session = await (await page.request.get('/api/v1/auth/me')).json()
  const headers = { Origin: new URL(page.url()).origin, 'X-CSRF-Token': session.csrf_token }
  async function create(resource: string, data: unknown) {
    const response = await page.request.post(`/api/v1/admin/${resource}`, { headers, data })
    expect(response.status()).toBe(201)
    return response.json()
  }
  const bank = (await (await page.request.get('/api/v1/admin/banks')).json()).items.find(
    (b: { code: string }) => b.code === 'boc',
  )
  const holder = await create('cardholders', { name: '年费模块测试持卡人' })
  const account = await create('accounts', {
    name: '年费模块测试账户',
    bank_id: bank.id,
    cardholder_id: holder.id,
    mode: 'independent',
  })
  const card = await create('cards', {
    account_id: account.id,
    name: '年费测试环球通',
    last_four: '3536',
    credit_limit: '80000.50',
    annual_fee_month: 10,
    annual_fee_day: 17,
  })
  expect(
    (
      await page.request.patch(`/api/v1/admin/accounts/${account.id}`, {
        headers,
        data: { name: account.name, active: false },
      })
    ).status(),
  ).toBe(200)
  await page.getByRole('link', { name: '年费管理', exact: true }).click()
  const row = page.getByRole('article', { name: '年费测试环球通 · 3536', exact: true })
  await expect(row).toContainText('上级已停用')
  await row.getByRole('button', { name: '管理年费 年费测试环球通 3536' }).click()
  const dialog = page.getByRole('dialog', { name: '管理年费', exact: true })
  await select(page, '年费月份', '2 月')
  await select(page, '年费日期', '29 日')
  const benefits = '首年免年费\n每年刷满 6 笔免次年年费\n<b>仅作为文本展示</b>'
  await dialog.getByLabel('年费优惠信息', { exact: true }).fill(benefits)
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  await expect(row).toContainText('每年 2 月 29 日')
  await expect(row.locator('.fee-benefits p')).toHaveText(benefits)
  await expect(row.locator('.fee-benefits b')).toHaveCount(0)
  await page.reload()
  await expect(row.locator('.fee-benefits p')).toHaveText(benefits)
  const savedAccount = (await (await page.request.get('/api/v1/admin/accounts')).json()).items.find(
    (a: { id: string }) => a.id === account.id,
  )
  expect(savedAccount.cards[0]).toMatchObject({
    id: card.id,
    name: card.name,
    last_four: '3536',
    active: true,
    credit_limit: '80000.50',
  })
  await page.getByRole('link', { name: '账户与卡片', exact: true }).click()
  await page.getByRole('button', { name: '年费测试环球通 · 3536', exact: true }).click()
  const details = page.getByRole('dialog', { name: '卡片详情', exact: true })
  await expect(details).toContainText('每年 2 月 29 日')
  await expect(details).toContainText('¥ 80,000.50')
  await expect(details).toContainText(benefits)
  await details.getByRole('button', { name: '关闭', exact: true }).click()
  await page.getByRole('link', { name: '年费管理', exact: true }).click()
  await row.getByRole('button', { name: '管理年费 年费测试环球通 3536' }).click()
  await dialog.getByLabel('年费优惠信息', { exact: true }).fill('取消时不保存')
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  await expect(row.locator('.fee-benefits p')).toHaveText(benefits)
  await row.getByRole('button', { name: '管理年费 年费测试环球通 3536' }).click()
  await select(page, '年费月份', '4 月')
  await expect(dialog.locator('.el-select').nth(1)).toContainText('29 日')
  await select(page, '年费月份', '1 月')
  await select(page, '年费日期', '31 日')
  await select(page, '年费月份', '4 月')
  await expect(dialog.locator('.el-select').nth(1)).toContainText('选择日期')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByText('请完整选择收取月份和日期，或清空月份以取消设置')).toBeVisible()
  const monthSelect = dialog.locator('.el-select').first()
  await monthSelect.hover()
  await monthSelect.locator('.el-select__clear').click()
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  await page.reload()
  await expect(row.locator('.fee-recurring')).toContainText('未设置')
  await expect(row.locator('.fee-benefits p')).toHaveText(benefits)
})
