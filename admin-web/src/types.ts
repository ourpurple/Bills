export interface Page<T> {
  items: T[]
  total: number
  page?: number
  page_size?: number
}
export interface Bank {
  id: string
  code: string
  name: string
  default_mode: 'merged' | 'independent'
  active: boolean
}
export interface Holder {
  id: string
  name: string
  active: boolean
}
export interface Group {
  id: string
  name: string
  bank_id: string
  cardholder_id: string
  active: boolean
}
export interface Card {
  id: string
  name: string
  last_four: string
  active: boolean
}
export interface Account {
  id: string
  group_id: string
  effective_active: boolean
  name: string
  mode: 'merged' | 'independent'
  statement_day: number | null
  active: boolean
  bank_id: string
  bank_name: string
  bank_code: string
  cardholder_id: string
  cardholder_name: string
  group_name: string
  cards: Card[]
}
export interface Payment {
  id: string
  amount: string
  currency: string
  occurred_at: string
  note: string
  revoked_at?: string | null
  revoke_reason?: string | null
  cancelled_at?: string | null
}
export interface Revision {
  version: number
  amount: string | null
  due_date: string | null
  reason: string
  created_at: string
}
export interface Transaction {
  index: number
  transaction_date: string | null
  posted_date: string | null
  description: string
  amount: string | null
  direction: string
  currency: string | null
  card_last_four: string | null
}
export interface Statement {
  id: string
  account: Account
  statement_date: string
  currency: string
  status: 'draft' | 'confirmed' | 'void'
  version: number
  source_kind: string
  amount: string | null
  minimum: string | null
  due_date: string | null
  period_start: string | null
  period_end: string | null
  paid: string
  remaining: string | null
  excess: string | null
  payment_status: string
  days_until_due: number | null
  overdue: boolean
  repayments?: Payment[]
  plans?: Payment[]
  transaction_status: string
  transactions?: Transaction[]
  detail_history?: { version: number; status: string; transactions: Transaction[] }[]
  revisions?: Revision[]
}
export interface OverviewStatement extends Statement {
  needs_update: boolean
  expected_statement_date: string | null
}
export interface Overview {
  totals: { currency: string; amount: string }[]
  latest_statements: OverviewStatement[]
  historical_unresolved: Statement[]
  warnings: { account_id: string; account_name: string; kind: string; message: string }[]
  account_count: number
  pending_count: number
  due_soon_count: number
  overdue_count: number
  disclaimer: string
  as_of: string
}
export const modeText = (mode: string) => (mode === 'merged' ? '合并还款' : '独立还款')
export const amountText = (amount: string | null, currency = 'CNY') =>
  amount === null
    ? '金额未知'
    : `${currency === 'CNY' ? '¥ ' : currency + ' '}${Number(amount).toLocaleString('zh-CN', { minimumFractionDigits: currency === 'JPY' ? 0 : 2, maximumFractionDigits: 2 })}`
export const stateText = (s: Statement) =>
  s.status === 'draft'
    ? '待确认'
    : s.status === 'void'
      ? '已作废'
      : s.payment_status === 'overpaid'
        ? '超额标记'
        : s.payment_status === 'settled'
          ? '已标记结清'
          : s.overdue
            ? '已过到期日'
            : s.payment_status === 'partial'
              ? '部分还款'
              : '待还款'
export const stateTagType = (
  s: Statement | null | undefined,
): 'success' | 'warning' | 'danger' | 'info' => {
  if (!s) return 'info'
  const text = stateText(s)
  switch (text) {
    case '已标记结清':
      return 'success'
    case '待还款':
    case '已过到期日':
      return 'danger'
    case '待确认':
    case '部分还款':
    case '超额标记':
      return 'warning'
    case '已作废':
    default:
      return 'info'
  }
}
export const dateTime = (s: string | null | undefined) => {
  if (!s) return '—'
  const date = new Date(s)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}
