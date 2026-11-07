import { test, expect, type Page } from '@playwright/test'
import type { Account, OverviewStatement } from '../src/types'

const account: Account = {
  id: 'ledger-account',
  group_id: 'ledger-group',
  name: '日常消费合并还款账户',
  mode: 'merged',
  statement_day: 12,
  active: true,
  effective_active: true,
  bank_id: 'cmb',
  bank_name: '招商银行',
  bank_code: 'cmb',
  cardholder_id: 'holder',
  cardholder_name: '演示持卡人',
  group_name: '日常账簿',
  cards: [
    {
      id: 'ledger-card',
      name: '全币种旅行白金信用卡 · 长名称换行与多卡身份验证',
      last_four: '0826',
      active: true,
      track_bills: true,
      credit_limit: '100000.00',
      has_card_number: true,
      has_cvv: true,
      card_type: 'credit',
      card_network: 'visa',
      annual_fee_month: 11,
      annual_fee_day: 18,
      annual_fee_benefits: '首年免年费\n每年刷满 6 笔免次年年费，优惠条件请自行核实。',
    },
    {
      id: 'second-card',
      name: '经典白金卡',
      last_four: '9712',
      active: true,
      credit_limit: null,
      annual_fee_month: null,
      annual_fee_day: null,
      annual_fee_benefits: null,
    },
  ],
}
const statement: OverviewStatement = {
  id: 'ledger-statement',
  account,
  statement_date: '2026-10-12',
  currency: 'CNY',
  status: 'confirmed',
  version: 1,
  source_kind: 'manual',
  amount: '12345678.90',
  minimum: '100.00',
  due_date: '2026-11-10',
  period_start: null,
  period_end: null,
  paid: '0.00',
  remaining: '12345678.90',
  excess: '0.00',
  payment_status: 'unpaid',
  days_until_due: 3,
  overdue: false,
  transaction_status: 'unavailable',
  repayments: [],
  plans: [],
  revisions: [],
  needs_update: true,
  expected_statement_date: '2026-11-12',
}
const pages = [
  ['/', '让账单，井然有序。'],
  ['/statements', '每期账单，清楚留档。'],
  ['/annual-fees', '年费有期，优惠有据。'],
  ['/imports', '邮件中心'],
  ['/models', '模型整理，你来定稿。'],
  ['/tasks', '所有任务，统一查看。'],
  ['/accounts', '账户与卡片，妥善归集。'],
  ['/settings', '你的数据，你来掌握。'],
] as const

async function fixtures(page: Page, populated = false) {
  await page.clock.setFixedTime(new Date(2026, 10, 7, 12))
  await page.route('**/api/v1/auth/me', (route) =>
    route.fulfill({ json: { username: 'ledger-admin', csrf_token: 'layout-only' } }),
  )
  await page.route('**/api/v1/auth/logout', (route) => route.fulfill({ json: {} }))
  await page.route('**/api/v1/admin/**', (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1/admin/', '')
    if (path === 'overview')
      return route.fulfill({
        json: {
          totals: populated
            ? [
                { currency: 'CNY', amount: '12345678.90' },
                { currency: 'USD', amount: '890.12' },
              ]
            : [],
          latest_statements: populated
            ? [
                statement,
                {
                  ...statement,
                  id: 'settled',
                  currency: 'USD',
                  needs_update: false,
                  payment_status: 'settled',
                  remaining: '0.00',
                },
              ]
            : [],
          historical_unresolved: populated ? [statement] : [],
          warnings: populated
            ? [
                {
                  account_id: account.id,
                  account_name: account.name,
                  kind: 'missing',
                  message: '本期账单需要更新，请与原始账单核对。',
                },
              ]
            : [],
          account_count: populated ? 1 : 0,
          pending_count: populated ? 2 : 0,
          due_soon_count: populated ? 1 : 0,
          overdue_count: 0,
          as_of: '2026-11-07',
          disclaimer: '仅记录用户确认的账单。',
        },
      })
    if (path === 'import-status')
      return route.fulfill({
        json: {
          worker_online: false,
          master_key_configured: true,
          last_heartbeat: null,
          ready_sources: 0,
          tasks: {},
        },
      })
    if (path === 'model-config')
      return route.fulfill({
        json: {
          config: {
            configured: false,
            version: 1,
            daily_call_limit: 50,
            auto_publish_profiles: [],
          },
          master_key_configured: true,
          pending_candidates: 0,
          usage: {
            reserved_calls: 0,
            unknown_cost_calls: 0,
            cost_estimates: {},
            day_timezone: 'Asia/Shanghai',
          },
        },
      })
    if (path === `statements/${statement.id}`) return route.fulfill({ json: statement })
    const items =
      populated && path === 'accounts'
        ? [account]
        : populated && path === 'statements'
          ? [statement]
          : path === 'banks'
            ? [{ id: 'cmb', code: 'cmb', name: '招商银行', active: true, default_mode: 'merged' }]
            : path === 'cardholders'
              ? [{ id: 'holder', name: '演示持卡人', active: true }]
              : []
    return route.fulfill({ json: { items, total: items.length } })
  })
}
async function noOverflow(page: Page) {
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    .toBe(true)
  for (const element of await page
    .locator('main .panel, main .page-heading, main .summary-grid')
    .all()) {
    const bounds = (await element.boundingBox())!
    expect(bounds.x).toBeGreaterThanOrEqual(0)
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(page.viewportSize()!.width + 1)
  }
}

for (const width of [1440, 1024, 768, 390, 320]) {
  test(`all routes, empty states and login fit the viewport (${width}px)`, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.setViewportSize({ width, height: 1000 })
    await fixtures(page)
    for (const [path] of pages) {
      await page.goto(path)
      await expect(page.locator('main h1')).toBeVisible()
      await expect(page.locator('.el-loading-mask')).toHaveCount(0)
      await noOverflow(page)
      if (path === '/imports' || path === '/tasks') {
        const label = path === '/imports' ? '邮件显示范围' : '任务状态筛选'
        const option = path === '/imports' ? '全部邮件' : '失败'
        await page
          .locator('.el-select')
          .filter({ has: page.getByRole('combobox', { name: label }) })
          .click()
        await expect(page.getByRole('option', { name: option, exact: true })).toBeVisible()
        await page.getByRole('option', { name: option, exact: true }).click()
        await noOverflow(page)
      }
      if (width >= 1024) {
        await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
        await expect(page.getByRole('navigation').locator('[aria-current="page"]')).toHaveCount(1)
      } else {
        await expect(page.getByRole('button', { name: '打开主导航' })).toBeVisible()
      }
    }
    await page.goto('/login')
    await expect(page.getByLabel('用户名', { exact: true })).toBeVisible()
    await expect(page.getByLabel('密码', { exact: true })).toBeVisible()
    await noOverflow(page)
    expect(errors).toEqual([])
  })

  test(`long identities, large amounts, currencies and drawers stay readable (${width}px)`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 })
    await fixtures(page, true)
    await page.goto('/')
    await expect(page.locator('.bill-row')).toHaveCount(2)
    await expect(page.locator('.foreign-totals')).toContainText('USD')
    await expect(page.locator('.warning-strip')).toContainText('需要留意')
    await expect(page.locator('.history-notice')).toBeVisible()
    await noOverflow(page)
    for (const row of await page.locator('.bill-row').all()) {
      const amount = (await row.locator('.bill-amount').boundingBox())!
      const deadline = (await row.locator('.bill-deadline').boundingBox())!
      const button = (await row.getByRole('button', { name: '查看' }).boundingBox())!
      const number = (await row.locator('.bill-amount strong').boundingBox())!
      expect(
        deadline.x + deadline.width <= amount.x || deadline.y + deadline.height <= amount.y,
      ).toBe(true)
      expect(number.x + number.width <= button.x + 1 || number.y + number.height <= button.y).toBe(
        true,
      )
    }
    await page.screenshot({
      path: testInfo.outputPath(`ledger-overview-${width}.png`),
      fullPage: true,
      animations: 'disabled',
    })
    await page.goto('/statements')
    await expect(page.locator('.el-table__body tr')).toHaveCount(1)
    await noOverflow(page)
    await page.locator('.el-table .el-scrollbar__wrap').evaluate((element) => {
      element.scrollLeft = element.scrollWidth
    })
    await page.getByRole('button', { name: '详情', exact: true }).click()
    const drawer = page.getByRole('dialog', { name: '账单档案' })
    await expect(drawer).toBeVisible()
    await expect
      .poll(async () => {
        const bounds = (await drawer.boundingBox())!
        return bounds.x >= 0 && bounds.x + bounds.width <= width + 1
      })
      .toBe(true)
    await expect(drawer.locator('.detail-summary')).toContainText('12,345,678.90')
    await page.keyboard.press('Escape')
    await expect(drawer).not.toBeVisible()
    await page.goto('/annual-fees')
    await expect(page.locator('.fee-row')).toHaveCount(2)
    await noOverflow(page)
    await page
      .getByRole('button', { name: /管理年费/ })
      .first()
      .click()
    await expect(page.getByRole('dialog', { name: '管理年费' })).toBeVisible()
    await noOverflow(page)
    await page.keyboard.press('Escape')
    await page.goto('/accounts')
    await expect(page.locator('.bank-card-table .el-table__body tr')).toHaveCount(2)
    await noOverflow(page)
    await page.screenshot({
      path: testInfo.outputPath(`ledger-accounts-${width}.png`),
      fullPage: true,
      animations: 'disabled',
    })
  })
}

for (const width of [768, 390, 320]) {
  test(`mobile navigation traps focus, closes and preserves logout (${width}px)`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 844 })
    await fixtures(page)
    await page.goto('/')
    const trigger = page.getByRole('button', { name: '打开主导航' })
    const menu = page.getByRole('dialog', { name: '我的账单工作台' })
    await expect(trigger).toHaveAttribute('aria-expanded', 'false')
    await trigger.click()
    await expect(menu).toBeVisible()
    await expect(trigger).toHaveAttribute('aria-expanded', 'true')
    await expect(menu.getByRole('navigation').getByRole('link')).toHaveCount(8)
    await expect(menu.getByRole('button', { name: '退出登录' })).toBeVisible()
    for (let i = 0; i < 15; i++) {
      await page.keyboard.press('Tab')
      expect(await menu.evaluate((element) => element.contains(document.activeElement))).toBe(true)
    }
    await page.screenshot({
      path: testInfo.outputPath(`ledger-navigation-${width}.png`),
      fullPage: true,
      animations: 'disabled',
    })
    await page.keyboard.press('Escape')
    await expect(menu).not.toBeVisible()
    await expect(trigger).toBeFocused()
    await trigger.click()
    await menu.getByRole('link', { name: '安全与设置' }).click()
    await expect(page).toHaveURL('/settings')
    await expect(menu).not.toBeVisible()
    await expect(trigger).toBeFocused()
    await trigger.click()
    await menu.getByRole('link', { name: '安全与设置' }).click()
    await expect(menu).not.toBeVisible()
    await trigger.click()
    await page.mouse.click(width - 4, 200)
    await expect(menu).not.toBeVisible()
    await trigger.click()
    await page.setViewportSize({ width: 1440, height: 900 })
    await expect(menu).toHaveCount(0)
    await expect(page.locator('.sidebar')).toBeVisible()
    await expect(page.locator('body')).not.toHaveClass(/el-popup-parent--hidden/)
    await page.setViewportSize({ width, height: 844 })
    await trigger.click()
    await page.getByRole('button', { name: '退出登录' }).click()
    await expect(page).toHaveURL('/login')
    await expect(menu).toHaveCount(0)
  })
}

test('overview shows a recoverable error and respects reduced motion', async ({ page }) => {
  await fixtures(page)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  let failed = true
  await page.route('**/api/v1/admin/overview', async (route) => {
    if (failed) return route.fulfill({ status: 503, json: { error: { message: '服务暂不可用' } } })
    await route.fallback()
  })
  await page.goto('/')
  await expect(page.getByText('账单概况暂时无法加载', { exact: true })).toBeVisible()
  await expect(page.locator('.page-heading')).toHaveCSS('animation-name', 'none')
  failed = false
  await page.getByRole('button', { name: '重新加载' }).click()
  await expect(page.locator('.summary-grid')).toBeVisible()
  await expect(page.getByText('还没有已确认账单。先添加账户，再录入第一份账单。')).toBeVisible()
})
