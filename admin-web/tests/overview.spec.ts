import { test, expect } from '@playwright/test'

for (const width of [1440, 390]) {
  test(`overview highlights unpaid bills due within seven days (${width}px)`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 })
    const cases = [
      { id: 'today', days: 0, urgent: true },
      { id: 'three-days', days: 3, urgent: true },
      { id: 'four-days', days: 4, urgent: true },
      { id: 'seven-days', days: 7, urgent: true },
      { id: 'eight-days', days: 8, urgent: false },
      { id: 'nine-days', days: 9, urgent: false },
      { id: 'unknown', days: null, urgent: false },
      { id: 'settled', days: 4, remaining: '0.00', urgent: false },
      { id: 'settled-usd', days: -12, remaining: '0.00', urgent: false },
      { id: 'overdue', days: -1, urgent: true },
    ]
    const latest = cases.map((c) => ({
      id: c.id,
      account: {
        bank_name: `测试银行 ${c.id}`,
        bank_code: 'test',
        cardholder_name: '测试用户',
        name: c.id,
        mode: 'independent',
        cards: [],
      },
      statement_date: '2026-10-01',
      currency: c.id === 'settled-usd' ? 'USD' : 'CNY',
      status: 'confirmed',
      remaining: c.remaining ?? '100.00',
      payment_status: c.remaining === '0.00' ? 'settled' : 'unpaid',
      days_until_due: c.days,
      due_date: c.days === null ? null : `2026-10-${String(13 + c.days).padStart(2, '0')}`,
      overdue: c.days !== null && c.days < 0 && c.remaining !== '0.00',
    }))
    await page.route('**/api/v1/auth/me', (route) =>
      route.fulfill({ json: { username: 'test-admin', csrf_token: 'test-csrf' } }),
    )
    await page.route('**/api/v1/admin/overview', (route) =>
      route.fulfill({
        json: {
          totals: [{ currency: 'CNY', amount: '800.00' }],
          latest_statements: latest,
          historical_unresolved: [],
          warnings: [],
          account_count: cases.length,
          pending_count: 0,
          due_soon_count: 4,
          overdue_count: 1,
          disclaimer: '测试账单',
          as_of: '2026-10-13',
        },
      }),
    )
    await page.goto('/')
    await expect(page.locator('.bill-row')).toHaveCount(cases.length)
    for (const c of cases) {
      const row = page.locator('.bill-row').filter({
        has: page.getByText(`测试银行 ${c.id} · 测试用户`, { exact: true }),
      })
      const amount = row.locator('.bill-amount')
      const deadline = row.locator('.bill-deadline')
      if (c.remaining === '0.00') {
        const tag = deadline.locator('.el-tag--success')
        await expect(tag).toHaveText('已标记结清')
        await expect(tag).toHaveCSS('color', 'rgb(103, 194, 58)')
        await expect(tag).toHaveCSS('background-color', 'rgb(240, 249, 235)')
        await expect(tag).toHaveCSS('border-top-color', 'rgb(225, 243, 216)')
        await expect(tag).toHaveCSS('font-size', '12px')
        await expect(deadline.locator('.bill-due-date')).toHaveText(
          latest.find((s) => s.id === c.id)!.due_date!,
        )
      } else {
        await expect(deadline.locator('.el-tag')).toHaveCount(0)
      }
      if (c.urgent) {
        await expect(amount).toHaveClass(/urgent/)
        await expect(deadline).toHaveClass(/urgent/)
        for (const target of [
          amount.locator('strong'),
          deadline.locator('strong'),
          deadline.locator('.bill-due-date'),
        ]) {
          await expect(target).toHaveCSS('color', 'rgb(192, 57, 43)')
          await expect(target).toHaveCSS('font-weight', '700')
        }
      } else {
        await expect(amount).not.toHaveClass(/urgent/)
        await expect(deadline).not.toHaveClass(/urgent/)
        await expect(amount.locator('strong')).not.toHaveCSS('color', 'rgb(192, 57, 43)')
        await expect(deadline.locator('.bill-due-date')).not.toHaveCSS('color', 'rgb(192, 57, 43)')
      }
    }
    const settled = latest.filter((s) => s.payment_status === 'settled')
    await page.route('**/api/v1/admin/accounts?*', (route) =>
      route.fulfill({ json: { items: [], total: 0 } }),
    )
    await page.route('**/api/v1/admin/statements?*', (route) =>
      route.fulfill({ json: { items: settled, total: settled.length } }),
    )
    for (const s of settled) {
      await page.route(`**/api/v1/admin/statements/${s.id}`, (route) =>
        route.fulfill({
          json: {
            ...s,
            version: 1,
            amount: '100.00',
            paid: '100.00',
            minimum: null,
            transaction_status: 'unavailable',
            repayments: [],
            plans: [],
          },
        }),
      )
    }
    await page.getByRole('link', { name: '全部账单' }).click()
    await expect(page.locator('.el-table__body .el-tag--success')).toHaveCount(2)
    for (const s of settled) {
      const row = page.locator('.el-table__body tr').filter({
        has: page.getByText(`${s.account.bank_name} · ${s.account.cardholder_name}`, {
          exact: true,
        }),
      })
      await row.getByRole('button', { name: '详情', exact: true }).click()
      const tag = page.locator('.detail-heading .el-tag--success')
      await expect(tag).toHaveText('已标记结清')
      await expect(tag).toHaveCSS('color', 'rgb(103, 194, 58)')
      await expect(tag).toHaveCSS('background-color', 'rgb(240, 249, 235)')
      await page.keyboard.press('Escape')
      await expect(page.locator('.el-drawer')).not.toBeVisible()
    }
  })
}

for (const width of [1440, 390]) {
  test(`overview distinguishes stale bills from settled current bills (${width}px)`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 1100 })
    const cases = [
      { id: 'stale-settled', stale: true, remaining: '0.00', day: 13 },
      { id: 'stale-unpaid', stale: true, remaining: '100.00', day: 13 },
      { id: 'current-settled', stale: false, remaining: '0.00', day: 13 },
      { id: 'unknown-day', stale: false, remaining: '0.00', day: null },
    ]
    let updated = false
    const rows = () =>
      cases.map((c) => ({
        id: c.id,
        account: {
          id: `account-${c.id}`,
          bank_name: '浦发银行',
          bank_code: 'spdb',
          cardholder_name: '测试持卡人',
          mode: 'merged',
          statement_day: c.day,
          cards: [
            { id: `card-${c.id}`, name: c.id, last_four: '9190', active: true },
            { id: `second-${c.id}`, name: '运通白', last_four: '5661', active: true },
          ],
        },
        statement_date: c.stale && !updated ? '2026-09-13' : '2026-10-13',
        expected_statement_date: c.day ? '2026-10-13' : null,
        needs_update: c.stale && !updated,
        currency: 'CNY',
        remaining: c.remaining,
        payment_status: c.remaining === '0.00' ? 'settled' : 'unpaid',
        status: 'confirmed',
        due_date: '2026-11-01',
        days_until_due: 18,
        overdue: false,
      }))
    await page.route('**/api/v1/auth/me', (route) =>
      route.fulfill({ json: { username: 'test-admin', csrf_token: 'test-csrf' } }),
    )
    await page.route('**/api/v1/admin/overview', (route) =>
      route.fulfill({
        json: {
          totals: [{ currency: 'CNY', amount: '100.00' }],
          latest_statements: rows(),
          historical_unresolved: [],
          warnings: [],
          account_count: cases.length,
          pending_count: 0,
          due_soon_count: 0,
          overdue_count: 0,
          as_of: '2026-10-14',
          disclaimer: '测试账单',
        },
      }),
    )
    await page.goto('/')
    await expect(page.locator('.bill-update-notice')).toHaveCount(2)
    for (const [index, c] of cases.entries()) {
      const row = page.locator('.bill-row').nth(index)
      if (c.stale) {
        await expect(row.locator('.bill-update-notice .el-tag--warning')).toHaveText('待更新账单')
        await expect(row.locator('.bill-update-notice small')).toHaveText('本期应出账：2026-10-13')
        await expect(row.locator('.bill-amount > span')).toHaveText('旧账单记录待还')
        const bounds = (await row.boundingBox())!
        const notice = (await row.locator('.bill-update-notice').boundingBox())!
        expect(notice.x + notice.width).toBeLessThanOrEqual(bounds.x + bounds.width)
      } else {
        await expect(row.locator('.bill-update-notice')).toHaveCount(0)
        await expect(row.locator('.bill-amount > span')).toHaveText('记录待还')
      }
      if (c.remaining === '0.00') {
        await expect(row.locator('.bill-deadline .el-tag--success')).toHaveText(
          c.stale ? '旧账单已标记结清' : '已标记结清',
        )
      }
    }
    await page.screenshot({
      path: testInfo.outputPath(`overview-update-${width}.png`),
      fullPage: true,
      animations: 'disabled',
    })
    updated = true
    await page.getByRole('button', { name: '刷新', exact: true }).click()
    await expect(page.locator('.bill-update-notice')).toHaveCount(0)
    await expect(
      page.locator('.bill-row').first().locator('.bill-deadline .el-tag--success'),
    ).toHaveText('已标记结清')
  })
}
