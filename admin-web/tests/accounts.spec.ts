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
  await creator.getByPlaceholder('填写 4 位尾号，或填写完整卡号自动提取').fill('5678')
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
    const cells = await details
      .locator('.card-detail-list > div:not(.card-detail-number):not(.private-controls)')
      .evaluateAll((elements) =>
        elements.map((element) => {
          const rect = element.getBoundingClientRect()
          return { x: rect.x, y: rect.y, right: rect.right, width: rect.width }
        }),
      )
    expect(cells).toHaveLength(11)
    for (let index = 0; index < 10; index += 2) {
      expect(Math.abs(cells[index].y - cells[index + 1].y)).toBeLessThan(1)
      expect(cells[index + 1].x).toBeGreaterThan(cells[index].right)
      expect(Math.abs(cells[index].width - cells[index + 1].width)).toBeLessThan(1)
    }
    expect(cells[10].x).toBe(cells[0].x)
    expect(Math.abs(cells[10].right - cells[1].right)).toBeLessThan(1)
    expect(cells[10].y).toBeGreaterThan(cells[9].y)
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
  await expect(details.locator('.card-detail-identity > h2')).toHaveText('待删除银行卡')
  await expect(details.locator('.card-tracking-status')).toHaveText('追踪账单：是')
  await expect(details.getByLabel('卡片尾号 4321', { exact: true })).toBeVisible()
  await expect(details.locator('dt').filter({ hasText: /^(卡片尾号|所属账户)$/ })).toHaveCount(0)
  const privateBlock = details.getByRole('region', { name: '卡片安全信息' })
  await expect(privateBlock.locator('dt')).toHaveText(['卡号', '有效期', 'CVV'])
  await expect(privateBlock.getByText('未填写', { exact: true })).toHaveCount(3)
  await expect(privateBlock.locator('.private-controls')).toHaveCount(0)
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
  await expect(details.getByRole('button', { name: '编辑', exact: true })).toBeInViewport()
  await expect(details.getByRole('button', { name: /删除|保存|添加/ })).toHaveCount(0)
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
  await expect(details.locator('.card-validity-status')).toHaveClass(/el-tag--danger/)
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
    await expect(details.getByRole('button', { name: '编辑', exact: true })).toBeInViewport()
    await expect(details.getByRole('button', { name: /删除|保存|添加/ })).toHaveCount(0)
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

test('card details keep complete information compact without clipping on desktop and mobile', async ({
  page,
}, testInfo) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const { tile, account, card, headers } = await setup(page, '紧凑布局测试')
  expect(
    (
      await page.request.patch(`/api/v1/admin/cards/${card.id}`, {
        headers,
        data: {
          name: 'VISA',
          active: true,
          card_number: '4111111111114321',
          cvv: '012',
          expiry: '05/29',
          card_type: 'credit',
          card_network: 'visa',
          credit_limit: '60000.00',
          annual_fee_month: 1,
          annual_fee_day: 1,
          annual_fee_benefits: '终身免年费',
        },
      })
    ).status(),
  ).toBe(200)
  expect(
    (
      await page.request.patch(`/api/v1/admin/accounts/${account.id}`, {
        headers,
        data: { name: account.name, active: true, statement_day: 13 },
      })
    ).status(),
  ).toBe(200)
  await page.reload()
  await tile.getByRole('button', { name: 'VISA · 4321', exact: true }).click()
  const details = page.getByRole('dialog', { name: '卡片详情', exact: true })
  const panel = details.locator('.card-details-dialog')
  const body = details.locator('.el-dialog__body')
  for (const [width, height] of [
    [1440, 900],
    [390, 844],
    [320, 640],
  ]) {
    await page.setViewportSize({ width, height })
    await expect(details).toBeVisible()
    await expect
      .poll(async () => {
        const bounds = (await panel.boundingBox())!
        return (
          bounds.x >= 0 &&
          bounds.x + bounds.width <= width &&
          bounds.y >= 0 &&
          bounds.y + bounds.height <= height
        )
      })
      .toBe(true)
    expect(await body.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true)
    if (height >= 844) {
      expect(await body.evaluate((el) => el.scrollHeight <= el.clientHeight + 1)).toBe(true)
      await expect(details.getByText('终身免年费', { exact: true })).toBeInViewport()
    }
    if (width === 1440) expect((await panel.boundingBox())!.height).toBeLessThan(560)
    await expect(details.getByRole('button', { name: '关闭', exact: true })).toBeInViewport()
    await panel.screenshot({
      path: testInfo.outputPath(`card-details-compact-${width}.png`),
      animations: 'disabled',
    })
  }
  await details.getByRole('button', { name: '查看卡号 / CVV', exact: true }).click()
  await expect(details.getByText('4111 1111 1111 4321', { exact: true })).toBeVisible()
  expect(await body.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true)
  await details.getByRole('button', { name: '隐藏卡号 / CVV', exact: true }).click()
  await expect(details.getByText('4111 1111 1111 4321', { exact: true })).toHaveCount(0)
})

test('card numbers are larger, colored and grouped every four digits without changing stored values', async ({
  page,
}, testInfo) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const { tile, card, headers } = await setup(page, '卡号分组显示测试')
  const details = page.getByRole('dialog', { name: '卡片详情', exact: true })
  const numberValue = details.locator('.card-number-value')
  const variants = [
    ['123456784321', '1234 5678 4321'],
    ['123456789014321', '1234 5678 9014 321'],
    ['4111111111114321', '4111 1111 1111 4321'],
    ['6222021234567894321', '6222 0212 3456 7894 321'],
  ]
  for (const [raw, formatted] of variants) {
    expect(
      (
        await page.request.patch(`/api/v1/admin/cards/${card.id}`, {
          headers,
          data: { name: card.name, active: true, card_number: raw, cvv: '012' },
        })
      ).status(),
    ).toBe(200)
    await page.reload()
    await tile.getByRole('button', { name: '待删除银行卡 · 4321', exact: true }).click()
    await expect(numberValue).toHaveText('•••• •••• •••• 4321')
    await details.getByRole('button', { name: '查看卡号 / CVV', exact: true }).click()
    await expect(numberValue).toHaveText(formatted)
    const privateBlock = details.getByRole('region', { name: '卡片安全信息' })
    await expect(privateBlock.locator('dt')).toHaveText(['卡号', '有效期', 'CVV'])
    await expect(
      privateBlock.getByRole('button', { name: '隐藏卡号 / CVV', exact: true }),
    ).toBeVisible()
    expect(await privateBlock.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe(
      'rgb(242, 246, 239)',
    )
    await expect(details.locator('dd').filter({ hasText: /^012$/ })).toBeVisible()
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 844 })
      const styles = await numberValue.evaluate((el) => {
        const style = getComputedStyle(el)
        return {
          size: parseFloat(style.fontSize),
          color: style.color,
          fits: el.scrollWidth <= el.clientWidth + 1,
        }
      })
      expect(styles.size).toBe(width === 1440 ? 22 : 18)
      expect(styles.color).toBe('rgb(36, 87, 69)')
      expect(styles.fits).toBe(true)
      expect(
        await details
          .locator('.el-dialog__body')
          .evaluate((el) => el.scrollWidth <= el.clientWidth),
      ).toBe(true)
      if (raw.length >= 16)
        await details.locator('.card-details-dialog').screenshot({
          path: testInfo.outputPath(`card-number-${raw.length}-${width}.png`),
          animations: 'disabled',
        })
    }
    // Grouping is display-only: the API retains the exact original digits and CVV.
    expect(
      await (await page.request.post(`/api/v1/admin/cards/${card.id}/private`, { headers })).json(),
    ).toEqual({
      card_number: raw,
      cvv: '012',
    })
    await details.getByRole('button', { name: '隐藏卡号 / CVV', exact: true }).click()
    await expect(numberValue).toHaveText('•••• •••• •••• 4321')
    await details.getByRole('button', { name: '关闭', exact: true }).click()
  }
})

test('card identity fields validate, stay private, persist and clear', async ({
  page,
}, testInfo) => {
  const { tile, card, headers } = await setup(page, '卡片身份信息测试')
  const editor = page.getByRole('dialog', { name: '编辑卡片', exact: true })
  const details = page.getByRole('dialog', { name: '卡片详情', exact: true })
  const field = (name: string) =>
    details
      .locator('dl > div')
      .filter({ has: page.locator('dt').filter({ hasText: new RegExp(`^${name}$`) }) })
      .locator('dd')
  const privatePath = `/api/v1/admin/cards/${card.id}/private`
  const privateData = { card_number: '4111111111114321', cvv: '012' }
  let reads = 0
  page.on('request', (request) => {
    if (request.url().endsWith('/private')) reads++
  })
  await openCardEditor(page, tile)
  await editor.getByLabel('卡号', { exact: true }).fill('123')
  await editor.getByRole('button', { name: '保存', exact: true }).click()
  await expect(
    page.getByText('卡号请输入 12–19 位数字，可使用空格或短横线分隔', { exact: true }),
  ).toBeVisible()
  await editor.getByLabel('卡号', { exact: true }).fill('4111 1111 1111 4321')
  await editor.getByLabel('有效期', { exact: true }).fill('13/29')
  await editor.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByText('有效期请按 MM/YY 填写，例如 09/29', { exact: true })).toBeVisible()
  await editor.getByLabel('有效期', { exact: true }).fill('09/29')
  await editor.getByLabel('CVV', { exact: true }).fill('01')
  await editor.getByRole('button', { name: '保存', exact: true }).click()
  await expect(page.getByText('CVV 请输入 3 或 4 位数字', { exact: true })).toBeVisible()
  await editor.getByLabel('CVV', { exact: true }).fill(privateData.cvv)
  await editor.locator('.card-identity-fields .el-select').first().click()
  await page.getByRole('option', { name: '借记卡', exact: true }).click()
  await editor.locator('.card-identity-fields .el-select').last().click()
  for (const name of ['银联', 'VISA', '万事达', 'AE（美国运通）']) {
    await expect(page.getByRole('option', { name, exact: true })).toBeVisible()
  }
  await page.getByRole('option', { name: 'VISA', exact: true }).click()
  await expect(editor.getByLabel('卡号', { exact: true })).toHaveAttribute('type', 'password')
  await expect(editor.getByLabel('CVV', { exact: true })).toHaveAttribute('type', 'password')
  await editor.getByRole('button', { name: '保存', exact: true }).click()
  await expect(editor).not.toBeVisible()
  expect(reads).toBe(0)
  await tile.getByRole('button', { name: '待删除银行卡 · 4321', exact: true }).click()
  await expect(field('卡片类型')).toHaveText('借记卡')
  await expect(field('卡组织')).toHaveText('VISA')
  await expect(field('有效期')).toHaveText('09/29')
  await expect(field('卡号')).toContainText('••••')
  await expect(field('CVV')).toHaveText('•••')
  expect(reads).toBe(0)
  await details.getByRole('button', { name: '查看卡号 / CVV', exact: true }).click()
  await expect(field('卡号')).toHaveText('4111 1111 1111 4321')
  await expect(field('CVV')).toHaveText(privateData.cvv)
  await details.getByRole('button', { name: '隐藏卡号 / CVV', exact: true }).click()
  await expect(field('卡号')).toHaveText('•••• •••• •••• 4321')
  await details.getByRole('button', { name: '查看卡号 / CVV', exact: true }).click()
  await expect(field('CVV')).toHaveText(privateData.cvv)
  await details.getByRole('button', { name: '关闭', exact: true }).click()
  await tile.getByRole('button', { name: '待删除银行卡 · 4321', exact: true }).click()
  await expect(field('CVV')).toHaveText('•••')
  await details.getByRole('button', { name: '关闭', exact: true }).click()
  await page.reload()
  await openCardEditor(page, tile)
  await expect(editor.getByLabel('卡号', { exact: true })).toHaveValue('')
  await expect(editor.getByLabel('CVV', { exact: true })).toHaveValue('')
  await editor.getByLabel('额度（元）', { exact: true }).fill('5000')
  await editor.getByRole('button', { name: '保存', exact: true }).click()
  await expect(editor).not.toBeVisible()
  expect(await (await page.request.post(privatePath, { headers })).json()).toEqual(privateData)
  await openCardEditor(page, tile)
  await editor.getByRole('button', { name: '读取已保存卡号 / CVV', exact: true }).click()
  await expect(editor.getByLabel('卡号', { exact: true })).toHaveValue(privateData.card_number)
  await expect(editor.getByLabel('CVV', { exact: true })).toHaveValue(privateData.cvv)
  await expect(editor.getByLabel('有效期', { exact: true })).toHaveValue('09/29')
  await editor.locator('.el-dialog__body').evaluate((body) => {
    body.scrollTop = 0
  })
  await editor.screenshot({
    path: testInfo.outputPath('card-identity-desktop.png'),
    animations: 'disabled',
  })
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 })
    const bounds = await editor.boundingBox()
    expect(bounds!.x).toBeGreaterThanOrEqual(0)
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width)
    await expect(editor.getByRole('button', { name: '保存', exact: true })).toBeInViewport()
  }
  await editor.locator('.el-dialog__body').evaluate((body) => {
    body.scrollTop = 0
  })
  await editor.screenshot({
    path: testInfo.outputPath('card-identity-mobile.png'),
    animations: 'disabled',
  })
  await editor.getByLabel('卡号', { exact: true }).fill('')
  await editor.getByLabel('CVV', { exact: true }).fill('')
  await editor.getByLabel('有效期', { exact: true }).fill('')
  await editor.getByRole('button', { name: '保存', exact: true }).click()
  await expect(editor).not.toBeVisible()
  expect(await (await page.request.post(privatePath, { headers })).json()).toEqual({
    card_number: null,
    cvv: null,
  })
})

test('new card derives its tail and supports every card network', async ({ page }) => {
  const { tile, account, card, headers } = await setup(page, '新增完整卡信息测试')
  await page.request.patch(`/api/v1/admin/cards/${card.id}`, {
    headers,
    data: { name: card.name, active: false },
  })
  await tile.getByRole('button', { name: '添加卡片', exact: true }).click()
  const creator = page.getByRole('dialog', { name: '新增卡片', exact: true })
  await creator.getByRole('textbox').first().fill('完整卡信息测试卡')
  await creator.getByLabel('卡号', { exact: true }).fill('3782 822463 10005')
  await creator.getByLabel('有效期', { exact: true }).fill('12/30')
  await creator.getByLabel('CVV', { exact: true }).fill('0001')
  await creator.locator('.card-identity-fields .el-select').last().click()
  await page.getByRole('option', { name: 'AE（美国运通）', exact: true }).click()
  await creator.getByRole('button', { name: '保存', exact: true }).click()
  await expect(creator).not.toBeVisible()
  await expect(
    tile.getByRole('button', { name: '完整卡信息测试卡 · 0005', exact: true }),
  ).toBeVisible()
  const accounts = await (await page.request.get('/api/v1/admin/accounts')).json()
  const saved = accounts.items
    .find((a: { id: string }) => a.id === account.id)
    .cards.find((c: { last_four: string }) => c.last_four === '0005')
  expect(saved).toMatchObject({
    card_type: 'credit',
    card_network: 'amex',
    expiry: '12/30',
    has_card_number: true,
    has_cvv: true,
  })
  for (const card_network of ['unionpay', 'visa', 'mastercard', 'amex']) {
    const response = await page.request.patch(`/api/v1/admin/cards/${saved.id}`, {
      headers,
      data: { name: saved.name, active: true, card_network },
    })
    expect(response.status()).toBe(200)
    expect((await response.json()).card_network).toBe(card_network)
  }
})

test('bill tracking persists independently of validity and gates billing', async ({
  page,
}, testInfo) => {
  const { tile, account, card, statement, headers } = await setup(page, '账单追踪开关测试', true)
  expect(
    (
      await page.request.post(`/api/v1/admin/statements/${statement.id}/confirm`, {
        headers,
        data: { expected_version: 1, reason: '追踪测试' },
      })
    ).status(),
  ).toBe(200)
  const editor = page.getByRole('dialog', { name: '编辑卡片', exact: true })
  for (const [active, tracking] of [
    [true, false],
    [false, true],
    [false, false],
    [true, true],
  ]) {
    await openCardEditor(page, tile)
    const activeSwitch = editor.getByRole('switch', { name: '有效状态', exact: true })
    const trackingSwitch = editor.getByRole('switch', { name: '追踪账单', exact: true })
    for (const [control, value] of [
      [activeSwitch, active],
      [trackingSwitch, tracking],
    ] as const) {
      if ((await control.getAttribute('aria-checked')) !== String(value))
        await control.locator('..').click()
      await expect(control).toHaveAttribute('aria-checked', String(value))
    }
    if (active && !tracking) {
      for (const width of [1440, 390, 320]) {
        await page.setViewportSize({ width, height: 844 })
        await trackingSwitch.locator('..').scrollIntoViewIfNeeded()
        await expect(activeSwitch.locator('..')).toBeInViewport()
        await expect(trackingSwitch.locator('..')).toBeInViewport()
        expect(
          await editor
            .locator('.el-dialog__body')
            .evaluate((el) => el.scrollWidth <= el.clientWidth),
        ).toBe(true)
        await editor.locator('.card-editor-dialog').screenshot({
          path: testInfo.outputPath(`card-bill-tracking-${width}.png`),
          animations: 'disabled',
        })
      }
    }
    await editor.getByRole('button', { name: '保存', exact: true }).click()
    await expect(editor).not.toBeVisible()
    await page.reload()
    const saved = (await (await page.request.get('/api/v1/admin/accounts')).json()).items.find(
      (a: { id: string }) => a.id === account.id,
    )
    expect(saved.cards[0].active).toBe(active)
    expect(saved.cards[0].track_bills).toBe(tracking)
    expect(saved.billing_enabled).toBe(active && tracking)
    const overview = await (await page.request.get('/api/v1/admin/overview')).json()
    expect(overview.latest_statements.some((s: { id: string }) => s.id === statement.id)).toBe(
      active && tracking,
    )
    if (!active || !tracking) {
      expect(
        overview.warnings.some((w: { account_id: string }) => w.account_id === account.id),
      ).toBe(false)
      const response = await page.request.post('/api/v1/admin/statements', {
        headers,
        data: { account_id: account.id, statement_date: '2026-11-01' },
      })
      expect(response.status()).toBe(422)
      expect((await response.json()).error.code).toBe('BILLING_NOT_TRACKED')
    }
    expect((await page.request.get(`/api/v1/admin/statements/${statement.id}`)).status()).toBe(200)
  }
  await openCardEditor(page, tile)
  await expect(editor.getByRole('switch', { name: '追踪账单', exact: true })).toHaveAttribute(
    'aria-checked',
    'true',
  )
  await editor.getByRole('switch', { name: '追踪账单', exact: true }).locator('..').click()
  await editor.getByRole('button', { name: '取消', exact: true }).click()
  await openCardEditor(page, tile)
  await expect(editor.getByRole('switch', { name: '追踪账单', exact: true })).toHaveAttribute(
    'aria-checked',
    'true',
  )
  await editor.getByRole('button', { name: '取消', exact: true }).click()
  // New cards default to tracking but can opt out at creation.
  expect(
    (
      await page.request.patch(`/api/v1/admin/cards/${card.id}`, {
        headers,
        data: { name: card.name, active: false },
      })
    ).status(),
  ).toBe(200)
  await page.reload()
  await tile.getByRole('button', { name: '添加卡片', exact: true }).click()
  const creator = page.getByRole('dialog', { name: '新增卡片', exact: true })
  await expect(creator.getByRole('switch', { name: '追踪账单', exact: true })).toHaveAttribute(
    'aria-checked',
    'true',
  )
  await creator.getByRole('textbox').first().fill('不追踪的新卡')
  await creator.getByPlaceholder('填写 4 位尾号，或填写完整卡号自动提取').fill('9876')
  await creator.getByRole('switch', { name: '追踪账单', exact: true }).locator('..').click()
  await creator.getByRole('button', { name: '保存', exact: true }).click()
  await expect(creator).not.toBeVisible()
  await tile.getByRole('button', { name: '不追踪的新卡 · 9876', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '卡片详情', exact: true })).toContainText(
    '追踪账单：否',
  )
  const saved = (await (await page.request.get('/api/v1/admin/accounts')).json()).items.find(
    (a: { id: string }) => a.id === account.id,
  )
  expect(saved.cards.find((c: { last_four: string }) => c.last_four === '9876').track_bills).toBe(
    false,
  )
  expect(saved.billing_enabled).toBe(false)
  await page
    .getByRole('dialog', { name: '卡片详情', exact: true })
    .getByRole('button', { name: '关闭', exact: true })
    .click()
  await page.getByRole('link', { name: '账单管理', exact: true }).click()
  const historicalRow = page.locator('.el-table__row').filter({ hasText: '不追踪的新卡' })
  await expect(historicalRow).toHaveCount(0)
  await page.getByText('包含未追踪历史', { exact: true }).click()
  await expect(historicalRow).toHaveCount(1)
})

test('card tracking status is explicit and untracked chips stay pale blue', async ({
  page,
}, testInfo) => {
  const { tile, card, headers } = await setup(page, '账单追踪状态显示')
  const details = page.getByRole('dialog', { name: '卡片详情', exact: true })
  const blue = 'rgb(234, 244, 255)'
  for (const [active, tracking] of [
    [true, true],
    [true, false],
    [false, false],
    [false, true],
    [true, true],
  ]) {
    const response = await page.request.patch(`/api/v1/admin/cards/${card.id}`, {
      headers,
      data: { name: card.name, active, track_bills: tracking },
    })
    expect(response.status()).toBe(200)
    await page.reload()
    const chip = tile.getByRole('button', {
      name: `${card.name} · ${card.last_four}${active ? '' : '（停用）'}`,
      exact: true,
    })
    await page.mouse.move(0, 0)
    await expect(chip).toHaveAttribute('title', new RegExp(`追踪账单：${tracking ? '是' : '否'}`))
    if (tracking) {
      await expect(chip).not.toHaveClass(/is-untracked/)
      await expect(chip).not.toHaveCSS('background-color', blue)
    } else {
      await expect(chip).toHaveClass(/is-untracked/)
      await expect(chip).toHaveCSS('background-color', blue)
      await chip.hover()
      await expect(chip).toHaveCSS('background-color', 'rgb(220, 236, 255)')
    }
    if (!active) await expect(chip).toContainText('（停用）')
    await chip.click()
    await expect(details.locator('.card-tracking-status')).toHaveText(
      `追踪账单：${tracking ? '是' : '否'}`,
    )
    await expect(details.locator('.card-validity-status')).toHaveText(active ? '有效' : '已停用')
    const trackingBadge = details.locator('.card-tracking-status')
    const validityBadge = details.locator('.card-validity-status')
    await expect(trackingBadge).toHaveClass(tracking ? /el-tag--success/ : /el-tag--danger/)
    await expect(trackingBadge).toHaveCSS(
      'color',
      tracking ? 'rgb(103, 194, 58)' : 'rgb(245, 108, 108)',
    )
    await expect(details.locator('.card-detail-identity .card-tracking-status')).toHaveCount(0)
    async function expectStatusOrder() {
      await expect
        .poll(() =>
          details.locator('.card-detail-statuses').evaluate((group) => {
            // Read both bounds in one frame while the dialog is animating.
            const [tracking, validity] = Array.from(group.children, (tag) =>
              tag.getBoundingClientRect(),
            )
            return tracking.right < validity.x && Math.abs(tracking.y - validity.y) < 1
          }),
        )
        .toBe(true)
    }
    await expectStatusOrder()
    if (!tracking)
      await expect(details.locator('.card-detail-heading')).toHaveCSS('background-color', blue)
    if (active && !tracking) {
      for (const [width, height] of [
        [1440, 900],
        [390, 844],
        [320, 640],
      ]) {
        await page.setViewportSize({ width, height })
        const body = details.locator('.el-dialog__body')
        expect(await body.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true)
        await expect(details.locator('.card-tracking-status')).toBeInViewport()
        await expect(validityBadge).toBeInViewport()
        await expectStatusOrder()
        await expect(details.getByRole('button', { name: '关闭', exact: true })).toBeInViewport()
        await details.locator('.card-details-dialog').screenshot({
          path: testInfo.outputPath(`card-tracking-status-${width}.png`),
          animations: 'disabled',
        })
      }
    }
    await details.getByRole('button', { name: '关闭', exact: true }).click()
    await expect(details).not.toBeVisible()
    if (active && !tracking) {
      await tile.screenshot({
        path: testInfo.outputPath('untracked-card-chip-320.png'),
        animations: 'disabled',
      })
    }
  }
})

test('card details edit shortcut opens the selected card and preserves private fields', async ({
  page,
}, testInfo) => {
  const { tile, account, card, headers } = await setup(page, '详情直接编辑测试')
  const secret = { card_number: '4111111111114321', cvv: '012' }
  const initial = await page.request.patch(`/api/v1/admin/cards/${card.id}`, {
    headers,
    data: { name: card.name, active: false, track_bills: false, expiry: '09/29', ...secret },
  })
  expect(initial.status()).toBe(200)
  const details = page.getByRole('dialog', { name: '卡片详情', exact: true })
  const editor = page.getByRole('dialog', { name: '编辑卡片', exact: true })
  const chip = tile.getByRole('button', { name: `${card.name} · 4321（停用）`, exact: true })
  const nameInput = editor.locator('.el-form-item').filter({ hasText: /^名称/ }).locator('input')
  await page.reload()
  for (const width of [1440, 320]) {
    await page.setViewportSize({ width, height: 900 })
    await chip.click()
    const editButton = details.getByRole('button', { name: '编辑', exact: true })
    const closeButton = details.getByRole('button', { name: '关闭', exact: true })
    await expect(editButton).toBeInViewport()
    await expect(closeButton).toBeInViewport()
    expect((await editButton.boundingBox())!.x).toBeLessThan((await closeButton.boundingBox())!.x)
    await details.locator('.card-details-dialog').screenshot({
      path: testInfo.outputPath(`card-detail-edit-${width}.png`),
      animations: 'disabled',
    })
    await editButton.click()
    await expect(details).not.toBeVisible()
    await expect(editor).toBeVisible()
    await expect(nameInput).toHaveValue(card.name)
    await expect(editor.getByLabel('有效期', { exact: true })).toHaveValue('09/29')
    await expect(editor.getByLabel('卡号', { exact: true })).toHaveValue('')
    await expect(editor.getByLabel('CVV', { exact: true })).toHaveValue('')
    await nameInput.fill('取消后不应保留的名称')
    await editor.getByRole('button', { name: '取消', exact: true }).click()
    await expect(editor).not.toBeVisible()
    await expect(chip).toBeVisible()
  }
  await chip.click()
  await details.getByRole('button', { name: '编辑', exact: true }).click()
  await nameInput.fill('详情编辑后的卡片')
  const saved = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/admin/cards/${card.id}`) && response.request().method() === 'PATCH',
  )
  await editor.getByRole('button', { name: '保存', exact: true }).click()
  expect((await saved).status()).toBe(200)
  await expect(editor).not.toBeVisible()
  const updatedChip = tile.getByRole('button', {
    name: '详情编辑后的卡片 · 4321（停用）',
    exact: true,
  })
  await updatedChip.click()
  await expect(
    details.getByRole('heading', { name: '详情编辑后的卡片', exact: true }),
  ).toBeVisible()
  await expect(details.locator('.card-tracking-status')).toHaveText('追踪账单：否')
  await expect(details.locator('.card-validity-status')).toHaveText('已停用')
  await details.getByRole('button', { name: '关闭', exact: true }).click()
  await page.reload()
  await expect(updatedChip).toBeVisible()
  const accounts = await (await page.request.get('/api/v1/admin/accounts')).json()
  const savedCard = accounts.items
    .find((item: { id: string }) => item.id === account.id)
    .cards.find((item: { id: string }) => item.id === card.id)
  expect(savedCard.name).toBe('详情编辑后的卡片')
  expect(savedCard.expiry).toBe('09/29')
  expect(savedCard.active).toBe(false)
  expect(savedCard.track_bills).toBe(false)
  const privateResponse = await page.request.post(`/api/v1/admin/cards/${card.id}/private`, {
    headers,
  })
  expect(privateResponse.status()).toBe(200)
  expect(await privateResponse.json()).toMatchObject(secret)
})
