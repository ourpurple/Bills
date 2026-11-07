import { test, expect } from '@playwright/test'

for (const { view, width } of ['statements', 'overview'].flatMap((view) =>
  [1440, 390].map((width) => ({ view, width })),
)) {
  test(`${view} card identity (${width}px)`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    const accounts = [
      {
        id: 'single',
        bank_code: 'gfb',
        mode: 'independent',
        bank_id: 'bank-1',
        cardholder_id: 'holder-1',
        name: '账户名称不是卡片名称',
        bank_name: '广发银行',
        cardholder_name: '示例持卡人',
        cards: [{ id: 'card-1', name: '广发臻享白', last_four: '0759', active: true }],
      },
      {
        id: 'merged',
        bank_code: 'cmb',
        mode: 'merged',
        bank_id: 'bank-2',
        cardholder_id: 'holder-2',
        name: '合并还款账户',
        bank_name: '招商银行',
        cardholder_name: '另一位持卡人',
        cards: [
          {
            id: 'card-2',
            name: '这是一张名称非常长的银行卡用于验证窄屏完整展示',
            last_four: '1234',
            active: true,
          },
          { id: 'card-3', name: '家庭备用卡', last_four: '5678', active: true },
          { id: 'card-4', name: '已停用的历史卡片', last_four: '9012', active: false },
        ],
      },
      {
        id: 'empty',
        bank_code: 'ccb',
        mode: 'independent',
        bank_id: 'bank-3',
        cardholder_id: 'holder-3',
        name: '待配置账户',
        bank_name: '建设银行',
        cardholder_name: '第三位持卡人',
        cards: [],
      },
    ]
    const statements = accounts.map((account) => ({
      id: `statement-${account.id}`,
      account,
      statement_date: '2026-10-06',
      currency: 'CNY',
      amount: '100.00',
      remaining: '100.00',
      due_date: '2026-10-26',
      days_until_due: 20,
      status: 'confirmed',
      payment_status: 'unpaid',
      overdue: false,
    }))
    await page.route('**/api/v1/auth/me', (route) =>
      route.fulfill({ json: { username: 'test-admin', csrf_token: 'test-csrf' } }),
    )
    await page.route('**/api/v1/admin/accounts?*', (route) =>
      route.fulfill({ json: { items: accounts, total: accounts.length } }),
    )
    await page.route('**/api/v1/admin/statements?*', (route) =>
      route.fulfill({ json: { items: statements, total: statements.length } }),
    )
    await page.route('**/api/v1/admin/overview', (route) =>
      route.fulfill({
        json: {
          totals: [{ currency: 'CNY', amount: '300.00' }],
          latest_statements: statements,
          historical_unresolved: [],
          warnings: [],
          account_count: accounts.length,
          pending_count: 0,
          due_soon_count: 0,
          overdue_count: 0,
          disclaimer: '测试账单',
        },
      }),
    )
    await page.goto(view === 'statements' ? '/statements' : '/')
    await expect(page.locator('.statement-identity')).toHaveCount(accounts.length)
    if (view === 'statements') {
      await expect(
        page.getByRole('columnheader', { name: '卡片 / 尾号 / 银行 / 持卡人' }),
      ).toBeVisible()
    }
    for (const [index, account] of accounts.entries()) {
      const identity = page.locator('.statement-identity').nth(index)
      await expect(identity.locator('.statement-account')).toHaveText(
        `${account.bank_name} · ${account.cardholder_name}`,
      )
      await expect(identity).not.toContainText(account.name)
      if (view === 'overview') {
        const row = page.locator('.bill-row').nth(index)
        await expect(row.locator('.bill-identity small')).toHaveText(
          `${account.mode === 'merged' ? '多卡合并还款' : '独立还款'} · 账单日：2026-10-06`,
        )
        const rowBounds = (await row.boundingBox())!
        const identityBounds = (await identity.boundingBox())!
        expect(identityBounds.x + identityBounds.width).toBeLessThanOrEqual(
          rowBounds.x + rowBounds.width,
        )
      }
      await expect(identity.locator('.statement-card')).toHaveCount(account.cards.length)
      for (const [cardIndex, card] of account.cards.entries()) {
        const cardRow = identity.locator('.statement-card').nth(cardIndex)
        await expect(cardRow.locator('.statement-card-name')).toHaveText(card.name)
        await expect(cardRow.locator('.statement-card-tail')).toHaveText(`· ${card.last_four}`)
        await expect(cardRow).toHaveCSS('font-size', '14px')
        await expect(cardRow).toHaveCSS('font-weight', '600')
        await expect(cardRow).toHaveCSS('color', 'rgb(37, 57, 50)')
        const accountInfo = identity.locator('.statement-account')
        await expect(accountInfo).toHaveCSS('font-size', '12px')
        await expect(accountInfo).toHaveCSS('font-weight', '400')
        await expect(accountInfo).toHaveCSS('color', 'rgb(123, 133, 125)')
        const cardBounds = (await cardRow.boundingBox())!
        const accountBounds = (await accountInfo.boundingBox())!
        expect(cardBounds.y + cardBounds.height).toBeLessThanOrEqual(accountBounds.y)
        const identityBounds = (await identity.boundingBox())!
        for (const part of ['.statement-card-name', '.statement-card-tail']) {
          const bounds = (await cardRow.locator(part).boundingBox())!
          expect(bounds.x + bounds.width).toBeLessThanOrEqual(
            identityBounds.x + identityBounds.width + 1,
          )
        }
      }
    }
    await expect(page.locator('.statement-identity').last()).toContainText('暂未添加卡片')
    await page.screenshot({
      path: testInfo.outputPath(`${view}-identity-${width}.png`),
      fullPage: true,
      animations: 'disabled',
    })
  })
}

test('statements status tag highlights unpaid bills in red (danger)', async ({ page }) => {
  const account = {
    id: 'acc-1',
    bank_code: 'gfb',
    mode: 'independent',
    bank_id: 'bank-1',
    cardholder_id: 'holder-1',
    name: '测试账户',
    bank_name: '广发银行',
    cardholder_name: '示例持卡人',
    cards: [{ id: 'card-1', name: '广发臻享白', last_four: '0759', active: true }],
  }
  const statements = [
    {
      id: 'statement-unpaid',
      account,
      statement_date: '2026-10-06',
      currency: 'CNY',
      amount: '100.00',
      remaining: '100.00',
      due_date: '2026-11-02',
      days_until_due: 19,
      status: 'confirmed',
      payment_status: 'unpaid',
      overdue: false,
    },
    {
      id: 'statement-settled',
      account,
      statement_date: '2026-10-06',
      currency: 'CNY',
      amount: '100.00',
      remaining: '0.00',
      due_date: '2026-11-02',
      days_until_due: 19,
      status: 'confirmed',
      payment_status: 'settled',
      overdue: false,
    },
  ]
  await page.route('**/api/v1/auth/me', (route) =>
    route.fulfill({ json: { username: 'test-admin', csrf_token: 'test-csrf' } }),
  )
  await page.route('**/api/v1/admin/accounts?*', (route) =>
    route.fulfill({ json: { items: [account], total: 1 } }),
  )
  await page.route('**/api/v1/admin/statements?*', (route) =>
    route.fulfill({ json: { items: statements, total: statements.length } }),
  )
  await page.route('**/api/v1/admin/statements/statement-unpaid', (route) =>
    route.fulfill({
      json: {
        ...statements[0],
        version: 1,
        source_kind: 'manual',
        paid: '0.00',
        excess: '0.00',
        repayments: [],
      },
    }),
  )

  await page.goto('/statements')

  const unpaidTag = page.locator('.el-table .el-tag').filter({ hasText: '待还款' })
  await expect(unpaidTag).toBeVisible()
  await expect(unpaidTag).toHaveClass(/el-tag--danger/)

  const settledTag = page.locator('.el-table .el-tag').filter({ hasText: '已结清' })
  await expect(settledTag).toBeVisible()
  await expect(settledTag).toHaveClass(/el-tag--success/)

  // Verify detail drawer tag
  await page.locator('.el-table tbody tr').first().getByRole('button', { name: '详情' }).click()
  await expect(page.locator('.el-drawer')).toBeVisible()
  const drawerTag = page.locator('.detail-heading .el-tag')
  await expect(drawerTag).toHaveText('待还款')
  await expect(drawerTag).toHaveClass(/el-tag--danger/)
})

test('statement archive defaults to transaction details on every open', async ({ page }) => {
  const account = {
    id: 'account-1',
    bank_code: 'cmb',
    mode: 'independent',
    bank_id: 'bank-1',
    cardholder_id: 'holder-1',
    bank_name: '招商银行',
    cardholder_name: '示例持卡人',
    cards: [{ id: 'card-1', name: '测试卡', last_four: '1234', active: true }],
  }
  const statement = {
    id: 'statement-1',
    account,
    statement_date: '2026-10-06',
    due_date: '2026-10-26',
    currency: 'CNY',
    amount: '100.00',
    remaining: '100.00',
    paid: '0.00',
    excess: '0.00',
    status: 'confirmed',
    payment_status: 'unpaid',
    version: 1,
    source_kind: 'model',
    transaction_status: 'complete',
    transactions: [
      {
        transaction_date: '2026-10-01',
        description: '默认显示的测试交易',
        direction: 'debit',
        amount: '100.00',
        currency: 'CNY',
      },
    ],
    repayments: [],
    plans: [],
    revisions: [],
    detail_history: [],
  }
  await page.route('**/api/v1/auth/me', (route) =>
    route.fulfill({ json: { username: 'test-admin', csrf_token: 'test-csrf' } }),
  )
  await page.route('**/api/v1/admin/accounts?*', (route) =>
    route.fulfill({ json: { items: [account], total: 1 } }),
  )
  await page.route('**/api/v1/admin/statements?*', (route) =>
    route.fulfill({ json: { items: [statement], total: 1 } }),
  )
  await page.route('**/api/v1/admin/statements/statement-1', (route) =>
    route.fulfill({ json: statement }),
  )

  await page.goto('/statements')
  const drawer = page.getByRole('dialog', { name: '账单档案' })
  const transactionsTab = drawer.getByRole('tab', { name: '交易详单', exact: true })
  const openDetails = page.getByRole('button', { name: '详情', exact: true })
  await openDetails.click()
  await expect(transactionsTab).toHaveAttribute('aria-selected', 'true')
  await expect(drawer.getByRole('cell', { name: '默认显示的测试交易' })).toBeVisible()

  await drawer.getByRole('tab', { name: '还款记录', exact: true }).click()
  await expect(drawer.getByText('尚无还款记录')).toBeVisible()
  await drawer.getByRole('button', { name: '关闭此对话框' }).click()
  await expect(drawer).not.toBeVisible()
  await openDetails.click()
  await expect(transactionsTab).toHaveAttribute('aria-selected', 'true')
  await expect(drawer.getByRole('cell', { name: '默认显示的测试交易' })).toBeVisible()

  await page.goto('/statements?detail=statement-1')
  await expect(transactionsTab).toHaveAttribute('aria-selected', 'true')
  await expect(drawer.getByRole('cell', { name: '默认显示的测试交易' })).toBeVisible()
})

for (const width of [1920, 1440, 1024, 768, 390, 320]) {
  test(`statement spacing preserves full page width (${width}px)`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 })
    const account = {
      id: 'spacing-account',
      bank_id: 'cmb',
      bank_code: 'cmb',
      bank_name: '招商银行',
      cardholder_id: 'holder',
      cardholder_name: '示例持卡人',
      mode: 'merged',
      cards: [
        { id: 'card-1', name: '经典白金卡', last_four: '9999', active: true },
        {
          id: 'card-2',
          name: '用于验证长名称与完整尾号展示的家庭附属信用卡',
          last_four: '8727',
          active: true,
        },
      ],
    }
    const statements = [
      {
        currency: 'CNY',
        amount: '5.14',
        remaining: '5.14',
        payment_status: 'unpaid',
        overdue: false,
      },
      {
        currency: 'CNY',
        amount: '3393.15',
        remaining: '3393.15',
        payment_status: 'partial',
        overdue: false,
      },
      {
        currency: 'USD',
        amount: '12345678.90',
        remaining: '12345678.90',
        payment_status: 'unpaid',
        overdue: true,
      },
      {
        currency: 'USD',
        amount: '9999999999.99',
        remaining: '0.00',
        payment_status: 'settled',
        overdue: false,
      },
    ].map((statement, index) => ({
      ...statement,
      id: `spacing-${index}`,
      account,
      status: 'confirmed',
      statement_date: '2026-10-28',
      due_date: '2026-11-21',
    }))
    await page.route('**/api/v1/auth/me', (route) =>
      route.fulfill({ json: { username: 'test-admin', csrf_token: 'test-csrf' } }),
    )
    await page.route('**/api/v1/admin/accounts?*', (route) =>
      route.fulfill({ json: { items: [account], total: 1 } }),
    )
    await page.route('**/api/v1/admin/statements?*', (route) =>
      route.fulfill({ json: { items: statements, total: statements.length } }),
    )
    await page.goto('/statements')
    const table = page.locator('.statement-table')
    const rows = table.locator('.el-table__body .el-table__row')
    await expect(rows).toHaveCount(4)
    // The panel, heading and filters must still fill the unchanged page content box.
    const contentWidth = await page.locator('main').evaluate((element) => {
      const style = getComputedStyle(element)
      return element.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
    })
    const panel = page.locator('main > .panel')
    expect((await panel.boundingBox())!.width).toBeCloseTo(contentWidth, 0)
    expect((await page.locator('.page-heading').boundingBox())!.width).toBeCloseTo(contentWidth, 0)
    const tableWidth = (await table.boundingBox())!.width
    expect((await page.locator('.statement-filters').boundingBox())!.width).toBeCloseTo(
      tableWidth,
      0,
    )
    const headers = table.locator('thead th.el-table__cell')
    const minWidths = [200, 120, 130, 130, 160, 120]
    const available = Math.max(tableWidth - 80, 860)
    for (const [index, minimum] of minWidths.entries()) {
      await expect
        .poll(async () =>
          Math.abs((await headers.nth(index).boundingBox())!.width - (available * minimum) / 860),
        )
        .toBeLessThan(3)
    }
    expect((await headers.nth(6).boundingBox())!.width).toBe(80)
    for (const row of await rows.all()) {
      const remaining = row.locator('.statement-remaining .numeric')
      const due = row.locator('.statement-due .cell')
      const amountRight = await remaining.evaluate(
        (element) => element.getBoundingClientRect().right,
      )
      const dateLeft = await due.evaluate((element) => {
        const range = document.createRange()
        range.selectNodeContents(element)
        return range.getBoundingClientRect().left
      })
      expect(dateLeft - amountRight).toBeGreaterThanOrEqual(50)
      await expect(due).toHaveCSS('white-space', 'nowrap')
      expect(await due.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(
        true,
      )
      for (const amount of await row.locator('.numeric').all()) {
        expect(
          await amount.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
        ).toBe(true)
        await expect(amount).toHaveCSS('text-align', 'right')
      }
    }
    await expect(table.locator('.statement-card-tail')).toHaveCount(8)
    await expect(table.locator('.el-table__body .el-tag')).toHaveText([
      '待还款',
      '部分还款',
      '已过到期日',
      '已结清',
    ])
    const scroll = table.locator('.el-table__body-wrapper .el-scrollbar__wrap')
    await scroll.evaluate((element) => {
      element.scrollLeft = element.scrollWidth
    })
    await expect(table.getByRole('button', { name: '详情', exact: true }).first()).toBeInViewport()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await scroll.evaluate((element) => {
      element.scrollLeft = 0
    })
    await page.screenshot({
      path: testInfo.outputPath(`statement-full-width-${width}.png`),
      fullPage: true,
      animations: 'disabled',
    })
  })
}
