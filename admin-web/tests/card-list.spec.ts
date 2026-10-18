import { test, expect, type Page } from '@playwright/test'
import type { Account, Card } from '../src/types'

function card(id: string, overrides: Partial<Card> = {}): Card {
  return {
    id,
    name: '臻享白',
    last_four: '0059',
    active: true,
    track_bills: true,
    card_type: 'credit',
    card_network: 'visa',
    has_card_number: true,
    has_cvv: true,
    credit_limit: '50000.00',
    annual_fee_month: null,
    annual_fee_day: null,
    annual_fee_benefits: null,
    ...overrides,
  }
}
function account(id: string, cards: Card[], overrides: Partial<Account> = {}): Account {
  return {
    id,
    group_id: `group-${id}`,
    group_name: '测试分组',
    name: '家庭合并账户',
    bank_id: 'bank-cmb',
    bank_name: '招商银行',
    bank_code: 'cmb',
    cardholder_id: 'holder-1',
    cardholder_name: '测试持卡人',
    mode: 'merged',
    statement_day: 12,
    active: true,
    effective_active: true,
    cards,
    ...overrides,
  }
}
async function mockAccounts(page: Page, getAccounts: () => Account[]) {
  await page.route('**/api/v1/auth/me', (route) =>
    route.fulfill({ json: { username: 'card-list-test', csrf_token: 'test-csrf' } }),
  )
  for (const resource of ['banks', 'cardholders']) {
    await page.route(`**/api/v1/admin/${resource}?*`, (route) =>
      route.fulfill({ json: { items: [], total: 0 } }),
    )
  }
  await page.route('**/api/v1/admin/accounts?*', (route) => {
    const url = new URL(route.request().url())
    const pageNumber = Number(url.searchParams.get('page'))
    const pageSize = Number(url.searchParams.get('page_size'))
    const accounts = getAccounts()
    return route.fulfill({
      json: {
        items: accounts.slice((pageNumber - 1) * pageSize, pageNumber * pageSize),
        total: accounts.length,
      },
    })
  })
}
async function openList(page: Page) {
  await page.goto('/accounts')
  await page.getByRole('tab', { name: '账户列表', exact: true }).click()
}
const rows = (page: Page) => page.locator('.bank-card-table .el-table__body-wrapper tbody tr')

test('card list shows all cards and metadata with private details opt-in and synchronized edits', async ({
  page,
}) => {
  const cards = [
    card('main-card'),
    card('inactive-card', {
      name: '家庭备用卡',
      last_four: '0007',
      active: false,
      track_bills: false,
      card_type: 'debit',
      card_network: 'unionpay',
    }),
  ]
  const accounts = [
    account('merged-account', cards),
    account(
      'disabled-account',
      [
        card('legacy-card', {
          name: '旧卡资料',
          last_four: '1000',
          card_type: null,
          card_network: null,
          track_bills: undefined,
        }),
      ],
      { name: '已停用账户', effective_active: false, active: false },
    ),
    account('empty-account', []),
  ]
  await mockAccounts(page, () => accounts)
  let privateRequests = 0
  await page.route('**/api/v1/admin/cards/main-card/private', (route) => {
    privateRequests++
    return route.fulfill({ json: { card_number: '4111111111110059', cvv: '123' } })
  })
  await page.route('**/api/v1/admin/cards/main-card', async (route) => {
    expect(route.request().method()).toBe('PATCH')
    Object.assign(cards[0]!, route.request().postDataJSON())
    await route.fulfill({ json: cards[0] })
  })
  await openList(page)
  await expect(page.getByRole('tab')).toHaveText(['账户列表', '账户管理', '持卡人', '银行配置'])
  await expect(page.getByRole('tab', { name: '还款账户', exact: true })).toHaveCount(0)
  await expect(rows(page)).toHaveCount(3)
  await expect(page.getByText('共 3 张银行卡（含停用卡片），卡号仅展示后四位。')).toBeVisible()
  const main = rows(page).filter({ hasText: '臻享白' })
  await expect(main.locator('td')).toHaveText([
    /招\s*招商银行/,
    '测试持卡人',
    '臻享白',
    '0059',
    '信用卡',
    'VISA',
    '家庭合并账户',
    '有效',
    '是',
    '卡片详情',
  ])
  const inactive = rows(page).filter({ hasText: '家庭备用卡' })
  await expect(inactive).toContainText('0007')
  await expect(inactive).toContainText('借记卡')
  await expect(inactive).toContainText('银联')
  await expect(inactive.locator('td').nth(7)).toHaveText('已停用')
  await expect(inactive.locator('td').nth(8)).toHaveText('否')
  const legacy = rows(page).filter({ hasText: '旧卡资料' })
  await expect(legacy.locator('td').nth(4)).toHaveText('未设置')
  await expect(legacy.locator('td').nth(5)).toHaveText('未设置')
  await expect(legacy).toContainText('所属账户已停用')
  await expect(legacy.locator('td').nth(8)).toHaveText('是')
  expect(privateRequests).toBe(0)
  await main.getByRole('button', { name: '卡片详情', exact: true }).click()
  const details = page.getByRole('dialog', { name: '卡片详情', exact: true })
  await expect(details.getByRole('heading', { name: '臻享白', exact: true })).toBeVisible()
  await expect(details).toContainText('招商银行')
  await expect(details).toContainText('测试持卡人')
  await expect(details).toContainText('•••• •••• •••• 0059')
  expect(privateRequests).toBe(0)
  await details.getByRole('button', { name: '查看卡号 / CVV', exact: true }).click()
  await expect(details).toContainText('4111 1111 1111 0059')
  expect(privateRequests).toBe(1)
  await details.getByRole('button', { name: '关闭', exact: true }).click()
  await inactive.getByRole('button', { name: '卡片详情', exact: true }).click()
  await expect(details.getByRole('heading', { name: '家庭备用卡', exact: true })).toBeVisible()
  await expect(details).not.toContainText('4111 1111 1111 0059')
  await expect(details).toContainText('•••• •••• •••• 0007')
  expect(privateRequests).toBe(1)
  await details.getByRole('button', { name: '关闭', exact: true }).click()
  await main.getByRole('button', { name: '卡片详情', exact: true }).click()
  await details.getByRole('button', { name: '编辑', exact: true }).click()
  const editor = page.getByRole('dialog', { name: '编辑卡片', exact: true })
  await editor.getByLabel('名称', { exact: true }).fill('更新后的卡种')
  await editor.getByRole('button', { name: '保存', exact: true }).click()
  await expect(editor).not.toBeVisible()
  await expect(rows(page).filter({ hasText: '更新后的卡种' })).toHaveCount(1)
  await expect(page.getByRole('tab', { name: '账户列表', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await page.getByRole('button', { name: '管理账户与卡片', exact: true }).click()
  await expect(page.getByRole('button', { name: '新增账户', exact: true })).toBeVisible()
})

test('card list includes all paginated accounts and supports refresh and empty states', async ({
  page,
}) => {
  let accounts = Array.from({ length: 101 }, (_, index) =>
    account(`account-${index}`, [
      card(`card-${index}`, {
        name: `银行卡-${index}`,
        last_four: String(index).padStart(4, '0'),
      }),
    ]),
  )
  await mockAccounts(page, () => accounts)
  await openList(page)
  await expect(rows(page)).toHaveCount(101)
  await expect(rows(page).last()).toContainText('银行卡-100')
  accounts = [account('without-cards', [])]
  await page.getByRole('button', { name: '刷新', exact: true }).click()
  await expect(rows(page)).toHaveCount(0)
  await expect(page.getByText('共 0 张银行卡（含停用卡片），卡号仅展示后四位。')).toBeVisible()
  await expect(page.getByText('暂无银行卡，请在账户管理中添加账户与卡片。')).toBeVisible()
  accounts = []
  await page.getByRole('button', { name: '刷新', exact: true }).click()
  await expect(page.getByText('暂无银行卡，请在账户管理中添加账户与卡片。')).toBeVisible()
  await page.getByRole('button', { name: '管理账户与卡片', exact: true }).click()
  await expect(
    page.getByText('先添加持卡人，再选择银行创建还款账户。', { exact: true }),
  ).toBeVisible()
})

test('card list fits desktop and mobile with a reachable details action', async ({
  page,
}, testInfo) => {
  const accounts = [
    account('layout-account', [
      card('layout-card', { name: '一张名称很长的银行卡用于验证列表在窄屏中的可读性' }),
      card('other-card', { name: '万事达世界卡', card_network: 'mastercard' }),
      card('amex-card', { name: '运通白金卡', card_network: 'amex' }),
    ]),
  ]
  await mockAccounts(page, () => accounts)
  await page.setViewportSize({ width: 1920, height: 1000 })
  await openList(page)
  await expect(rows(page)).toHaveCount(3)
  await page.screenshot({
    path: testInfo.outputPath('card-list-desktop.png'),
    fullPage: true,
    animations: 'disabled',
  })
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      width,
    )
    const button = rows(page).first().getByRole('button', { name: '卡片详情', exact: true })
    await expect(button).toBeInViewport()
    const table = page.locator('.bank-card-table .el-scrollbar__wrap').first()
    expect(await table.evaluate((element) => element.scrollWidth)).toBeGreaterThan(width)
    await button.click()
    const details = page.getByRole('dialog', { name: '卡片详情', exact: true })
    await expect(details).toBeVisible()
    await expect(details).toContainText('一张名称很长的银行卡用于验证列表在窄屏中的可读性')
    await details.getByRole('button', { name: '关闭', exact: true }).click()
    await expect(details).not.toBeVisible()
    await page.screenshot({
      path: testInfo.outputPath(`card-list-${width}.png`),
      fullPage: true,
      animations: 'disabled',
    })
  }
})
