import { test, expect } from '@playwright/test'

for (const width of [1440, 390]) {
  test(`statement card identity (${width}px)`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    const accounts = [
      {
        id: 'single',
        name: '账户名称不是卡片名称',
        bank_name: '广发银行',
        cardholder_name: '示例持卡人',
        cards: [{ id: 'card-1', name: '广发臻享白', last_four: '0759', active: true }],
      },
      {
        id: 'merged',
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
    await page.goto('/statements')
    await expect(page.locator('.statement-identity')).toHaveCount(accounts.length)
    await expect(
      page.getByRole('columnheader', { name: '卡片 / 尾号 / 银行 / 持卡人' }),
    ).toBeVisible()
    for (const [index, account] of accounts.entries()) {
      const identity = page.locator('.statement-identity').nth(index)
      await expect(identity.locator('.statement-account')).toHaveText(
        `${account.bank_name} · ${account.cardholder_name}`,
      )
      await expect(identity).not.toContainText(account.name)
      await expect(identity.locator('.statement-card')).toHaveCount(account.cards.length)
      for (const [cardIndex, card] of account.cards.entries()) {
        const cardRow = identity.locator('.statement-card').nth(cardIndex)
        await expect(cardRow.locator('.statement-card-name')).toHaveText(card.name)
        await expect(cardRow.locator('.statement-card-tail')).toHaveText(`· 尾号 ${card.last_four}`)
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
      path: testInfo.outputPath(`statement-identity-${width}.png`),
      fullPage: true,
      animations: 'disabled',
    })
  })
}
