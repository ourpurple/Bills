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
  await expect(page.getByRole('tab', { name: '账户列表', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
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
    /••\s*0059/,
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
  // Explicit colors on badges, labels and the fixed action must also be muted.
  for (const row of [inactive, legacy]) {
    const detailsAction = row.getByRole('button', { name: '卡片详情', exact: true })
    await expect(detailsAction).toBeEnabled()
    for (const hovered of [false, true]) {
      if (hovered) await detailsAction.hover()
      await expect
        .poll(() =>
          row
            .locator('td, .cell, .cell *')
            .evaluateAll((elements) =>
              [...new Set(elements.map((element) => getComputedStyle(element).color))].sort(),
            ),
        )
        .toEqual(['rgb(163, 166, 160)'])
    }
  }
  await expect(main.locator('.card-product-name')).not.toHaveCSS('color', 'rgb(163, 166, 160)')
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
  await expect(page.getByRole('button', { name: '管理账户与卡片', exact: true })).toHaveCount(0)
  await page.getByRole('tab', { name: '账户管理', exact: true }).click()
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
  await expect(page.getByRole('button', { name: '管理账户与卡片', exact: true })).toHaveCount(0)
  await page.getByRole('tab', { name: '账户管理', exact: true }).click()
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
    for (const filter of await page.locator('.card-list-filter').all()) {
      const bounds = (await filter.boundingBox())!
      expect(bounds.x).toBeGreaterThanOrEqual(0)
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width)
    }
    await chooseCardFilter(page, '卡组织', '万事达')
    await expect(rows(page)).toHaveCount(1)
    await expect(rows(page)).toContainText('万事达世界卡')
    await page.getByRole('button', { name: '重置筛选', exact: true }).click()
    await expect(rows(page)).toHaveCount(3)
    const button = rows(page).first().getByRole('button', { name: '卡片详情', exact: true })
    await button.scrollIntoViewIfNeeded()
    await expect(button).toBeInViewport()
    const table = page.locator('.bank-card-table .el-scrollbar__wrap').first()
    expect(await table.evaluate((element) => element.scrollWidth)).toBeGreaterThan(width)
    await button.click()
    const details = page.getByRole('dialog', { name: '卡片详情', exact: true })
    await expect(details).toBeVisible()
    await expect(details).toContainText('一张名称很长的银行卡用于验证列表在窄屏中的可读性')
    await details.getByRole('button', { name: '关闭', exact: true }).click()
    await expect(details).not.toBeVisible()
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({
      path: testInfo.outputPath(`card-list-${width}.png`),
      fullPage: true,
      animations: 'disabled',
    })
  }
})

async function chooseCardFilter(page: Page, label: string, option: string) {
  await page.getByRole('combobox', { name: `${label}筛选`, exact: true }).click()
  await page.getByRole('option', { name: option, exact: true }).click()
}

test('card list sorts banks by priority then holder while keeping local banks last', async ({
  page,
}) => {
  const bankOrder = [
    ['icbc', '工商银行'],
    ['boc', '中国银行'],
    ['abc', '农业银行'],
    ['ccb', '建设银行'],
    ['bocom', '交通银行'],
    ['psbc', '邮储银行'],
    ['cmb', '招商银行'],
    ['cgb', '广发银行'],
    ['citic', '中信银行'],
    ['spdb', '浦发银行'],
    ['cmbc', '民生银行'],
    ['cib', '兴业银行'],
    ['hxb', '华夏银行'],
    ['bj', '北京银行'],
    ['sh', '上海银行'],
  ]
  const expected = bankOrder.flatMap(([code]) => [`${code}-陈`, `${code}-张`])
  const accounts = bankOrder
    .flatMap(([code, name], index) =>
      ['张', '陈'].map((holder, holderIndex) =>
        account(`${code}-${holder}`, [card(`${code}-${holder}`, { name: `${code}-${holder}` })], {
          bank_id: `bank-${code}`,
          bank_code: code!,
          bank_name: name!,
          cardholder_id: `holder-${holder}`,
          cardholder_name: `${holder}先生`,
          statement_day: 28 - index - holderIndex,
        }),
      ),
    )
    .reverse()
  await mockAccounts(page, () => accounts)
  await openList(page)
  await expect(rows(page).locator('td:nth-child(3)')).toHaveText(expected)
  await chooseCardFilter(page, '持卡人', '张先生')
  await expect(rows(page).locator('td:nth-child(3)')).toHaveText(
    bankOrder.map(([code]) => `${code}-张`),
  )
  await page.getByRole('combobox', { name: '银行筛选', exact: true }).click()
  await expect(page.getByRole('option')).toHaveText(bankOrder.map(([, name]) => name!))
  await page.keyboard.press('Escape')
})

test('card dropdowns compose, clear, reset and retain filters on refresh', async ({ page }) => {
  let accounts = [
    account(
      'cmb-chen',
      [
        card('visa', { name: '有效VISA卡' }),
        card('debit', {
          name: '停用借记卡',
          active: false,
          card_type: 'debit',
          card_network: 'unionpay',
        }),
      ],
      { cardholder_id: 'chen', cardholder_name: '陈先生' },
    ),
    account('icbc-zhang', [card('mastercard', { name: '万事达卡', card_network: 'mastercard' })], {
      bank_id: 'bank-icbc',
      bank_code: 'icbc',
      bank_name: '工商银行',
      cardholder_id: 'zhang',
      cardholder_name: '张先生',
    }),
    account(
      'cmb-zhang',
      [
        card('missing', {
          name: '未设置卡',
          card_type: null,
          card_network: undefined,
        }),
        card('amex', { name: '运通卡', card_network: 'amex' }),
      ],
      {
        cardholder_id: 'zhang',
        cardholder_name: '张先生',
        effective_active: false,
      },
    ),
  ]
  await mockAccounts(page, () => accounts)
  await openList(page)
  const reset = page.getByRole('button', { name: '重置筛选', exact: true })
  await expect(reset).toBeDisabled()
  await expect(rows(page)).toHaveCount(5)
  await chooseCardFilter(page, '银行', '招商银行')
  await expect(rows(page)).toHaveCount(4)
  await chooseCardFilter(page, '持卡人', '陈先生')
  await expect(rows(page)).toHaveCount(2)
  await chooseCardFilter(page, '卡类型', '信用卡')
  await chooseCardFilter(page, '卡组织', 'VISA')
  await chooseCardFilter(page, '状态', '有效')
  await expect(rows(page)).toHaveCount(1)
  await expect(rows(page)).toContainText('有效VISA卡')
  await expect(page.locator('.card-list-toolbar')).toContainText('共 5 张银行卡，筛选出 1 张')
  await page.getByRole('button', { name: '刷新', exact: true }).click()
  await expect(rows(page)).toHaveCount(1)
  await expect(
    page
      .locator('.card-list-filter')
      .filter({ has: page.getByRole('combobox', { name: '银行筛选', exact: true }) }),
  ).toContainText('招商银行')
  await chooseCardFilter(page, '卡组织', '银联')
  await expect(rows(page)).toHaveCount(0)
  await expect(page.getByText('暂无符合筛选条件的银行卡。', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '清空筛选', exact: true }).click()
  await expect(rows(page)).toHaveCount(5)
  await expect(reset).toBeDisabled()
  for (const [label, option, names] of [
    ['卡类型', '借记卡', ['停用借记卡']],
    ['卡类型', '未设置', ['未设置卡']],
    ['卡组织', '银联', ['停用借记卡']],
    ['卡组织', '万事达', ['万事达卡']],
    ['卡组织', 'AE（美国运通）', ['运通卡']],
    ['卡组织', '未设置', ['未设置卡']],
    ['状态', '有效', ['万事达卡', '有效VISA卡']],
    ['状态', '已停用', ['停用借记卡']],
    ['状态', '所属账户已停用', ['未设置卡', '运通卡']],
  ] as const) {
    await chooseCardFilter(page, label, option)
    await expect(rows(page).locator('td:nth-child(3)')).toHaveText([...names])
    await reset.click()
  }
  // Clearing one control keeps the other filters intact.
  await chooseCardFilter(page, '持卡人', '张先生')
  await chooseCardFilter(page, '银行', '工商银行')
  const bankSelect = page.locator('.card-list-filter').filter({ hasText: '银行' })
  await bankSelect.hover()
  await bankSelect.locator('.el-select__clear').click()
  await expect(rows(page)).toHaveCount(3)
  await expect(
    page
      .locator('.card-list-filter')
      .filter({ has: page.getByRole('combobox', { name: '持卡人筛选', exact: true }) }),
  ).toContainText('张先生')
  // Choices remain available independently of the current filtered subset.
  await chooseCardFilter(page, '持卡人', '陈先生')
  await expect(rows(page)).toHaveCount(2)
  accounts = accounts.slice(1)
  await page.getByRole('button', { name: '刷新', exact: true }).click()
  await expect(rows(page)).toHaveCount(0)
  await expect(page.getByText('暂无符合筛选条件的银行卡。', { exact: true })).toBeVisible()
  await reset.click()
  await expect(rows(page)).toHaveCount(3)
})

test('account collection opens the list by default with accurate overview and styled bank groups', async ({
  page,
}, testInfo) => {
  const accounts = [
    account(
      'icbc',
      [
        card('icbc-cny', { name: '牡丹白金卡', last_four: '3484', card_network: 'unionpay' }),
        card('icbc-fx', { name: '环球旅行卡', last_four: '0377' }),
      ],
      {
        bank_id: 'bank-icbc',
        bank_code: 'icbc',
        bank_name: '工商银行',
        cardholder_name: '陈先生',
        name: '工行信用卡账户',
      },
    ),
    account(
      'boc',
      [
        card('boc-up', { name: '环球通白金卡', last_four: '3536', card_network: 'unionpay' }),
        card('boc-mc', {
          name: '万事达世界卡',
          last_four: '4853',
          card_network: 'mastercard',
          track_bills: false,
        }),
      ],
      {
        bank_id: 'bank-boc',
        bank_code: 'boc',
        bank_name: '中国银行',
        cardholder_name: '陈先生',
        name: '中行信用卡账户',
      },
    ),
    account(
      'abc',
      [
        card('abc-missing', {
          name: '悠然白金卡',
          last_four: '8753',
          card_type: null,
          card_network: null,
        }),
        card('abc-debit', {
          name: '金穗借记卡',
          last_four: '4029',
          card_type: 'debit',
          card_network: 'unionpay',
          active: false,
        }),
      ],
      {
        bank_id: 'bank-abc',
        bank_code: 'abc',
        bank_name: '农业银行',
        cardholder_name: '陈先生',
        name: '农行账户',
      },
    ),
    account(
      'cmb',
      [card('cmb-amex', { name: '百夫长白金卡', last_four: '1628', card_network: 'amex' })],
      {
        cardholder_id: 'holder-2',
        cardholder_name: '杨女士',
        name: '招商银行账户',
        effective_active: false,
      },
    ),
  ]
  await mockAccounts(page, () => accounts)
  await page.setViewportSize({ width: 1920, height: 1080 })
  await openList(page)
  await expect(page.locator('.card-count')).toHaveText('7 张')
  await expect(page.locator('.card-collection-heading p')).toHaveText('4 家银行 · 2 位持卡人')
  await expect(page.locator('.collection-active-count')).toHaveText('5 张有效卡片')
  await expect(rows(page).filter({ has: page.locator('.card-status-label.inactive') })).toHaveCount(
    1,
  )
  await expect(
    rows(page).filter({ has: page.locator('.card-status-label.account_inactive') }),
  ).toHaveCount(1)
  await expect(page.locator('.bank-card-table .bank-group-start')).toHaveCount(3)
  await expect(page.locator('.bank-card-table .is-inactive')).toHaveCount(2)
  await expect(page.getByRole('button', { name: '管理账户与卡片', exact: true })).toHaveCount(0)
  for (const width of [1920, 1440]) {
    await page.setViewportSize({ width, height: 1080 })
    await page.screenshot({
      path: testInfo.outputPath(`card-overview-${width}.png`),
      fullPage: true,
      animations: 'disabled',
    })
  }
  await chooseCardFilter(page, '状态', '有效')
  await expect(rows(page)).toHaveCount(5)
  // Overview counts describe the entire collection, not only filtered cards.
  await expect(page.locator('.card-count')).toHaveText('7 张')
  await expect(page.locator('.collection-active-count')).toHaveText('5 张有效卡片')
  await page.getByRole('tab', { name: '账户管理', exact: true }).click()
  await expect(page.getByRole('button', { name: '新增账户', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '刷新', exact: true }).click()
  await expect(page.getByRole('tab', { name: '账户管理', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await page.reload()
  await expect(page.getByRole('tab', { name: '账户列表', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await expect(rows(page)).toHaveCount(7)
})
