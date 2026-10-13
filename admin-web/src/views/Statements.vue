<script setup lang="ts">
import { computed, ref, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Search, ArrowDown, ArrowUp } from '@element-plus/icons-vue'
import { detailState } from '../model-types'
import { api, allItems, ApiError } from '../api'
import { amountText, dateTime, stateText, type Page, type Account, type Statement } from '../types'
const route = useRoute(),
  rows = ref<Statement[]>([]),
  accounts = ref<Account[]>([]),
  loading = ref(false),
  saving = ref(false),
  total = ref(0),
  page = ref(1)
const filterAccount = ref(String(route.query.account_id || '')),
  filterBank = ref(''),
  filterHolder = ref(''),
  keyword = ref(''),
  appliedKeyword = ref(''),
  filterCurrency = ref(''),
  dateField = ref<'statement_date' | 'due_date'>('statement_date'),
  dateRange = ref<[string, string] | null>(null),
  moreFilters = ref(false),
  filterStatus = ref(String(route.query.status || '')),
  detail = ref<Statement | null>(null),
  drawer = ref(false)
const editor = ref(false),
  editing = ref(''),
  form = ref<Record<string, any>>({}),
  paymentOpen = ref(false),
  isPlan = ref(false),
  payment = ref<Record<string, any>>({}),
  paymentKey = ref('')
const banks = computed(() => [
  ...new Map(accounts.value.map((a) => [a.bank_id, { id: a.bank_id, name: a.bank_name }])).values(),
])
const holders = computed(() => [
  ...new Map(
    accounts.value.map((a) => [a.cardholder_id, { id: a.cardholder_id, name: a.cardholder_name }]),
  ).values(),
])
const availableAccounts = computed(() =>
  accounts.value.filter(
    (a) =>
      (!filterBank.value || a.bank_id === filterBank.value) &&
      (!filterHolder.value || a.cardholder_id === filterHolder.value),
  ),
)
function cardLabel(account: Account) {
  const cards = account.cards.map((c) => `${c.name} · 尾号 ${c.last_four}`).join(' / ')
  return `${cards || account.name + '（暂未添加卡片）'} · ${account.bank_name} · ${account.cardholder_name}`
}
const activeFilterCount = computed(
  () =>
    [
      appliedKeyword.value,
      filterBank.value,
      filterHolder.value,
      filterAccount.value,
      filterStatus.value,
      filterCurrency.value,
      dateRange.value?.length,
    ].filter(Boolean).length,
)
function changeIdentityFilter() {
  if (!availableAccounts.value.some((a) => a.id === filterAccount.value)) filterAccount.value = ''
  changeFilter()
}
function search() {
  appliedKeyword.value = keyword.value.trim()
  changeFilter()
}
function resetFilters() {
  for (const filter of [
    keyword,
    appliedKeyword,
    filterBank,
    filterHolder,
    filterAccount,
    filterStatus,
    filterCurrency,
  ]) {
    filter.value = ''
  }
  dateRange.value = null
  dateField.value = 'statement_date'
  changeFilter()
}
function changeFilter() {
  page.value = 1
  void load()
}
watch(
  () => route.query.account_id,
  (accountId) => {
    filterAccount.value = String(accountId || '')
    changeFilter()
  },
)
let loadSequence = 0
async function load() {
  const sequence = ++loadSequence
  loading.value = true
  try {
    const params = new URLSearchParams({ page: String(page.value), page_size: '20' })
    if (filterAccount.value) params.set('account_id', filterAccount.value)
    if (filterBank.value) params.set('bank_id', filterBank.value)
    if (filterHolder.value) params.set('cardholder_id', filterHolder.value)
    if (appliedKeyword.value) params.set('keyword', appliedKeyword.value)
    if (filterStatus.value) {
      if (['draft', 'confirmed', 'void'].includes(filterStatus.value))
        params.set('status', filterStatus.value)
      else params.set('payment_state', filterStatus.value)
    }
    if (filterCurrency.value) params.set('currency', filterCurrency.value)
    if (dateRange.value?.length === 2) {
      params.set('date_field', dateField.value)
      params.set('date_from', dateRange.value[0])
      params.set('date_to', dateRange.value[1])
    }
    const data = await api<Page<Statement>>('/admin/statements?' + params)
    if (sequence !== loadSequence) return
    rows.value = data.items
    total.value = data.total
  } catch (e) {
    if (sequence === loadSequence) ElMessage.error((e as Error).message)
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}
async function show(id: string) {
  try {
    detail.value = await api<Statement>('/admin/statements/' + id)
    drawer.value = true
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}
function create() {
  editing.value = ''
  form.value = {
    account_id: filterAccount.value,
    statement_date: '',
    currency: 'CNY',
    amount: '',
    minimum: '',
    due_date: '',
    period_start: '',
    period_end: '',
    reason: '人工录入',
  }
  editor.value = true
}
function edit() {
  if (!detail.value) return
  editing.value = detail.value.id
  form.value = { ...detail.value, reason: '' }
  editor.value = true
}
async function save() {
  const f = form.value
  const body: Record<string, unknown> = {
    amount: f.amount === '' ? null : f.amount,
    minimum: f.minimum === '' ? null : f.minimum,
    due_date: f.due_date || null,
    period_start: f.period_start || null,
    period_end: f.period_end || null,
    reason: f.reason,
  }
  if (editing.value) body.expected_version = f.version
  else {
    body.account_id = f.account_id
    body.statement_date = f.statement_date
    body.currency = f.currency
  }
  saving.value = true
  try {
    const row = await api<Statement>(
      '/admin/statements' + (editing.value ? '/' + editing.value : ''),
      editing.value ? 'PATCH' : 'POST',
      body,
    )
    editor.value = false
    ElMessage.success(editing.value ? '修订已保存' : '已保存草稿，请核对后确认')
    await load()
    await show(row.id)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    saving.value = false
  }
}
async function action(kind: 'confirm' | 'void') {
  if (!detail.value) return
  try {
    const { value } = await ElMessageBox.prompt(
      kind === 'confirm'
        ? '核对金额、币种和到期日后确认。该操作不代表银行到账。'
        : '作废账单将保留历史，关联计划会取消。',
      kind === 'confirm' ? '确认账单' : '作废账单',
      { inputPlaceholder: '填写原因', inputValidator: (v) => !!v?.trim() || '请填写原因' },
    )
    await api(`/admin/statements/${detail.value.id}/${kind}`, 'POST', {
      expected_version: detail.value.version,
      reason: value,
    })
    await show(detail.value.id)
    await load()
    ElMessage.success('已更新')
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error((e as Error).message)
  }
}
function openPayment(planned = false) {
  isPlan.value = planned
  paymentKey.value = crypto.randomUUID()
  payment.value = {
    amount: detail.value?.remaining || '',
    occurred_at: new Date(),
    note: '',
    confirm_exception: false,
  }
  paymentOpen.value = true
}
async function savePayment() {
  if (!detail.value || !(payment.value.occurred_at instanceof Date)) {
    ElMessage.warning('请选择时间')
    return
  }
  const body = {
    amount: payment.value.amount,
    currency: detail.value.currency,
    occurred_at: payment.value.occurred_at.toISOString(),
    note: payment.value.note,
    confirm_exception: payment.value.confirm_exception,
  }
  saving.value = true
  try {
    await api(
      `/admin/statements/${detail.value.id}/${isPlan.value ? 'plans' : 'repayments'}`,
      'POST',
      body,
      { 'Idempotency-Key': paymentKey.value },
    )
    paymentOpen.value = false
    await show(detail.value.id)
    await load()
    ElMessage.success(
      isPlan.value ? '计划已保存；本阶段尚未启用提醒投递' : '已标记还款，不代表银行到账确认',
    )
  } catch (e) {
    if (e instanceof ApiError && e.code === 'CONFIRM_EXCEPTION') {
      try {
        await ElMessageBox.confirm(e.message, '请再次核对', {
          type: 'warning',
          confirmButtonText: '确认并继续',
        })
        payment.value.confirm_exception = true
        await savePayment()
      } catch {}
    } else ElMessage.error((e as Error).message)
  } finally {
    saving.value = false
  }
}
async function cancelRecord(id: string, plan = false) {
  try {
    const { value } = await ElMessageBox.prompt(
      '该操作保留历史，请说明原因。',
      plan ? '取消计划' : '撤销还款标记',
      { inputValidator: (v) => !!v?.trim() || '请填写原因' },
    )
    await api(
      `/admin/${plan ? 'plans' : 'repayments'}/${id}/${plan ? 'cancel' : 'revoke'}`,
      'POST',
      { reason: value },
    )
    if (detail.value) await show(detail.value.id)
    await load()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error((e as Error).message)
  }
}
onMounted(async () => {
  try {
    accounts.value = await allItems<Account>('/admin/accounts')
    await load()
    if (route.query.detail) await show(String(route.query.detail))
    if (route.query.new) create()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
})
</script>
<template>
  <div class="page-heading">
    <div>
      <div class="eyebrow">STATEMENT ARCHIVE</div>
      <h1>每期账单，清楚留档。</h1>
      <p>核对后确认，分次记录还款；所有修订都有迹可循。</p>
    </div>
    <div class="heading-actions">
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button
      ><el-button type="primary" :icon="Plus" @click="create">录入账单</el-button>
    </div>
  </div>
  <section class="panel padded">
    <div class="statement-filters" aria-label="账单筛选">
      <div class="statement-filter-grid">
        <div class="filter-field filter-search">
          <span>搜索账单</span>
          <el-input
            v-model="keyword"
            :maxlength="100"
            aria-label="搜索账单"
            clearable
            placeholder="卡名、尾号、银行或持卡人"
            :prefix-icon="Search"
            @keyup.enter="search"
            @clear="search"
          >
            <template #append
              ><el-button aria-label="搜索" :icon="Search" @click="search"
            /></template>
          </el-input>
        </div>
        <div class="filter-field">
          <span>银行</span>
          <el-select
            v-model="filterBank"
            aria-label="银行筛选"
            clearable
            filterable
            placeholder="全部银行"
            @change="changeIdentityFilter"
          >
            <el-option v-for="bank in banks" :key="bank.id" :label="bank.name" :value="bank.id" />
          </el-select>
        </div>
        <div class="filter-field">
          <span>持卡人</span>
          <el-select
            v-model="filterHolder"
            aria-label="持卡人筛选"
            clearable
            filterable
            placeholder="全部持卡人"
            @change="changeIdentityFilter"
          >
            <el-option
              v-for="holder in holders"
              :key="holder.id"
              :label="holder.name"
              :value="holder.id"
            />
          </el-select>
        </div>
        <div class="filter-field filter-card">
          <span>卡片 / 尾号</span>
          <el-select
            v-model="filterAccount"
            aria-label="卡片筛选"
            :fit-input-width="true"
            popper-class="statement-card-options"
            clearable
            filterable
            placeholder="全部卡片"
            @change="changeFilter"
          >
            <el-option
              v-for="account in availableAccounts"
              :key="account.id"
              :value="account.id"
              :label="cardLabel(account)"
              :aria-label="cardLabel(account)"
            >
              <span class="statement-card-option-name">{{
                account.cards.map((c) => `${c.name} · 尾号 ${c.last_four}`).join(' / ') ||
                account.name + '（暂未添加卡片）'
              }}</span>
              <span class="statement-card-option-meta"
                >{{ account.bank_name }} · {{ account.cardholder_name
                }}{{ account.mode === 'merged' ? ' · 合并还款' : '' }}</span
              >
            </el-option>
          </el-select>
        </div>
        <div class="filter-field">
          <span>账单状态</span>
          <el-select
            v-model="filterStatus"
            aria-label="账单状态筛选"
            clearable
            placeholder="全部状态"
            @change="changeFilter"
          >
            <el-option label="待还款" value="unpaid" />
            <el-option label="部分还款" value="partial" />
            <el-option label="已标记结清" value="settled" />
            <el-option label="已过到期日" value="overdue" />
            <el-option label="超额标记" value="overpaid" />
            <el-option label="待确认" value="draft" />
            <el-option label="已确认（全部还款状态）" value="confirmed" />
            <el-option label="已作废" value="void" />
          </el-select>
        </div>
      </div>
      <div v-if="moreFilters" id="statement-more-filters" class="statement-advanced-filters">
        <div class="filter-field filter-currency">
          <span>币种</span>
          <el-select
            v-model="filterCurrency"
            aria-label="币种筛选"
            clearable
            placeholder="全部币种"
            @change="changeFilter"
          >
            <el-option label="人民币 CNY" value="CNY" /><el-option label="美元 USD" value="USD" />
            <el-option label="欧元 EUR" value="EUR" /><el-option label="港币 HKD" value="HKD" />
            <el-option label="日元 JPY" value="JPY" />
          </el-select>
        </div>
        <div class="filter-field filter-date">
          <span id="statement-date-label">日期范围</span>
          <div class="filter-date-controls">
            <el-select v-model="dateField" aria-label="日期类型" @change="changeFilter">
              <el-option label="账单日" value="statement_date" /><el-option
                label="到期日"
                value="due_date"
              />
            </el-select>
            <el-date-picker
              v-model="dateRange"
              aria-labelledby="statement-date-label"
              type="daterange"
              value-format="YYYY-MM-DD"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              range-separator="至"
              @change="changeFilter"
            />
          </div>
        </div>
      </div>
      <div class="statement-filter-footer">
        <div class="statement-filter-actions">
          <el-button
            text
            type="primary"
            :icon="moreFilters ? ArrowUp : ArrowDown"
            :aria-expanded="moreFilters"
            aria-controls="statement-more-filters"
            @click="moreFilters = !moreFilters"
          >
            {{ moreFilters ? '收起筛选' : '更多筛选' }}
          </el-button>
          <el-button v-if="activeFilterCount || keyword" text @click="resetFilters"
            >重置筛选</el-button
          >
          <span v-if="activeFilterCount" class="filter-count"
            >已选 {{ activeFilterCount }} 项条件</span
          >
        </div>
        <span class="muted" aria-live="polite"
          >{{ activeFilterCount ? '筛选结果' : '共' }} <strong>{{ total }}</strong> 份账单</span
        >
      </div>
      <p
        v-if="filterAccount && accounts.find((a) => a.id === filterAccount)?.mode === 'merged'"
        class="filter-note"
      >
        该卡片属于合并还款账户，展示整个账户的账单，不按卡片拆分金额。
      </p>
    </div>
    <el-table
      :data="rows"
      v-loading="loading"
      :empty-text="
        activeFilterCount
          ? '没有符合条件的账单，试试调整或重置筛选。'
          : '没有账单，点击“录入账单”创建第一份。'
      "
      ><el-table-column label="卡片 / 尾号 / 银行 / 持卡人" min-width="260"
        ><template #default="{ row }"
          ><div class="statement-identity">
            <div v-for="card in row.account.cards" :key="card.id" class="statement-card">
              <span class="statement-card-name">{{ card.name }}</span>
              <span class="statement-card-tail">· 尾号 {{ card.last_four }}</span>
            </div>
            <div v-if="!row.account.cards.length" class="table-sub">暂未添加卡片</div>
            <div class="statement-account">
              {{ row.account.bank_name }} · {{ row.account.cardholder_name }}
            </div>
          </div></template
        ></el-table-column
      ><el-table-column prop="statement_date" label="账单日" min-width="110" /><el-table-column
        label="本期应还"
        min-width="130"
        align="right"
        ><template #default="{ row }"
          ><span class="numeric">{{ amountText(row.amount, row.currency) }}</span></template
        ></el-table-column
      ><el-table-column label="记录待还" min-width="130" align="right"
        ><template #default="{ row }"
          ><strong class="numeric">{{ amountText(row.remaining, row.currency) }}</strong></template
        ></el-table-column
      ><el-table-column prop="due_date" label="到期日" min-width="110" /><el-table-column
        label="状态"
        min-width="120"
        ><template #default="{ row }"
          ><el-tag
            :type="
              row.status === 'draft'
                ? 'warning'
                : row.payment_status === 'settled'
                  ? 'success'
                  : row.overdue
                    ? 'danger'
                    : 'info'
            "
            >{{ stateText(row) }}</el-tag
          ></template
        ></el-table-column
      ><el-table-column label="操作" width="80" fixed="right"
        ><template #default="{ row }"
          ><el-button text type="primary" @click="show(row.id)">详情</el-button></template
        ></el-table-column
      ></el-table
    ><el-pagination
      class="pagination"
      v-model:current-page="page"
      :page-size="20"
      :total="total"
      layout="prev, pager, next"
      @current-change="load"
    />
  </section>
  <p class="fine-print">金额未知不等于零。计划还款不减少待还，最低还款不等于全额结清。</p>
  <el-drawer v-model="drawer" title="账单档案" size="min(700px, 100vw)"
    ><template v-if="detail"
      ><div class="detail-heading">
        <div class="bank-badge" :class="detail.account.bank_code">
          {{ detail.account.bank_name.slice(0, 1) }}
        </div>
        <div>
          <h2>{{ detail.account.bank_name }} · {{ detail.account.cardholder_name }}</h2>
          <p>{{ detail.account.name }} · {{ detail.statement_date }} · v{{ detail.version }}</p>
        </div>
        <el-tag
          :type="
            detail.status === 'confirmed' && detail.payment_status === 'settled'
              ? 'success'
              : 'primary'
          "
          >{{ stateText(detail) }}</el-tag
        >
      </div>
      <div class="detail-summary">
        <span>本期应还</span><strong>{{ amountText(detail.amount, detail.currency) }}</strong>
        <div>
          <span>已标记 {{ amountText(detail.paid, detail.currency) }}</span
          ><span>记录待还 {{ amountText(detail.remaining, detail.currency) }}</span>
        </div>
      </div>
      <el-descriptions :column="2" border
        ><el-descriptions-item label="到期日">{{ detail.due_date || '未知' }}</el-descriptions-item
        ><el-descriptions-item label="最低还款">{{
          amountText(detail.minimum, detail.currency)
        }}</el-descriptions-item
        ><el-descriptions-item label="账期"
          >{{ detail.period_start || '未知' }} ～
          {{ detail.period_end || '未知' }}</el-descriptions-item
        ><el-descriptions-item label="来源">{{
          detail.source_kind === 'model' ? '模型识别 / 已核对' : '人工录入'
        }}</el-descriptions-item></el-descriptions
      >
      <div class="detail-actions">
        <el-button v-if="detail.status === 'draft'" type="primary" @click="action('confirm')"
          >确认账单</el-button
        ><el-button v-if="detail.status === 'confirmed'" type="primary" @click="openPayment()"
          >记录还款</el-button
        ><el-button v-if="detail.status === 'confirmed'" @click="openPayment(true)"
          >计划还款</el-button
        ><el-button v-if="detail.status !== 'void'" @click="edit">修订账单</el-button
        ><el-button v-if="detail.status !== 'void'" text type="danger" @click="action('void')"
          >作废</el-button
        >
      </div>
      <el-alert
        v-if="Number(detail.excess) > 0"
        title="存在超额还款标记，不会自动抵扣其他账期。"
        type="warning"
        :closable="false"
      />
      <el-tabs
        ><el-tab-pane label="还款记录"
          ><el-empty
            v-if="!detail.repayments?.length"
            description="尚无还款记录"
            :image-size="60"
          />
          <div
            v-for="p in detail.repayments"
            :key="p.id"
            class="record-row"
            :class="{ inactive: !!p.revoked_at }"
          >
            <div>
              <strong>{{ amountText(p.amount, p.currency) }}</strong
              ><small>{{ dateTime(p.occurred_at) }} · {{ p.note || '用户手动标记' }}</small
              ><small v-if="p.revoked_at">已撤销：{{ p.revoke_reason }}</small>
            </div>
            <el-button v-if="!p.revoked_at" text type="danger" @click="cancelRecord(p.id)"
              >撤销</el-button
            >
          </div></el-tab-pane
        ><el-tab-pane label="计划还款"
          ><el-alert
            title="计划仅记录，不减少待还。本阶段尚未启用通知投递。"
            type="info"
            :closable="false"
          /><el-empty v-if="!detail.plans?.length" description="尚无计划" :image-size="60" />
          <div
            v-for="p in detail.plans"
            :key="p.id"
            class="record-row"
            :class="{ inactive: !!p.cancelled_at }"
          >
            <div>
              <strong>{{ amountText(p.amount, p.currency) }}</strong
              ><small
                >{{ dateTime(p.occurred_at) }} ·
                {{ p.cancelled_at ? '已取消' : p.note || '计划还款' }}</small
              >
            </div>
            <el-button v-if="!p.cancelled_at" text @click="cancelRecord(p.id, true)"
              >取消计划</el-button
            >
          </div></el-tab-pane
        ><el-tab-pane label="交易详单"
          ><el-tag>{{ detailState(detail.transaction_status) }}</el-tag>
          <p class="muted">
            未提供或部分详单不等于零消费；交易方向及金额来自核对结果，不代表银行资金到账。
          </p>
          <el-table :data="detail.transactions || []" empty-text="当前版本未提供交易明细"
            ><el-table-column
              prop="transaction_date"
              label="交易日"
              min-width="110"
            /><el-table-column prop="description" label="描述" min-width="150" /><el-table-column
              label="方向"
              min-width="100"
              ><template #default="{ row }">{{
                row.direction === 'debit'
                  ? '支出'
                  : row.direction === 'credit'
                    ? '入账 / 退款'
                    : '未知'
              }}</template></el-table-column
            ><el-table-column label="金额" min-width="135"
              ><template #default="{ row }">{{
                amountText(row.amount, row.currency || 'CNY')
              }}</template></el-table-column
            ></el-table
          ><el-collapse class="section-gap"
            ><el-collapse-item
              v-for="h in detail.detail_history"
              :key="h.version"
              :title="`详单历史 v${h.version} · ${detailState(h.status)} · ${h.transactions.length} 笔`"
              ><div v-for="t in h.transactions" :key="t.index" class="record-row">
                <div>
                  <strong>{{ t.description }}</strong
                  ><small
                    >{{ t.transaction_date || t.posted_date || '日期未知' }} ·
                    {{ amountText(t.amount, t.currency || 'CNY') }} · {{ t.direction }}</small
                  >
                </div>
              </div></el-collapse-item
            ></el-collapse
          ></el-tab-pane
        ><el-tab-pane label="修订历史"
          ><div v-for="r in detail.revisions" :key="r.version" class="record-row">
            <div>
              <strong>v{{ r.version }} · {{ amountText(r.amount, detail.currency) }}</strong
              ><small>{{ r.reason }} · {{ dateTime(r.created_at) }}</small>
            </div>
            <span>{{ r.due_date }}</span>
          </div></el-tab-pane
        ><el-tab-pane label="交易明细"
          ><el-empty description="该人工账单未提供交易明细。" :image-size="70" /></el-tab-pane
      ></el-tabs>
      <p class="fine-print">仅记录用户操作，不执行转账，也不确认银行实际到账。</p></template
    ></el-drawer
  >
  <el-dialog
    v-model="editor"
    :title="editing ? '修订账单' : '录入账单（草稿）'"
    width="min(580px,94vw)"
    ><el-form label-position="top"
      ><el-form-item v-if="!editing" label="还款账户" required
        ><el-select v-model="form.account_id" filterable
          ><el-option
            v-for="a in accounts.filter((a) => a.effective_active)"
            :key="a.id"
            :value="a.id"
            :label="`${a.bank_name} · ${a.cardholder_name} · ${a.name}`" /></el-select
      ></el-form-item>
      <div v-if="!editing" class="form-grid">
        <el-form-item label="账单日期" required
          ><el-date-picker
            v-model="form.statement_date"
            type="date"
            value-format="YYYY-MM-DD" /></el-form-item
        ><el-form-item label="币种"
          ><el-select v-model="form.currency"
            ><el-option
              v-for="c in ['CNY', 'USD', 'EUR', 'HKD', 'JPY']"
              :key="c"
              :value="c"
              :label="c" /></el-select
        ></el-form-item>
      </div>
      <div class="form-grid">
        <el-form-item label="本期应还（留空代表未知）"
          ><el-input v-model="form.amount" placeholder="例如 8000.00" /></el-form-item
        ><el-form-item label="最低还款（可选）"><el-input v-model="form.minimum" /></el-form-item>
      </div>
      <el-form-item label="到期日"
        ><el-date-picker v-model="form.due_date" type="date" value-format="YYYY-MM-DD"
      /></el-form-item>
      <div class="form-grid">
        <el-form-item label="账期开始（可选）"
          ><el-date-picker
            v-model="form.period_start"
            type="date"
            value-format="YYYY-MM-DD" /></el-form-item
        ><el-form-item label="账期结束（可选）"
          ><el-date-picker v-model="form.period_end" type="date" value-format="YYYY-MM-DD"
        /></el-form-item>
      </div>
      <el-form-item label="录入 / 修订原因" required
        ><el-input v-model="form.reason" type="textarea" maxlength="500" /></el-form-item></el-form
    ><template #footer
      ><el-button @click="editor = false">取消</el-button
      ><el-button type="primary" :loading="saving" @click="save">保存</el-button></template
    ></el-dialog
  >
  <el-dialog
    v-model="paymentOpen"
    :title="isPlan ? '计划还款' : '记录已还款'"
    width="min(480px,94vw)"
    ><el-alert
      :title="isPlan ? '只记录计划，不减少待还。' : '仅标记你已操作的还款，不执行支付。'"
      type="info"
      :closable="false"
    /><el-form label-position="top" class="payment-form"
      ><el-form-item :label="`金额（${detail?.currency}）`" required
        ><el-input v-model="payment.amount" /></el-form-item
      ><el-form-item :label="isPlan ? '计划时间' : '实际还款时间'" required
        ><el-date-picker v-model="payment.occurred_at" type="datetime" /></el-form-item
      ><small class="form-hint">按当前设备当地时间输入；列表统一按上海时区显示。</small
      ><el-form-item label="备注"
        ><el-input v-model="payment.note" maxlength="500" /></el-form-item></el-form
    ><template #footer
      ><el-button @click="paymentOpen = false">取消</el-button
      ><el-button type="primary" :loading="saving" @click="savePayment">{{
        isPlan ? '保存计划' : '标记已还款'
      }}</el-button></template
    ></el-dialog
  >
</template>

<style scoped>
:global(.statement-card-options .el-select-dropdown__item) {
  height: auto;
  min-height: 34px;
  padding-block: 9px;
  line-height: 1.5;
  white-space: normal;
}
:global(.statement-card-option-name) {
  display: block;
  overflow-wrap: anywhere;
}
:global(.statement-card-option-meta) {
  display: block;
  margin-top: 3px;
  font-size: 12px;
  color: #7b857d;
  font-weight: 400;
}

.statement-filters {
  padding: 20px 0 14px;
}
.statement-filter-grid {
  display: grid;
  grid-template-columns:
    minmax(230px, 1.4fr) minmax(125px, 0.8fr) minmax(125px, 0.8fr) minmax(210px, 1.3fr)
    minmax(150px, 0.9fr);
  gap: 14px;
}
.filter-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.filter-field > span {
  color: #7b857d;
  font-size: 12px;
  font-weight: 500;
}
.filter-field .el-select {
  width: 100%;
}
.statement-advanced-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px dashed var(--el-border-color);
}
.filter-currency {
  width: 180px;
}
.filter-date {
  flex: 1;
  max-width: 560px;
}
.filter-date-controls {
  display: flex;
  gap: 10px;
  min-width: 0;
}
.filter-date-controls > .el-select {
  width: 110px;
  flex-shrink: 0;
}
.filter-date-controls :deep(.el-date-editor) {
  min-width: 0;
  width: 100%;
}
.statement-filter-footer,
.statement-filter-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.statement-filter-footer {
  justify-content: space-between;
  margin-top: 14px;
}
.statement-filter-actions .el-button {
  margin-left: 0;
  padding-inline: 0;
}
.filter-count,
.filter-note {
  color: #7b857d;
  font-size: 12px;
}
.filter-note {
  margin: 8px 0 0;
}
@media (max-width: 1200px) {
  .statement-filter-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .filter-search {
    grid-column: span 2;
  }
}
@media (max-width: 640px) {
  .statement-filter-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px 10px;
  }
  .filter-search {
    grid-column: 1 / -1;
  }
  .filter-card,
  .statement-filter-grid > .filter-field:last-child {
    grid-column: 1 / -1;
  }
  .filter-currency,
  .filter-date {
    width: 100%;
    max-width: none;
    flex: auto;
  }
  .filter-date-controls {
    flex-direction: column;
  }
  .filter-date-controls > .el-select {
    width: 100%;
  }
  .statement-filter-footer {
    align-items: flex-start;
  }
}

.statement-identity {
  padding: 2px 0;
}
.statement-account {
  margin-top: 4px;
  color: #7b857d;
  font-size: 12px;
  font-weight: 400;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.statement-card {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0 6px;
  color: var(--el-text-color-primary);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
}
.statement-card + .statement-card {
  margin-top: 3px;
}
.statement-card-name {
  min-width: 0;
  overflow-wrap: anywhere;
}
.statement-card-tail {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
</style>
