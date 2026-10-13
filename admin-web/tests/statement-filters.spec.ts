import { test, expect, type Page } from '@playwright/test'

const accounts = [
  {
    id: 'gf',
    name: '账户名称不是卡片名称',
    mode: 'independent',
    bank_id: 'gfb',
    bank_name: '广发银行',
    cardholder_id: 'niu',
    cardholder_name: '牛整辉',
    cards: [{ id: 'gf-card', name: '臻享白', last_four: '9759', active: true }],
  },
  {
    id: 'cmb',
    name: '合并还款账户',
    mode: 'merged',
    bank_id: 'cmbb',
    bank_name: '招商银行',
    cardholder_id: 'yang',
    cardholder_name: '杨小宾',
    cards: [
      { id: 'cmb-card', name: '经典白', last_four: '9999', active: true },
      { id: 'cmb-card2', name: '普卡', last_four: '9166', active: false },
    ],
  },
  {
    id: 'abc',
    name: '农业银行账户',
    mode: 'independent',
    bank_id: 'abcb',
    bank_name: '农业银行',
    cardholder_id: 'niu',
    cardholder_name: '牛整辉',
    cards: [{ id: 'abc-card', name: '悠然白', last_four: '8753', active: true }],
  },
]
const statements = Array.from({ length: 24 }, (_, i) => ({
  id: `s-${i}`,
  account: accounts[i % accounts.length],
  statement_date: i < 12 ? '2026-10-06' : '2026-09-06',
  due_date: i < 12 ? '2026-10-26' : '2026-09-26',
  currency: i % 2 ? 'USD' : 'CNY',
  amount: i % 4 === 0 ? '0.00' : '4115.82',
  remaining: i % 4 === 0 ? '0.00' : '4115.82',
  status: 'confirmed',
  payment_status: i % 4 === 0 ? 'settled' : 'unpaid',
  overdue: false,
}))

async function setup(page: Page, url = '/statements') {
  await page.route('**/api/v1/auth/me', (route) =>
    route.fulfill({ json: { username: 'test-admin', csrf_token: 'test-csrf' } }),
  )
  await page.route('**/api/v1/admin/accounts?*', (route) =>
    route.fulfill({ json: { items: accounts, total: accounts.length } }),
  )
  await page.route('**/api/v1/admin/statements?*', (route) => {
    const p = new URL(route.request().url()).searchParams
    let items = statements.filter(
      (s) =>
        (!p.get('account_id') || s.account.id === p.get('account_id')) &&
        (!p.get('bank_id') || s.account.bank_id === p.get('bank_id')) &&
        (!p.get('cardholder_id') || s.account.cardholder_id === p.get('cardholder_id')) &&
        (!p.get('currency') || s.currency === p.get('currency')) &&
        (!p.get('status') || s.status === p.get('status')) &&
        (!p.get('payment_state') || s.payment_status === p.get('payment_state')) &&
        (!p.get('keyword') || JSON.stringify(s.account).includes(p.get('keyword')!)),
    )
    const field = p.get('date_field') === 'due_date' ? 'due_date' : 'statement_date'
    items = items.filter(
      (s) =>
        (!p.get('date_from') || s[field] >= p.get('date_from')!) &&
        (!p.get('date_to') || s[field] <= p.get('date_to')!),
    )
    const total = items.length
    const start = (Number(p.get('page')) - 1) * 20
    return route.fulfill({ json: { items: items.slice(start, start + 20), total } })
  })
  await page.goto(url)
  await expect(page.locator('.statement-filter-footer')).toContainText('24 份账单')
}
async function choose(page: Page, label: string, option: string) {
  await page
    .locator('.el-select')
    .filter({ has: page.getByRole('combobox', { name: label, exact: true }) })
    .click()
  await page.getByRole('option', { name: option, exact: true }).click()
}

for (const width of [1440, 390]) {
  test(`statement filters layout and search (${width}px)`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await setup(page)
    for (const selector of ['.statement-filters', '.filter-search', '.filter-card']) {
      const bounds = (await page.locator(selector).boundingBox())!
      expect(bounds.x).toBeGreaterThanOrEqual(0)
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width)
    }
    await page.getByRole('button', { name: '更多筛选', exact: true }).click()
    await expect(page.getByRole('combobox', { name: '币种筛选' })).toBeVisible()
    const dateBounds = (await page.locator('.filter-date-controls').boundingBox())!
    expect(dateBounds.x + dateBounds.width).toBeLessThanOrEqual(width)
    await page.locator('.filter-card .el-select').click()
    const options = page.locator('.el-select__popper.statement-card-options:visible')
    await expect(options).toBeVisible()
    const optionBounds = (await options.boundingBox())!
    expect(optionBounds.x).toBeGreaterThanOrEqual(0)
    expect(optionBounds.x + optionBounds.width).toBeLessThanOrEqual(width)
    await page.keyboard.press('Escape')
    await page.screenshot({
      path: testInfo.outputPath(`statement-filters-${width}.png`),
      fullPage: true,
      animations: 'disabled',
    })
    await page
      .locator('.statement-filters')
      .screenshot({ path: testInfo.outputPath(`filters-bar-${width}.png`), animations: 'disabled' })
    await page.getByRole('textbox', { name: '搜索账单', exact: true }).fill('9166')
    await page.getByRole('button', { name: '搜索', exact: true }).click()
    await expect(page.locator('.statement-filter-footer')).toContainText('8 份账单')
    await expect(page.locator('.statement-identity')).toHaveCount(8)
    await expect(page.locator('.statement-identity').first()).toContainText('经典白')
    await page.getByRole('button', { name: '重置筛选' }).click()
    await expect(page.locator('.statement-filter-footer')).toContainText('24 份账单')
  })
}

test('bank and holder narrow card choices and clear an incompatible card', async ({ page }) => {
  await setup(page)
  await choose(page, '卡片筛选', '经典白 · 尾号 9999 / 普卡 · 尾号 9166 · 招商银行 · 杨小宾')
  await expect(page.locator('.filter-note')).toContainText('不按卡片拆分金额')
  await choose(page, '银行筛选', '广发银行')
  await expect(page.locator('.filter-card')).toContainText('全部卡片')
  await expect(page.locator('.statement-filter-footer')).toContainText('8 份账单')
  await page.getByRole('combobox', { name: '卡片筛选' }).click()
  await expect(page.getByRole('option', { name: /臻享白/ })).toBeVisible()
  await expect(page.getByRole('option', { name: /经典白/ })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await choose(page, '持卡人筛选', '杨小宾')
  await expect(page.locator('.statement-filter-footer')).toContainText('0 份账单')
  await expect(page.getByText('没有符合条件的账单，试试调整或重置筛选。')).toBeVisible()
})

test('pagination resets and repayment states match the query', async ({ page }) => {
  await setup(page)
  await page.locator('.pagination .btn-next').click()
  await expect(page.locator('.statement-identity')).toHaveCount(4)
  const request = page.waitForRequest(
    (r) => r.url().includes('/admin/statements?') && r.url().includes('payment_state=settled'),
  )
  await choose(page, '账单状态筛选', '已标记结清')
  expect(new URL((await request).url()).searchParams.get('page')).toBe('1')
  await expect(page.locator('.statement-filter-footer')).toContainText('6 份账单')
  await expect(page.locator('.statement-identity')).toHaveCount(6)
  await choose(page, '账单状态筛选', '待确认')
  await expect(page.locator('.statement-filter-footer')).toContainText('0 份账单')
  await page.getByRole('button', { name: '重置筛选' }).click()
  await expect(page.locator('.statement-identity')).toHaveCount(20)
})

test('advanced currency and due-date filters combine and reset', async ({ page }) => {
  await setup(page)
  await page.getByRole('button', { name: '更多筛选', exact: true }).click()
  await choose(page, '币种筛选', '美元 USD')
  await choose(page, '日期类型', '到期日')
  await page.getByPlaceholder('开始日期', { exact: true }).fill('2026-10-26')
  await page.getByPlaceholder('结束日期', { exact: true }).fill('2026-10-26')
  const request = page.waitForRequest((r) => r.url().includes('date_from=2026-10-26'))
  await page.getByPlaceholder('结束日期', { exact: true }).press('Tab')
  await page.locator('.statement-filter-footer').click()
  const params = new URL((await request).url()).searchParams
  expect(params.get('date_field')).toBe('due_date')
  expect(params.get('date_to')).toBe('2026-10-26')
  expect(params.get('currency')).toBe('USD')
  await expect(page.locator('.statement-filter-footer')).toContainText('6 份账单')
  await page.getByRole('button', { name: '收起筛选' }).click()
  await expect(page.locator('.filter-count')).toHaveText('已选 2 项条件')
  await page.getByRole('button', { name: '重置筛选' }).click()
  await expect(page.locator('.statement-filter-footer')).toContainText('24 份账单')
})

test('overview status deep link is preserved', async ({ page }) => {
  await page.route('**/api/v1/auth/me', (route) =>
    route.fulfill({ json: { username: 'test-admin', csrf_token: 'test-csrf' } }),
  )
  await page.route('**/api/v1/admin/accounts?*', (route) =>
    route.fulfill({ json: { items: accounts, total: accounts.length } }),
  )
  await page.route('**/api/v1/admin/statements?*', (route) => {
    expect(new URL(route.request().url()).searchParams.get('status')).toBe('draft')
    return route.fulfill({ json: { items: [], total: 0 } })
  })
  await page.goto('/statements?status=draft')
  await expect(page.locator('.filter-count')).toHaveText('已选 1 项条件')
  await expect(page.locator('.statement-filter-grid .filter-field').last()).toContainText('待确认')
})

for (const width of [1440, 390]) {
  test(`overview view filters all statements for the selected account (${width}px)`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 })
    await setup(page)
    await page.route('**/api/v1/admin/overview', (route) =>
      route.fulfill({
        json: {
          totals: [{ currency: 'CNY', amount: '4115.82' }],
          latest_statements: statements.slice(0, accounts.length),
          historical_unresolved: [],
          warnings: [],
          account_count: accounts.length,
          pending_count: 0,
          due_soon_count: 0,
          overdue_count: 0,
        },
      }),
    )
    for (const account of accounts) {
      await page.getByRole('link', { name: '账单概览', exact: true }).click()
      const request = page.waitForRequest((request) => {
        const url = new URL(request.url())
        return (
          url.pathname === '/api/v1/admin/statements' &&
          url.searchParams.get('account_id') === account.id
        )
      })
      await page
        .locator('.bill-row')
        .filter({ hasText: account.bank_name })
        .getByRole('button', { name: '查看', exact: true })
        .click()
      const params = new URL((await request).url()).searchParams
      expect([...params.keys()].sort()).toEqual(['account_id', 'page', 'page_size'])
      expect(params.get('page')).toBe('1')
      await expect(page).toHaveURL(`/statements?account_id=${account.id}`)
      await expect(page.locator('.filter-card')).toContainText(account.cards[0].last_four)
      const expected = statements.filter((s) => s.account.id === account.id)
      await expect(page.locator('.statement-filter-footer')).toContainText(
        `${expected.length} 份账单`,
      )
      await expect(page.locator('.statement-identity')).toHaveCount(expected.length)
      await expect(page.locator('.statement-account')).toHaveText(
        expected.map(() => `${account.bank_name} · ${account.cardholder_name}`),
      )
      await expect(page.locator('.el-drawer')).not.toBeVisible()
      await page.reload()
      await expect(page.locator('.statement-filter-footer')).toContainText(
        `${expected.length} 份账单`,
      )
      await expect(page.locator('.filter-card')).toContainText(account.cards[0].last_four)
    }
    await page.getByRole('link', { name: '账单管理', exact: true }).click()
    await expect(page.locator('.statement-filter-footer')).toContainText('24 份账单')
    await expect(page.locator('.filter-card')).toContainText('全部卡片')
    await choose(page, '卡片筛选', '臻享白 · 尾号 9759 · 广发银行 · 牛整辉')
    await expect(page.locator('.statement-filter-footer')).toContainText('8 份账单')
    await page.getByRole('button', { name: '重置筛选' }).click()
    await expect(page.locator('.statement-filter-footer')).toContainText('24 份账单')
  })
}
