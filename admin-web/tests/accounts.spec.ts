import { test, expect, type Page } from '@playwright/test'

async function setup(page: Page, name: string, history = false) {
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
  const banks = await (await page.request.get('/api/v1/admin/banks')).json()
  const bank = banks.items.find((b: { code: string }) => b.code === 'cmb')
  const holder = await create('cardholders', { name })
  const account = await create('accounts', {
    name,
    bank_id: bank.id,
    cardholder_id: holder.id,
    mode: 'independent',
  })
  const card = await create('cards', {
    name: '待删除银行卡',
    last_four: '4321',
    account_id: account.id,
  })
  const statement = history
    ? await create('statements', {
        account_id: account.id,
        statement_date: '2026-09-01',
        currency: 'CNY',
        amount: '1000.00',
        minimum: '100.00',
        due_date: '2026-09-21',
      })
    : null
  await page.getByRole('link', { name: '账户与卡片' }).click()
  const tile = page
    .locator('.account-card')
    .filter({ has: page.getByRole('heading', { name, exact: true }) })
  await expect(tile).toBeVisible()
  return { tile, account, card, statement, headers }
}

test('delete account: cancel, confirmation, children removed and persistence on refresh', async ({
  page,
}, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const { tile, account, card, headers } = await setup(page, '删除账户界面测试')
  // Deletion remains available for disabled tiles too.
  const disabled = await page.request.patch(`/api/v1/admin/accounts/${account.id}`, {
    headers,
    data: { name: account.name, active: false },
  })
  expect(disabled.status()).toBe(200)
  await page.getByRole('button', { name: '刷新', exact: true }).click()
  await expect(tile).toHaveClass(/inactive/)
  let deleteRequests = 0
  page.on('request', (request) => {
    if (request.method() === 'DELETE') deleteRequests++
  })
  await tile.getByRole('button', { name: '删除', exact: true }).click()
  const confirmation = page.locator('.el-message-box')
  await expect(confirmation).toContainText('及其下的所有卡片')
  await confirmation.getByRole('button', { name: '取消', exact: true }).click()
  await expect(confirmation).not.toBeVisible()
  await expect(tile).toBeVisible()
  expect(deleteRequests).toBe(0)
  await page.setViewportSize({ width: 390, height: 844 })
  await tile.getByRole('button', { name: '删除', exact: true }).click()
  await expect(confirmation).toBeVisible()
  const bounds = await confirmation.boundingBox()
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390)
  await page.screenshot({
    path: testInfo.outputPath('account-delete-confirm-mobile.png'),
    fullPage: true,
    animations: 'disabled',
  })
  await confirmation.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(page.getByText('账户已删除', { exact: true })).toBeVisible()
  await expect(tile).toHaveCount(0)
  expect(deleteRequests).toBe(1)
  const staleCard = await page.request.patch(`/api/v1/admin/cards/${card.id}`, {
    headers,
    data: { name: card.name, active: false },
  })
  expect(staleCard.status()).toBe(404)
  await page.reload()
  await expect(page.getByRole('heading', { name: '卡片有归属，账单不重复。' })).toBeVisible()
  await expect(tile).toHaveCount(0)
  expect(errors).toEqual([])
})

test('delete bank card preserves history and account with history cannot be deleted', async ({
  page,
}, testInfo) => {
  const { tile, account, card, statement } = await setup(page, '删除卡片历史保护测试', true)
  await tile.getByRole('button', { name: '待删除银行卡 · 4321', exact: true }).click()
  const editor = page.getByRole('dialog', { name: '编辑卡片', exact: true })
  await editor.getByRole('button', { name: '删除卡片', exact: true }).click()
  const confirmation = page.locator('.el-message-box')
  await expect(confirmation).toContainText('尾号 4321')
  await expect(confirmation).toContainText('所属账户和历史账单会保留')
  await confirmation.getByRole('button', { name: '取消', exact: true }).click()
  await expect(editor).toBeVisible()
  await editor.getByRole('button', { name: '删除卡片', exact: true }).click()
  await confirmation.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(editor).not.toBeVisible()
  await expect(page.getByText('卡片已删除', { exact: true })).toBeVisible()
  await expect(tile.getByRole('button', { name: '待删除银行卡 · 4321', exact: true })).toHaveCount(
    0,
  )
  await expect(tile.getByText('暂未添加卡片', { exact: true })).toBeVisible()
  const history = await (await page.request.get(`/api/v1/admin/statements/${statement.id}`)).json()
  expect(history.account.id).toBe(account.id)
  expect(history.amount).toBe('1000.00')
  await tile.getByRole('button', { name: '删除', exact: true }).click()
  await confirmation.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(
    page.getByText('该账户有关联账单，不能删除。请停用账户以保留历史记录。', { exact: true }),
  ).toBeVisible()
  await expect(tile).toBeVisible()
  await expect(tile.getByRole('button', { name: '删除', exact: true })).toBeEnabled()
  await page.reload()
  await expect(tile).toBeVisible()
  const accounts = await (await page.request.get('/api/v1/admin/accounts')).json()
  expect(accounts.items.find((a: { id: string }) => a.id === account.id).cards).toEqual([])
  await tile.getByRole('button', { name: '添加卡片', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '新增卡片', exact: true })
  await dialog
    .locator('.el-form-item')
    .filter({ hasText: '名称' })
    .locator('input')
    .fill('重新添加卡片')
  await dialog
    .locator('.el-form-item')
    .filter({ hasText: '卡片尾号' })
    .locator('input')
    .fill(card.last_four)
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  await expect(tile.getByRole('button', { name: '重新添加卡片 · 4321', exact: true })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('account-delete-desktop.png'), fullPage: true })
})
