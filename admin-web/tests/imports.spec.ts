import { test, expect } from '@playwright/test'

test('P2: inactive mailbox, upload, worker conversion, safe preview and narrow layout', async ({
  page,
}) => {
  const pageErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  const external: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('untrusted-preview.example')) external.push(request.url())
  })
  await page.goto('/login')
  await page.getByLabel('用户名', { exact: true }).fill('e2e-admin')
  await page.locator('#login-password').fill('browser-test-only-2026')
  await page.getByRole('button', { name: '进入工作台' }).click()
  await expect(page.getByRole('heading', { name: '让账单，井然有序。' })).toBeVisible()
  await page.getByRole('link', { name: '邮件中心' }).click()
  await expect(page.getByRole('heading', { name: '正在运行', exact: true })).toBeVisible()
  const mailListTab = page.getByRole('tab', { name: '邮件列表', exact: true })
  const mailboxTab = page.getByRole('tab', { name: '邮箱配置', exact: true })
  await expect(mailListTab).toHaveAttribute('aria-selected', 'true')
  await expect(mailboxTab).toHaveAttribute('aria-selected', 'false')
  await expect(page.getByRole('tab').first()).toHaveText('邮件列表')
  await expect(page.getByLabel('选择资料文件')).toBeAttached()
  await expect(page.locator('.import-upload')).toBeVisible()
  await mailboxTab.click()
  await expect(page.getByRole('button', { name: '新增邮箱', exact: true })).toBeVisible()
  await page.reload()
  await expect(mailListTab).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.import-upload')).toBeVisible()
  await mailboxTab.click()
  await expect(
    page.getByText('尚未配置邮箱。你也可以先在「邮件列表」上传 .eml 或 PDF。'),
  ).toBeVisible()
  await page.getByRole('button', { name: '新增邮箱', exact: true }).click()
  const dialog = page.getByRole('dialog')
  const field = (label: string) =>
    dialog
      .locator('.el-form-item')
      .filter({ has: page.locator('.el-form-item__label', { hasText: label }) })
  await field('邮箱名称').locator('input').fill('隔离演示邮箱')
  await field('邮箱地址').locator('input').fill('owner@example.test')
  await field('邮箱服务器').locator('input').fill('imap.example.test')
  await field('登录用户名').locator('input').fill('owner@example.test')
  await field('密码 / 授权码').locator('input').fill('synthetic-not-a-real-secret')
  await field('启用定时同步').locator('.el-switch').click()
  await dialog.getByRole('button', { name: '保存邮箱' }).click()
  await expect(dialog).not.toBeVisible()
  const box = page.locator('.mailbox-entry').filter({ hasText: '隔离演示邮箱' })
  await expect(box).toContainText('已停用')
  await expect(box.getByRole('button', { name: '立即同步' })).toBeDisabled()
  await box.getByRole('button', { name: '编辑邮箱' }).click()
  await expect(field('密码 / 授权码').locator('input')).toHaveValue('')
  await dialog.getByRole('button', { name: '取消', exact: true }).click()

  const before = await (await page.request.get('/api/v1/admin/statements')).json()
  await mailListTab.click()
  await expect(page.getByRole('tab', { name: '邮件列表', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  expect(
    (await page.locator('.import-filters .el-select').first().boundingBox())?.width,
  ).toBeGreaterThan(150)
  const raw = Buffer.from(
    [
      'From: synthetic@example.test',
      'Subject: Synthetic P2 safe preview',
      'MIME-Version: 1.0',
      'Content-Type: text/html; charset=utf-8',
      '',
      '<html><body><h1>Safe source 123.45</h1><script>window.p2Unsafe=true</script>',
      '<img src="https://untrusted-preview.example/tracking"><p>Text only, not a statement.</p></body></html>',
    ].join('\r\n'),
  )
  const upload = { name: 'synthetic.eml', mimeType: 'message/rfc822', buffer: raw }
  await page.getByLabel('选择资料文件').setInputFiles(upload)
  const row = page.getByRole('row').filter({ hasText: 'Synthetic P2 safe preview' })
  await expect(row).toContainText('文本已准备', { timeout: 20000 })
  await row.getByRole('button', { name: '查看详情' }).click()
  const preview = page.getByRole('dialog', { name: '安全资料预览 · 不是账单解析结果' })
  await expect(preview.locator('pre')).toContainText('Safe source 123.45')
  await expect(preview.locator('pre')).not.toContainText('p2Unsafe')
  expect(
    await page.evaluate(() => (window as Window & { p2Unsafe?: boolean }).p2Unsafe),
  ).toBeUndefined()
  expect(external).toEqual([])
  expect(pageErrors).toEqual([])
  await expect(preview.getByRole('button', { name: '重新收取并转换', exact: true })).toHaveCount(0)
  await preview.getByRole('button', { name: '重新转换', exact: true }).click()
  const confirmation = page.getByRole('dialog', { name: '重新转换', exact: true })
  await expect(confirmation).toContainText('不连接邮箱')
  await confirmation.getByRole('button', { name: '确定', exact: true }).click()
  await expect(preview).not.toBeVisible()
  await expect(page.getByText('重新转换任务已排队', { exact: true })).toBeVisible()
  await expect(row).toContainText('文本已准备', { timeout: 20000 })
  await expect(row).toHaveCount(1)
  await page.getByLabel('选择资料文件').setInputFiles(upload)
  await expect(page.getByText('该资料已经导入，不重复创建来源')).toBeVisible()
  await expect(row).toHaveCount(1)
  const after = await (await page.request.get('/api/v1/admin/statements')).json()
  expect(after.total).toBe(before.total)
  await page.getByRole('link', { name: '任务队列' }).click()
  await expect(page).toHaveURL(/\/tasks$/)
  await expect(page.getByRole('link', { name: '任务队列' })).toHaveClass(/active/)
  await page.reload()
  await expect(page.getByRole('heading', { name: '所有任务，统一查看。' })).toBeVisible()
  await expect(page.getByRole('row').filter({ hasText: '通用资料转换' }).first()).toContainText(
    '任务完成',
  )
  await page.getByRole('link', { name: '邮件中心' }).click()
  await expect(page.getByRole('tab', { name: '任务队列' })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: '邮件列表', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  await page.screenshot({ path: test.info().outputPath('imports.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  )
})

test('source recovery: refetch confirmation queues the selected email without model calls', async ({
  page,
}) => {
  const source = {
    id: 'synthetic-refetch-source',
    mailbox_id: 'synthetic-mailbox',
    filename: '',
    subject: '合成邮件 · 解码失败',
    sender: 'synthetic@example.test',
    state: 'unsupported',
    reason: 'INPUT_UNSUPPORTED',
    size_bytes: 1024,
    created_at: '2026-10-12T10:00:00+08:00',
    input_id: 'synthetic-input',
    raw_available: true,
  }
  const operations: unknown[] = []
  await page.route('**/api/v1/admin/sources?*', async (route) => {
    await route.fulfill({ json: { items: [source], total: 1, page: 1, page_size: 20 } })
  })
  await page.route('**/api/v1/admin/sources/synthetic-refetch-source/preview', async (route) => {
    await route.fulfill({
      json: {
        source,
        input: { state: 'unsupported', warnings: ['NO_TEXT', 'TEXT_DECODE_FAILED'], parts: [] },
      },
    })
  })
  await page.route('**/api/v1/admin/sources/synthetic-refetch-source/reprocess', async (route) => {
    operations.push(route.request().postDataJSON())
    source.state = 'pending'
    source.reason = ''
    source.input_id = ''
    await route.fulfill({
      status: 202,
      json: { id: 'synthetic-refetch-task', kind: 'mail_fetch', state: 'queued' },
    })
  })
  const modelRequests: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/parse'))
      modelRequests.push(request.url())
  })
  await page.goto('/login')
  await page.getByLabel('用户名', { exact: true }).fill('e2e-admin')
  await page.locator('#login-password').fill('browser-test-only-2026')
  await page.getByRole('button', { name: '进入工作台' }).click()
  await page.getByRole('link', { name: '邮件中心' }).click()
  await expect(page.getByRole('tab', { name: '邮件列表', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  const row = page.getByRole('row').filter({ hasText: source.subject })
  await row.getByRole('button', { name: '查看详情', exact: true }).click()
  const preview = page.getByRole('dialog', { name: '安全资料预览 · 不是账单解析结果' })
  await expect(preview).toContainText('正文字符解码失败')
  await preview.getByRole('button', { name: '重新收取并转换', exact: true }).click()
  const confirmation = page.getByRole('dialog', { name: '重新收取并转换', exact: true })
  await expect(confirmation).toContainText('从原邮箱重新下载这一封邮件')
  await confirmation.getByRole('button', { name: '取消', exact: true }).click()
  expect(operations).toEqual([])
  await preview.getByRole('button', { name: '重新收取并转换', exact: true }).click()
  await confirmation.getByRole('button', { name: '确定', exact: true }).click()
  await expect(preview).not.toBeVisible()
  await expect(row).toContainText('等待转换')
  expect(operations).toEqual([{ mode: 'refetch' }])
  expect(modelRequests).toEqual([])
  await expect(row.getByRole('button', { name: '转为文本', exact: true })).toBeDisabled()
})

test('source progress distinguishes prepared, pending review and saved bills', async ({ page }) => {
  const base = {
    mailbox_id: null,
    filename: 'synthetic.eml',
    sender: 'synthetic@example.test',
    state: 'ready',
    reason: null,
    size_bytes: 1024,
    created_at: '2026-10-12T10:00:00+08:00',
    input_id: 'synthetic-input',
    raw_available: true,
    llm_parsed: false,
    saved_statement_count: 0,
    pending_candidate_count: 0,
  }
  const sources = [
    { ...base, id: 'prepared', subject: '仅完成资料转换' },
    {
      ...base,
      id: 'recognized',
      subject: '模型识别等待核对',
      llm_parsed: true,
      pending_candidate_count: 1,
    },
    { ...base, id: 'saved', subject: '模型账单已保存', llm_parsed: true, saved_statement_count: 1 },
    {
      ...base,
      id: 'partial-save',
      subject: '一封邮件多份账单',
      llm_parsed: true,
      saved_statement_count: 1,
      pending_candidate_count: 1,
    },
    { ...base, id: 'rejected', subject: '识别候选未采用', llm_parsed: true },
    {
      ...base,
      id: 'reconverting',
      subject: '已保存后重新转换',
      llm_parsed: true,
      saved_statement_count: 1,
      state: 'pending',
    },
  ]
  await page.route('**/api/v1/admin/sources?*', async (route) => {
    await route.fulfill({ json: { items: sources, total: sources.length, page: 1, page_size: 20 } })
  })
  await page.goto('/login')
  await page.getByLabel('用户名', { exact: true }).fill('e2e-admin')
  await page.locator('#login-password').fill('browser-test-only-2026')
  await page.getByRole('button', { name: '进入工作台' }).click()
  await page.getByRole('link', { name: '邮件中心' }).click()
  await expect(page.getByRole('tab', { name: '邮件列表', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  const row = (subject: string) => page.getByRole('row').filter({ hasText: subject })
  await expect(row('仅完成资料转换')).toContainText('文本已准备')
  await expect(row('模型识别等待核对')).toContainText('已识别 · 待核对')
  await expect(row('模型账单已保存')).toContainText('已识别并保存')
  await expect(row('模型账单已保存')).toContainText('已保存 1 份账单')
  await expect(row('模型账单已保存')).not.toContainText('文本已准备')
  await expect(row('一封邮件多份账单')).toContainText('已识别 · 部分保存')
  await expect(row('一封邮件多份账单')).toContainText('1 份候选待核对')
  await expect(row('识别候选未采用')).toContainText('已识别 · 未保存')
  await expect(row('已保存后重新转换')).toContainText('已识别并保存')
  await expect(row('已保存后重新转换')).toContainText('等待转换')
  await page.screenshot({ path: test.info().outputPath('source-progress.png'), fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  )
})

test('mail center: suspected tags, ignored details, manual actions and batch controls', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  const base = {
    mailbox_id: 'demo-mailbox',
    filename: '',
    sender: '广发银行 <creditcard@example.test>',
    size_bytes: 1024,
    created_at: '2026-10-13T12:17:39+08:00',
    llm_parsed: false,
    saved_statement_count: 0,
    pending_candidate_count: 0,
  }
  const sources = [
    {
      ...base,
      id: 'bill',
      subject: '广发信用卡 2026年10月电子账单',
      suspected_bill: true,
      ignored: false,
      state: 'ready',
      reason: null,
      raw_available: true,
      input_id: 'bill-input',
      can_parse: true,
    },
    {
      ...base,
      id: 'reminder',
      subject: '【广发卡 09月还款提醒】',
      suspected_bill: false,
      ignored: true,
      state: 'skipped',
      reason: 'NOT_CREDIT_CARD_BILL',
      raw_available: false,
      input_id: null,
      can_parse: false,
    },
    {
      ...base,
      id: 'club',
      subject: '金鹏俱乐部9月电子对账单',
      sender: '金鹏俱乐部 <club@example.test>',
      suspected_bill: false,
      ignored: true,
      state: 'skipped',
      reason: 'NOT_CREDIT_CARD_BILL',
      raw_available: false,
      input_id: null,
      can_parse: false,
    },
  ]
  const actions: string[] = []
  const modelCalls: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/parse'))
      modelCalls.push(request.url())
  })
  await page.route('**/api/v1/admin/mailboxes*', async (route) => {
    await route.fulfill({
      json: {
        items: [
          {
            id: 'demo-mailbox',
            name: '测试邮箱',
            address: 'owner@example.test',
            protocol: 'imap',
            host: 'imap.example.test',
            port: 993,
            active: true,
            sender_filters: [],
            subject_filters: [],
          },
        ],
        total: 1,
      },
    })
  })
  await page.route('**/api/v1/admin/sources?*', async (route) => {
    await route.fulfill({ json: { items: sources, total: sources.length, page: 1, page_size: 20 } })
  })
  await page.route('**/api/v1/admin/sources/*/ignore', async (route) => {
    const id = route.request().url().split('/').at(-2)!
    actions.push(`ignore:${id}`)
    const source = sources.find((s) => s.id === id)!
    source.state = 'skipped'
    source.reason = 'MANUALLY_IGNORED'
    source.ignored = true
    await route.fulfill({ json: source })
  })
  await page.route('**/api/v1/admin/sources/*/restore', async (route) => {
    const id = route.request().url().split('/').at(-2)!
    actions.push(`restore:${id}`)
    const source = sources.find((s) => s.id === id)!
    source.state = source.input_id ? 'ready' : 'pending'
    source.reason = null
    source.ignored = false
    await route.fulfill({ json: source })
  })
  await page.goto('/login')
  await page.getByLabel('用户名', { exact: true }).fill('e2e-admin')
  await page.locator('#login-password').fill('browser-test-only-2026')
  await page.getByRole('button', { name: '进入工作台' }).click()
  await page.getByRole('link', { name: '邮件中心' }).click()
  const row = (subject: string) => page.getByRole('row').filter({ hasText: subject })
  const bill = row(sources[0].subject)
  const reminder = row(sources[1].subject)
  await expect(bill).toContainText('疑似账单')
  await expect(bill).toContainText('owner@example.test')
  await expect(reminder).toContainText('已忽略')
  await expect(reminder).not.toContainText('疑似账单')
  await expect(bill.getByRole('button', { name: '重新转换', exact: true })).toBeVisible()
  await expect(bill.getByRole('button', { name: '模型识别', exact: true })).toBeVisible()
  await expect(reminder.getByRole('button', { name: '收取并转文本', exact: true })).toBeVisible()
  await reminder.getByRole('button', { name: '查看详情', exact: true }).click()
  const drawer = page.getByRole('dialog', { name: '安全资料预览 · 不是账单解析结果' })
  await expect(drawer).toContainText('未通过信用卡账单初筛')
  await expect(drawer.getByRole('button', { name: '重新收取并转换', exact: true })).toBeVisible()
  await drawer.press('Escape')
  await expect(drawer).not.toBeVisible()
  await page.screenshot({ path: test.info().outputPath('mail-center.png'), fullPage: true })
  await bill.getByRole('button', { name: '忽略', exact: true }).click()
  await page
    .getByRole('dialog', { name: '忽略邮件', exact: true })
    .getByRole('button', { name: '取消', exact: true })
    .click()
  expect(actions).toEqual([])
  await bill.locator('.el-checkbox').click()
  await expect(bill.getByRole('checkbox')).toBeChecked()
  await page.getByRole('button', { name: '忽略所选', exact: true }).click()
  await page
    .getByRole('dialog', { name: '忽略邮件', exact: true })
    .getByRole('button', { name: '确定', exact: true })
    .click()
  await expect(bill).toContainText('已忽略')
  await expect(bill.getByRole('button', { name: '模型识别', exact: true })).toBeVisible()
  await bill.getByRole('button', { name: '恢复', exact: true }).click()
  await expect(bill).toContainText('文本已准备')
  await reminder.getByRole('button', { name: '恢复', exact: true }).click()
  await expect(reminder).toContainText('等待转换')
  expect(actions).toEqual(['ignore:bill', 'restore:bill', 'restore:reminder'])
  expect(modelCalls).toEqual([])
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  )
})
