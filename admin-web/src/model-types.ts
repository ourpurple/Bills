import type { Statement } from './types'
export interface ModelProfile {
  bank_code: string
  media_type: string
  prompt_version: string
  converter_version: string
  verified: boolean
}
export interface ModelConfiguration {
  configured: boolean
  version: number
  base_url?: string
  model?: string
  active?: boolean
  auto_parse?: boolean
  auto_publish_profiles?: ModelProfile[]
  timeout_seconds?: number
  max_tokens?: number
  input_chars?: number
  daily_call_limit?: number
  task_call_limit?: number
  json_mode?: boolean
  input_price?: string | null
  output_price?: string | null
  price_currency?: string
  api_key_configured?: boolean
}
export interface ModelStatus {
  config: ModelConfiguration
  master_key_configured: boolean
  pending_candidates: number
  usage: {
    reserved_calls: number
    unknown_cost_calls: number
    cost_estimates: Record<string, string>
    day_timezone: string
  }
}
export interface ModelTransaction {
  transaction_date: string | null
  posted_date: string | null
  description: string
  amount: string | null
  direction: 'debit' | 'credit' | 'unknown'
  currency: string | null
  card_last_four: string | null
  evidence?: { part: number; quote: string }
  index?: number
}
export interface ReviewFields {
  account_id: string | null
  statement_date: string | null
  currency: string | null
  amount: string | null
  minimum: string | null
  due_date: string | null
  period_start: string | null
  period_end: string | null
  transaction_status: string
  transactions: ModelTransaction[]
}
export interface Candidate {
  id: string
  run_id: string
  version: number
  state: string
  match_state: string
  bank_code: string | null
  holder_name: string | null
  card_last_four: string[]
  fields: ReviewFields
  warnings: string[]
  transaction_count: number
  source_id?: string
  statement_id: string | null
  extraction?: Record<string, unknown> & {
    evidence: Record<string, { part: number; quote: string }>
  }
  existing_statement: { id: string; version: number; status: string; amount: string | null } | null
  revisions?: { version: number; reason: string; created_at: string; fields: ReviewFields }[]
}
export interface ModelCall {
  id: string
  model: string
  kind: string
  state: string
  error_code: string | null
  input_tokens: number | null
  output_tokens: number | null
  cost_estimate: string | null
  price_currency: string
  created_at: string
  config_version: number
}
export interface ModelRun {
  id: string
  state: string
  error_code: string | null
  candidate_ids: string[]
  warnings: string[]
  task: { id: string; state: string; error_message: string | null } | null
}
export interface PublishResult {
  candidate: Candidate
  statement: Statement
  duplicate: boolean
}
export const modelState = (s: string) =>
  ({
    pending: '待核对',
    published: '已发布',
    duplicate: '已标记相同',
    rejected: '已忽略',
    queued: '排队中',
    running: '识别中',
    retry_wait: '等待重试',
    succeeded: '完成',
    failed: '失败',
    cancelled: '已取消',
    started: '调用中',
    unknown: '结果未知',
  })[s] || s
export const detailState = (s: string) =>
  ({ not_provided: '未提供详单', partial: '部分详单', complete: '完整详单' })[s] || s
export const warningText = (s: string) => {
  const known: Record<string, string> = {
    ACCOUNT_NEEDS_REVIEW: '账户未唯一匹配，或人工账户与模型识别不一致',
    EXISTING_STATEMENT: '已有相同账户、账单日及币种的账单',
    LLM_INPUT_TRUNCATED: '资料超过输入上限，模型未收到全部文本',
    DETAIL_PARTIAL: '详单不完整',
    DETAIL_NEEDS_REVIEW: '交易字段或证据不完整',
    MODEL_WARNING: '模型提示存在不确定信息',
  }
  const fields: Record<string, string> = {
    BANK_CODE: '银行',
    HOLDER_NAME: '持卡人',
    CARD_LAST_FOUR: '卡号尾号',
    STATEMENT_DATE: '账单日期',
    DUE_DATE: '到期日',
    CURRENCY: '币种',
    AMOUNT: '本期应还',
    MINIMUM: '最低还款',
    PERIOD_START: '账期开始',
    PERIOD_END: '账期结束',
    CREDIT_LIMIT: '额度',
  }
  return (
    known[s] ||
    (s.startsWith('MISSING_')
      ? `${fields[s.slice(8)] || s.slice(8)}缺失`
      : s.startsWith('EVIDENCE_')
        ? `${fields[s.slice(9)] || s.slice(9)}未找到有效原文引用`
        : s)
  )
}
