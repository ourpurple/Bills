import { test, expect } from '@playwright/test'

test('system task queue: global navigation, mixed tasks, pagination, details, retry and narrow layout', async ({
  page,
}) => {
  const kinds = ['mail_sync', 'prepare_input', 'llm_parse', 'llm_test']
  const tasks = Array.from({ length: 21 }, (_, index) => ({
    id: `task-${index}`,
    kind: kinds[index % kinds.length],
    state: index === 2 ? 'failed' : 'succeeded',
    mailbox_id: index === 0 ? 'queue-mailbox' : null,
    source_id: [1, 2].includes(index) ? `source-${index}` : null,
    attempts: 1,
    max_attempts: 3,
    progress: index,
    error_code: index === 2 ? 'MODEL_TEST_FAILURE' : null,
    error_message: index === 2 ? '合成模型失败记录' : null,
    created_at: '2026-10-12T06:34:00Z',
    available_at: '2026-10-12T06:34:00Z',
    result: { synthetic: true },
  }))
  let retried = false
  const queries: string[] = []
  await page.route('**/api/v1/admin/mailboxes?*', (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: 'queue-mailbox',
            name: '合成任务邮箱',
            address: 'queue@example.test',
            protocol: 'imap',
            host: 'imap.example.test',
            port: 993,
            tls_mode: 'ssl',
            active: true,
            username: 'queue@example.test',
            folder: 'INBOX',
            interval_minutes: 30,
            initial_days: 90,
            sender_filters: [],
            subject_filters: [],
            config_version: 1,
            secret_configured: true,
            paused_reason: null,
            last_error_code: null,
            last_success_at: null,
            last_test_at: null,
          },
        ],
        total: 1,
      },
    }),
  )
  await page.route('**/api/v1/admin/import-status', (route) =>
    route.fulfill({
      json: {
        master_key_configured: true,
        worker_online: true,
        last_heartbeat: '2026-10-12T06:34:00Z',
        ready_sources: 1,
        tasks: { succeeded: 20, failed: 1 },
      },
    }),
  )
  await page.route('**/api/v1/admin/mailboxes/queue-mailbox/test', (route) =>
    route.fulfill({ status: 202, json: tasks[0] }),
  )
  await page.route('**/api/v1/admin/tasks?*', (route) => {
    const url = new URL(route.request().url())
    queries.push(url.search)
    const state = url.searchParams.get('state')
    const number = Number(url.searchParams.get('page') || 1)
    const rows = state ? tasks.filter((task) => task.state === state) : tasks
    return route.fulfill({
      json: { items: rows.slice((number - 1) * 20, number * 20), total: rows.length },
    })
  })
  await page.route('**/api/v1/admin/tasks/task-2/retry', (route) => {
    retried = route.request().method() === 'POST'
    return route.fulfill({ status: 202, json: { ...tasks[2], id: 'retry-task', state: 'queued' } })
  })
  await page.goto('/login')
  await page.getByLabel('用户名', { exact: true }).fill('e2e-admin')
  await page.locator('#login-password').fill('browser-test-only-2026')
  await page.getByRole('button', { name: '进入工作台' }).click()
  await page.getByRole('link', { name: '邮箱与导入' }).click()
  await expect(page.getByRole('tab', { name: '任务队列' })).toHaveCount(0)
  await expect(page.getByRole('link', { name: '任务队列' })).toBeVisible()
  await page.getByRole('button', { name: '测试连接', exact: true }).click()
  await expect(page).toHaveURL(/\/tasks$/)
  await expect(page.getByRole('link', { name: '任务队列' })).toHaveClass(/active/)
  await expect(page.locator('.topbar')).toContainText('任务队列')
  const rows = page.locator('.el-table__body-wrapper tbody tr')
  await expect(rows).toHaveCount(20)
  await expect(rows.first()).toContainText('合成任务邮箱')
  await expect(rows.nth(1)).toContainText('通用资料转换')
  await expect(rows.nth(2)).toContainText('大模型识别')
  await expect(rows.nth(3)).toContainText('模型配置')
  await expect(rows.nth(3)).not.toContainText('手工上传')
  await page.locator('.el-pagination .number').filter({ hasText: /^2$/ }).click()
  await expect(rows).toHaveCount(1)
  await page.locator('.import-filters .el-select').click({ timeout: 8000 })
  await page.getByRole('option', { name: '失败', exact: true }).click({ timeout: 8000 })
  await expect(rows.first()).toContainText('合成模型失败记录')
  expect(queries.at(-1)).toBe('?page=1&state=failed')
  await rows.first().getByRole('button', { name: '详情', exact: true }).click()
  const detail = page.getByRole('dialog', { name: '任务详情' })
  await expect(detail).toContainText('MODEL_TEST_FAILURE')
  await expect(detail).toContainText('"synthetic": true')
  await detail.getByRole('button', { name: '关闭此对话框' }).click()
  await rows.first().getByRole('button', { name: '重试', exact: true }).click()
  await page.locator('.el-message-box').getByRole('button', { name: '确定', exact: true }).click()
  await expect(page.getByText('重试已排队', { exact: true })).toBeVisible()
  expect(retried).toBe(true)
  await expect(rows.first()).toContainText('合成模型失败记录')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  )
  await expect(page.getByRole('link', { name: '任务队列' })).toBeVisible()
})
