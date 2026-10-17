import { test, expect, type Page, type Locator } from '@playwright/test'

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

async function openCardEditor(page: Page, tile: Locator, cardName = '待删除银行卡') {
  await tile.getByRole('button', { name: '编辑', exact: true }).click()
  const accountEditor = page.getByRole('dialog', { name: '编辑还款账户', exact: true })
  await accountEditor.getByRole('button', { name: `编辑卡片 ${cardName}`, exact: true }).click()
  await expect(page.getByRole('dialog', { name: '编辑卡片', exact: true })).toBeVisible()
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
  await openCardEditor(page, tile)
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

test('compact account rows align on desktop and fit narrow screens', async ({ page }, testInfo) => {
  const fixtures = [
    {
      name: '臻享白',
      bank_name: '广发银行',
      statement_day: 7,
      cards: [{ name: '广发臻享白', last_four: '9759' }],
    },
    {
      name: 'DIY',
      bank_name: '广发银行',
      statement_day: 7,
      cards: [{ name: 'DIY', last_four: '2090' }],
    },
    {
      name: '兴业',
      bank_name: '兴业银行',
      statement_day: 27,
      cards: [{ name: '商旅白', last_four: '5835' }],
    },
    {
      name: '农行',
      bank_name: '农业银行',
      statement_day: 28,
      cards: [{ name: '悠然白', last_four: '8753' }],
    },
    {
      name: '合并还款账户名称很长时也应完整展示而不挤出列表',
      bank_name: '招商银行',
      statement_day: 12,
      cards: [
        { name: '这是一张名称非常长的银行卡用于验证窄屏展示', last_four: '1234' },
        { name: '家庭备用卡', last_four: '5678' },
        { name: '已停用卡', last_four: '9012', active: false },
      ],
    },
    { name: '待配置账户', bank_name: '建设银行', statement_day: null, cards: [] },
    { name: '已停用账户', bank_name: '交通银行', statement_day: 20, cards: [] },
  ].map((account, index) => ({
    ...account,
    id: `layout-account-${index}`,
    bank_id: 'layout-bank',
    bank_code: 'layout-bank',
    cardholder_id: 'layout-holder',
    cardholder_name: '示例持卡人',
    mode: account.cards.length > 1 ? 'merged' : 'independent',
    active: index !== 6,
    effective_active: index !== 6,
    cards: account.cards.map((card, cardIndex) => ({
      active: true,
      ...card,
      id: `layout-card-${index}-${cardIndex}`,
    })),
  }))
  let empty = false
  await page.route('**/api/v1/auth/me', (route) =>
    route.fulfill({ json: { username: 'layout-test', csrf_token: 'layout-csrf' } }),
  )
  await page.route('**/api/v1/admin/banks?*', (route) =>
    route.fulfill({ json: { items: [], total: 0 } }),
  )
  await page.route('**/api/v1/admin/cardholders?*', (route) =>
    route.fulfill({ json: { items: [], total: 0 } }),
  )
  await page.route('**/api/v1/admin/accounts?*', (route) =>
    route.fulfill({ json: { items: empty ? [] : fixtures, total: empty ? 0 : fixtures.length } }),
  )
  await page.route('**/api/v1/admin/accounts/layout-account-*', (route) => {
    const account = fixtures.find((item) =>
      route
        .request()
        .url()
        .endsWith('/' + item.id),
    )!
    Object.assign(account, route.request().postDataJSON())
    return route.fulfill({ json: account })
  })
  await page.setViewportSize({ width: 1600, height: 1000 })
  await page.goto('/accounts')
  const rows = page.locator('.account-card')
  await expect(rows).toHaveCount(fixtures.length)
  await expect(page.locator('.account-list-header')).toBeVisible()
  const expectedOrder = [0, 1, 4, 6, 2, 3, 5].map((index) => fixtures[index].name)
  await expect(rows.locator('h3')).toHaveText(expectedOrder)
  const accountRow = (name: string) =>
    rows.filter({ has: page.getByRole('heading', { name, exact: true }) })
  for (const name of ['臻享白', 'DIY', '兴业', '农行']) {
    const row = accountRow(name)
    const box = (await row.boundingBox())!
    expect(box.height).toBeLessThanOrEqual(90)
    const bounds = await row.locator(':scope > div').evaluateAll((elements) =>
      elements.map((element) => {
        const rect = element.getBoundingClientRect()
        return { x: rect.x, right: rect.right, centerY: rect.y + rect.height / 2 }
      }),
    )
    for (let cell = 1; cell < bounds.length; cell++) {
      expect(bounds[cell].x).toBeGreaterThanOrEqual(bounds[cell - 1].right)
      expect(Math.abs(bounds[cell].centerY - bounds[0].centerY)).toBeLessThan(2)
    }
  }
  await expect(accountRow('待配置账户')).toContainText('出账日未设置')
  await expect(accountRow('待配置账户')).toContainText('暂未添加卡片')
  await expect(accountRow('已停用账户')).toHaveClass(/inactive/)
  await expect(
    accountRow('已停用账户').getByRole('button', { name: '添加卡片', exact: true }),
  ).toBeDisabled()
  await page.screenshot({
    path: testInfo.outputPath('account-list-desktop.png'),
    fullPage: true,
    animations: 'disabled',
  })
  await rows.first().getByRole('button', { name: '编辑', exact: true }).click()
  const accountEditor = page.getByRole('dialog', { name: '编辑还款账户', exact: true })
  await expect(accountEditor.locator('input').first()).toHaveValue('臻享白')
  await accountEditor.getByRole('button', { name: '取消', exact: true }).click()
  await rows.first().getByRole('button', { name: '广发臻享白 · 9759', exact: true }).click()
  const cardDetails = page.getByRole('dialog', { name: '卡片详情', exact: true })
  await expect(cardDetails.getByRole('heading', { name: '广发臻享白', exact: true })).toBeVisible()
  await expect(cardDetails.locator('input, textarea, form')).toHaveCount(0)
  await cardDetails.getByRole('button', { name: '关闭', exact: true }).click()
  for (const width of [1440, 1280, 1100, 820, 700, 520, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 })
    const measurements = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      pageWidth: document.documentElement.scrollWidth,
      rows: Array.from(document.querySelectorAll('.account-card')).map((row) => {
        const rect = row.getBoundingClientRect()
        return {
          x: rect.x,
          right: rect.right,
          y: rect.y,
          bottom: rect.bottom,
          overflow: row.scrollWidth > row.clientWidth,
        }
      }),
    }))
    expect(measurements.pageWidth, `page overflow at ${width}px`).toBeLessThanOrEqual(width)
    for (let index = 0; index < measurements.rows.length; index++) {
      const row = measurements.rows[index]
      expect(row.overflow, `row ${index} overflow at ${width}px`).toBe(false)
      expect(row.right).toBeLessThanOrEqual(measurements.viewport)
      expect(row.x).toBe(measurements.rows[0].x)
      if (index > 0) expect(row.y).toBeGreaterThanOrEqual(measurements.rows[index - 1].bottom - 1)
      await expect(rows.nth(index).getByRole('button', { name: '编辑', exact: true })).toBeVisible()
      await expect(rows.nth(index).getByRole('button', { name: '删除', exact: true })).toBeVisible()
    }
    if (width === 390)
      await page.screenshot({
        path: testInfo.outputPath('account-list-mobile.png'),
        fullPage: true,
      })
  }
  await page.getByRole('button', { name: '刷新', exact: true }).click()
  await expect(rows.locator('h3')).toHaveText(expectedOrder)
  await page.reload()
  await expect(rows.locator('h3')).toHaveText(expectedOrder)
  for (const [name, day] of [
    ['臻享白', 31],
    ['待配置账户', 1],
  ] as const) {
    await accountRow(name).getByRole('button', { name: '编辑', exact: true }).click()
    await accountEditor.getByRole('spinbutton').fill(String(day))
    await accountEditor.getByRole('button', { name: '保存', exact: true }).click()
    await expect(accountEditor).not.toBeVisible()
    await expect(accountRow(name).locator('.account-statement-day strong')).toHaveText(String(day))
  }
  const updatedOrder = [5, 1, 4, 6, 2, 3, 0].map((index) => fixtures[index].name)
  await expect(rows.locator('h3')).toHaveText(updatedOrder)
  await page.reload()
  await expect(rows.locator('h3')).toHaveText(updatedOrder)
  empty = true
  await page.getByRole('button', { name: '刷新', exact: true }).click()
  await expect(rows).toHaveCount(0)
  await expect(page.locator('.account-list-header')).toHaveCount(0)
  await expect(
    page.getByText('先添加持卡人，再选择银行创建还款账户。', { exact: true }),
  ).toBeVisible()
})

test('card credit limit and annual fee details persist, validate and clear', async ({
  page,
}, testInfo) => {
  const { tile, account, card, headers } = await setup(page, '卡片年费信息测试')
  const editor = page.getByRole('dialog', { name: '编辑卡片', exact: true })
  async function openCard() {
    await openCardEditor(page, tile)
  }
  async function selectFeeDate(month: number, day: number) {
    await editor.locator('.annual-fee-date .el-select').first().click()
    await page.getByRole('option', { name: `${month} 月`, exact: true }).click()
    await editor.locator('.annual-fee-date .el-select').last().click()
    await page.getByRole('option', { name: `${day} 日`, exact: true }).click()
  }
  async function storedCard() {
    const result = await (await page.request.get('/api/v1/admin/accounts')).json()
    return result.items.find((a: { id: string }) => a.id === account.id).cards[0]
  }
  await openCard()
  await expect(editor.getByRole('combobox', { name: '年费日期', exact: true })).toBeDisabled()
  await editor.getByLabel('额度（元）', { exact: true }).fill('-1')
  await editor.getByRole('button', { name: '保存', exact: true }).click()
  await expect(
    page.getByText('额度请输入非负金额，最多 12 位整数和 2 位小数', { exact: true }),
  ).toBeVisible()
  await editor.getByLabel('额度（元）', { exact: true }).fill('80000.50')
  await selectFeeDate(2, 29)
  await editor.locator('.annual-fee-date .el-select').last().click()
  await expect(page.getByRole('option', { name: '30 日', exact: true })).toHaveCount(0)
  await editor.getByRole('combobox', { name: '年费日期', exact: true }).press('Escape')
  const benefits = '首年免年费\n每年刷满 6 笔免次年年费'
  await editor.getByLabel('年费优惠信息', { exact: true }).fill(benefits)
  await editor.screenshot({
    path: testInfo.outputPath('card-fee-editor-desktop.png'),
    animations: 'disabled',
  })
  await editor.getByRole('button', { name: '保存', exact: true }).click()
  await expect(editor).not.toBeVisible()
  expect(await storedCard()).toMatchObject({
    credit_limit: '80000.50',
    annual_fee_month: 2,
    annual_fee_day: 29,
    annual_fee_benefits: benefits,
  })
  await page.reload()
  await openCard()
  await expect(editor.getByLabel('额度（元）', { exact: true })).toHaveValue('80000.50')
  await expect(editor.locator('.annual-fee-date .el-select').first()).toContainText('2 月')
  await expect(editor.locator('.annual-fee-date .el-select').last()).toContainText('29 日')
  await expect(editor.getByLabel('年费优惠信息', { exact: true })).toHaveValue(benefits)
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 })
    const bounds = await editor.boundingBox()
    expect(bounds!.x).toBeGreaterThanOrEqual(0)
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width)
    await expect(editor.getByRole('button', { name: '保存', exact: true })).toBeVisible()
  }
  await editor.screenshot({
    path: testInfo.outputPath('card-fee-editor-mobile.png'),
    animations: 'disabled',
  })
  await editor.getByLabel('额度（元）', { exact: true }).fill('')
  // Clearing the month also clears the day.
  await editor.locator('.annual-fee-date .el-select').first().hover()
  await editor.locator('.annual-fee-date .el-select').first().locator('.el-select__clear').click()
  await editor.getByLabel('年费优惠信息', { exact: true }).fill('')
  await editor.getByRole('button', { name: '保存', exact: true }).click()
  await expect(editor).not.toBeVisible()
  expect(await storedCard()).toMatchObject({
    credit_limit: null,
    annual_fee_month: null,
    annual_fee_day: null,
    annual_fee_benefits: null,
  })
  // The creation dialog exposes and saves the same optional fields.
  await page.request.patch(`/api/v1/admin/cards/${card.id}`, {
    headers,
    data: { name: card.name, active: false },
  })
  await tile.getByRole('button', { name: '添加卡片', exact: true }).click()
  const creator = page.getByRole('dialog', { name: '新增卡片', exact: true })
  await creator.getByRole('textbox').first().fill('新卡')
  await creator.getByPlaceholder('仅保存尾号，不收集完整卡号').fill('5678')
  await creator.getByLabel('额度（元）', { exact: true }).fill('0')
  await creator.getByLabel('年费优惠信息', { exact: true }).fill('终身免年费')
  await creator.getByRole('button', { name: '保存', exact: true }).click()
  await expect(creator).not.toBeVisible()
  await expect(tile.getByRole('button', { name: '新卡 · 5678', exact: true })).toBeVisible()
  await page.reload()
  await openCardEditor(page, tile, '新卡')
  await expect(editor.getByLabel('额度（元）', { exact: true })).toHaveValue('0.00')
  await expect(editor.getByLabel('年费优惠信息', { exact: true })).toHaveValue('终身免年费')
})

test('card chips open read-only details with empty, disabled, zero-limit and mobile states', async ({
  page,
}, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const { tile, account, card, headers } = await setup(page, '只读卡片详情测试')
  const details = page.getByRole('dialog', { name: '卡片详情', exact: true })
  const detailPanel = details.locator('.card-details-dialog')
  const field = (name: string) =>
    details
      .locator('dl > div')
      .filter({ has: page.locator('dt').filter({ hasText: new RegExp(`^${name}$`) }) })
      .locator('dd')
  async function expectTwoColumnLayout() {
    const cells = await details.locator('.card-detail-list > div').evaluateAll((elements) =>
      elements.map((element) => {
        const rect = element.getBoundingClientRect()
        return { x: rect.x, y: rect.y, right: rect.right, width: rect.width }
      }),
    )
    expect(cells).toHaveLength(7)
    for (let index = 0; index < 6; index += 2) {
      expect(Math.abs(cells[index].y - cells[index + 1].y)).toBeLessThan(1)
      expect(cells[index + 1].x).toBeGreaterThan(cells[index].right)
      expect(Math.abs(cells[index].width - cells[index + 1].width)).toBeLessThan(1)
    }
    expect(cells[6].x).toBe(cells[0].x)
    expect(Math.abs(cells[6].right - cells[1].right)).toBeLessThan(1)
    expect(cells[6].y).toBeGreaterThan(cells[5].y)
  }
  let mutations = 0
  page.on('request', (request) => {
    if (['POST', 'PATCH', 'PUT', 'DELETE'].includes(request.method())) mutations++
  })
  const activeChip = tile.getByRole('button', { name: '待删除银行卡 · 4321', exact: true })
  await expect(activeChip).not.toHaveClass(/el-button--danger/)
  await activeChip.focus()
  await page.keyboard.press('Enter')
  await expect(details).toBeVisible()
  await expect(details.locator('.card-detail-identity')).toHaveText(/^待删除银行卡\s*· 4321$/)
  await expect(details.getByLabel('卡片尾号 4321', { exact: true })).toBeVisible()
  await expect(details.locator('dt').filter({ hasText: /^(卡片尾号|所属账户)$/ })).toHaveCount(0)
  await expect(field('银行')).toHaveText('招商银行')
  await expect(field('持卡人')).toHaveText('只读卡片详情测试')
  await expect(field('还款模式')).toHaveText('独立还款')
  await expect(field('额度')).toHaveText('未设置')
  await expect(field('年费收取日期')).toHaveText('未设置')
  await expect(field('年费优惠信息')).toHaveText('未填写')
  await expect(field('预计出账日')).toHaveText('未设置')
  await expectTwoColumnLayout()
  await expect(
    details.locator('input, textarea, form, [role="switch"], [contenteditable="true"]'),
  ).toHaveCount(0)
  await expect(details.getByRole('button', { name: /编辑|删除|保存|添加/ })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(details).not.toBeVisible()
  expect(mutations).toBe(0)

  const benefits =
    '首年免年费\n每年刷满 6 笔免次年年费\n<b>仅展示文字，不作为 HTML</b>\n' +
    '优惠条件以银行公布为准。'.repeat(15)
  const updated = await page.request.patch(`/api/v1/admin/cards/${card.id}`, {
    headers,
    data: {
      name: card.name,
      active: false,
      credit_limit: '80000.50',
      annual_fee_month: 2,
      annual_fee_day: 29,
      annual_fee_benefits: benefits,
    },
  })
  expect(updated.status()).toBe(200)
  expect(
    (
      await page.request.patch(`/api/v1/admin/accounts/${account.id}`, {
        headers,
        data: { name: account.name, active: false, statement_day: 12 },
      })
    ).status(),
  ).toBe(200)
  await page.reload()
  const inactiveChip = tile.getByRole('button', {
    name: '待删除银行卡 · 4321（停用）',
    exact: true,
  })
  await expect(inactiveChip).toHaveClass(/el-button--danger/)
  await expect(inactiveChip).toBeEnabled()
  await tile.screenshot({
    path: testInfo.outputPath('inactive-card-red.png'),
    animations: 'disabled',
  })
  await inactiveChip.click()
  await expect(details.getByText('已停用', { exact: true })).toBeVisible()
  await expect(details.locator('.card-detail-heading .el-tag')).toHaveClass(/el-tag--danger/)
  await expect(field('额度')).toHaveText('¥ 80,000.50')
  await expect(field('年费收取日期')).toHaveText('每年 2 月 29 日')
  await expect(field('年费优惠信息')).toHaveText(benefits)
  await expect(field('年费优惠信息').locator('b')).toHaveCount(0)
  await expect(field('预计出账日')).toHaveText('每月 12 日')
  expect(await field('年费优惠信息').evaluate((el) => getComputedStyle(el).whiteSpace)).toBe(
    'pre-wrap',
  )
  await detailPanel.screenshot({
    path: testInfo.outputPath('card-details-desktop.png'),
    animations: 'disabled',
  })
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 })
    const bounds = (await detailPanel.boundingBox())!
    expect(bounds.x).toBeGreaterThanOrEqual(0)
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width)
    expect(bounds.y).toBeGreaterThanOrEqual(0)
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(844)
    expect(await detailPanel.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true)
    await expectTwoColumnLayout()
    await expect(details.getByRole('button', { name: /编辑|删除|保存|添加/ })).toHaveCount(0)
    await details.getByRole('button', { name: '关闭', exact: true }).scrollIntoViewIfNeeded()
    const headerBounds = (await details.locator('.el-dialog__header').boundingBox())!
    const closeBounds = (await details
      .getByRole('button', { name: '关闭', exact: true })
      .boundingBox())!
    expect(headerBounds.y).toBeGreaterThanOrEqual(0)
    expect(closeBounds.y + closeBounds.height).toBeLessThanOrEqual(844)
    expect(await detailPanel.evaluate((el) => getComputedStyle(el).display)).toBe('flex')
  }
  await detailPanel.screenshot({
    path: testInfo.outputPath('card-details-mobile.png'),
    animations: 'disabled',
  })
  await details.getByRole('button', { name: '关闭', exact: true }).click()
  await expect(details).not.toBeVisible()
  expect(mutations).toBe(0)

  expect(
    (
      await page.request.patch(`/api/v1/admin/accounts/${account.id}`, {
        headers,
        data: { name: account.name, active: true },
      })
    ).status(),
  ).toBe(200)
  const created = await page.request.post('/api/v1/admin/cards', {
    headers,
    data: { name: '零额度备用卡', account_id: account.id, last_four: '6789', credit_limit: '0' },
  })
  expect(created.status()).toBe(201)
  await page.reload()
  await tile.getByRole('button', { name: '零额度备用卡 · 6789', exact: true }).click()
  await expect(details.getByRole('heading', { name: '零额度备用卡', exact: true })).toBeVisible()
  await expect(field('额度')).toHaveText('¥ 0.00')
  await expect(field('年费优惠信息')).toHaveText('未填写')
  await details.getByRole('button', { name: '关闭', exact: true }).click()
  expect(mutations).toBe(0)

  // Destructive actions remain in explicit account management, never in the detail view.
  await tile.getByRole('button', { name: '编辑', exact: true }).click()
  const manager = page.getByRole('dialog', { name: '编辑还款账户', exact: true })
  await manager.getByRole('button', { name: '删除卡片 零额度备用卡', exact: true }).click()
  const confirmation = page.locator('.el-message-box')
  await confirmation.getByRole('button', { name: '取消', exact: true }).click()
  await expect(manager.getByText('零额度备用卡 · 6789', { exact: true })).toBeVisible()
  expect(mutations).toBe(0)
  await manager.getByRole('button', { name: '删除卡片 零额度备用卡', exact: true }).click()
  await confirmation.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect(manager.getByText('零额度备用卡 · 6789', { exact: true })).toHaveCount(0)
  expect(mutations).toBe(1)
  expect(errors).toEqual([])
})
