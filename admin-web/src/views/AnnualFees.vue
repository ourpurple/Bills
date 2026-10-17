<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Calendar, Refresh, Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { allItems, api } from '../api'
import type { Account, Card } from '../types'
import { nextAnnualFee } from '../utils/annual-fees'

const accounts = ref<Account[]>([])
const loading = ref(false)
const loadError = ref('')
const today = ref(new Date())
const search = ref('')
const bank = ref('')
const holder = ref('')
const state = ref('all')
const schedule = ref('all')
const rows = computed(() =>
  accounts.value.flatMap((account) =>
    account.cards.map((card) => ({
      card,
      account,
      active: card.active && account.effective_active,
      next: nextAnnualFee(card.annual_fee_month, card.annual_fee_day, today.value),
    })),
  ),
)
type FeeRow = (typeof rows.value)[number]
const banks = computed(() =>
  [...new Map(accounts.value.map((a) => [a.bank_id, a.bank_name])).entries()].sort((a, b) =>
    a[1].localeCompare(b[1], 'zh-CN'),
  ),
)
const holders = computed(() =>
  [...new Map(accounts.value.map((a) => [a.cardholder_id, a.cardholder_name])).entries()].sort(
    (a, b) => a[1].localeCompare(b[1], 'zh-CN'),
  ),
)
const soon = (row: FeeRow) => row.active && row.next !== null && row.next.days <= 30
const totals = computed(() => ({
  total: rows.value.length,
  soon: rows.value.filter(soon).length,
  missing: rows.value.filter((r) => r.active && !r.next).length,
  inactive: rows.value.filter((r) => !r.active).length,
}))
const filtered = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  return rows.value
    .filter((r) => {
      const text = [
        r.card.name,
        r.card.last_four,
        r.account.bank_name,
        r.account.cardholder_name,
        r.account.name,
        r.card.annual_fee_benefits ?? '',
      ]
        .join(' ')
        .toLocaleLowerCase()
      return (
        (!query || text.includes(query)) &&
        (!bank.value || r.account.bank_id === bank.value) &&
        (!holder.value || r.account.cardholder_id === holder.value) &&
        (state.value === 'all' || (state.value === 'active' ? r.active : !r.active)) &&
        (schedule.value === 'all' ||
          (schedule.value === 'soon' ? soon(r) : schedule.value === 'missing' ? !r.next : !!r.next))
      )
    })
    .sort(
      (a, b) =>
        Number(b.active) - Number(a.active) ||
        (a.next?.days ?? Infinity) - (b.next?.days ?? Infinity) ||
        a.card.name.localeCompare(b.card.name, 'zh-CN') ||
        a.card.id.localeCompare(b.card.id),
    )
})
function resetFilters() {
  search.value = bank.value = holder.value = ''
  state.value = schedule.value = 'all'
}
async function load() {
  loading.value = true
  loadError.value = ''
  today.value = new Date()
  try {
    accounts.value = await allItems<Account>('/admin/accounts')
  } catch (e) {
    loadError.value = (e as Error).message
  } finally {
    loading.value = false
  }
}
const editing = ref<FeeRow | null>(null)
const dialog = ref(false)
const saving = ref(false)
const form = ref<{ month: number | null; day: number | null; benefits: string }>({
  month: null,
  day: null,
  benefits: '',
})
const daysInMonth = computed(() =>
  form.value.month ? new Date(2000, form.value.month, 0).getDate() : 31,
)
watch(
  () => form.value.month,
  () => {
    if (!form.value.month || (form.value.day && form.value.day > daysInMonth.value))
      form.value.day = null
  },
)
function open(row: FeeRow) {
  editing.value = row
  form.value = {
    month: row.card.annual_fee_month,
    day: row.card.annual_fee_day,
    benefits: row.card.annual_fee_benefits ?? '',
  }
  dialog.value = true
}
async function save() {
  if (!editing.value || saving.value) return
  if (!!form.value.month !== !!form.value.day) {
    ElMessage.warning('请完整选择收取月份和日期，或清空月份以取消设置')
    return
  }
  saving.value = true
  try {
    const saved = await api<Card>(`/admin/cards/${editing.value.card.id}/annual-fee`, 'PATCH', {
      annual_fee_month: form.value.month || null,
      annual_fee_day: form.value.day || null,
      annual_fee_benefits: form.value.benefits.trim() || null,
    })
    for (const account of accounts.value) {
      const index = account.cards.findIndex((c) => c.id === saved.id)
      if (index !== -1) account.cards[index] = saved
    }
    dialog.value = false
    ElMessage.success('年费资料已保存')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    saving.value = false
  }
}
let clock: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  void load()
  clock = setInterval(() => {
    today.value = new Date()
  }, 60_000)
})
onUnmounted(() => clearInterval(clock))
</script>

<template>
  <div class="annual-fees-page">
    <div class="page-heading">
      <div>
        <div class="eyebrow">ANNUAL FEE PLANNER</div>
        <h1>年费有期，优惠有据。</h1>
        <p>集中管理每张卡片的收取日期与优惠条件，提前安排，不遗漏。</p>
      </div>
      <el-button :icon="Refresh" :loading="loading" :disabled="saving" @click="load"
        >刷新</el-button
      >
    </div>
    <el-alert
      v-if="loadError"
      :title="loadError"
      type="error"
      show-icon
      :closable="false"
      class="fee-load-error"
    >
      <el-button text type="primary" @click="load">重新加载</el-button>
    </el-alert>
    <div class="fee-summary" aria-label="年费概况" :aria-busy="loading">
      <section class="panel fee-stat">
        <span>全部卡片</span><strong>{{ totals.total }}<small>张</small></strong>
        <p>包含停用卡片与上级停用卡片</p>
      </section>
      <section class="panel fee-stat fee-stat-soon">
        <span>未来 30 天需关注</span><strong>{{ totals.soon }}<small>张</small></strong>
        <p>仅统计有效卡片，含今天</p>
      </section>
      <section class="panel fee-stat">
        <span>待完善收取日期</span><strong>{{ totals.missing }}<small>张</small></strong>
        <p>尚未设置日期的有效卡片</p>
      </section>
      <section class="panel fee-stat">
        <span>已停用</span><strong>{{ totals.inactive }}<small>张</small></strong>
        <p>保留资料，不计入近期提醒</p>
      </section>
    </div>
    <section class="panel fee-panel" v-loading="loading" aria-label="卡片年费列表">
      <div class="fee-filters">
        <div class="fee-search">
          <label for="annual-fee-search">搜索卡片</label
          ><el-input
            id="annual-fee-search"
            v-model="search"
            :prefix-icon="Search"
            placeholder="卡名、尾号、银行、持卡人或优惠"
            clearable
          />
        </div>
        <div>
          <label id="fee-bank-label">银行</label
          ><el-select v-model="bank" aria-label="银行" placeholder="全部银行" clearable
            ><el-option v-for="[id, name] in banks" :key="id" :label="name" :value="id"
          /></el-select>
        </div>
        <div>
          <label id="fee-holder-label">持卡人</label
          ><el-select v-model="holder" aria-label="持卡人" placeholder="全部持卡人" clearable
            ><el-option v-for="[id, name] in holders" :key="id" :label="name" :value="id"
          /></el-select>
        </div>
        <div>
          <label id="fee-state-label">卡片状态</label
          ><el-select v-model="state" aria-label="卡片状态"
            ><el-option label="全部状态" value="all" /><el-option
              label="有效卡片"
              value="active" /><el-option label="已停用（含上级）" value="inactive"
          /></el-select>
        </div>
        <div>
          <label id="fee-schedule-label">年费日期</label
          ><el-select v-model="schedule" aria-label="年费日期"
            ><el-option label="全部日期" value="all" /><el-option
              label="未来 30 天"
              value="soon" /><el-option label="未设置日期" value="missing" /><el-option
              label="已设置日期"
              value="configured"
          /></el-select>
        </div>
      </div>
      <div class="fee-list-toolbar">
        <span
          >共 <strong>{{ filtered.length }}</strong> 张卡片<span
            v-if="filtered.length !== totals.total"
          >
            / 全部 {{ totals.total }} 张</span
          ></span
        ><el-button text @click="resetFilters">重置筛选</el-button>
      </div>
      <div class="fee-list-heading" aria-hidden="true">
        <span>卡片 / 银行 / 持卡人</span><span>年费收取日期</span><span>下次预计收取</span
        ><span>年费优惠信息</span><span>操作</span>
      </div>
      <div class="fee-rows">
        <article
          v-for="row in filtered"
          :key="row.card.id"
          class="fee-row"
          :class="{ 'fee-row-inactive': !row.active, 'fee-row-soon': soon(row) }"
          :aria-label="`${row.card.name} · ${row.card.last_four}`"
        >
          <div class="fee-identity">
            <div class="fee-card-title" :class="{ 'is-inactive': !row.active }">
              <strong>{{ row.card.name }} · {{ row.card.last_four }}</strong
              ><el-tag v-if="!row.active" type="danger" size="small">{{
                row.card.active ? '上级已停用' : '已停用'
              }}</el-tag>
            </div>
            <p>{{ row.account.bank_name }} · {{ row.account.cardholder_name }}</p>
          </div>
          <div class="fee-recurring">
            <span class="fee-mobile-label">年费收取日期</span
            ><span v-if="row.next"
              >每年 {{ row.card.annual_fee_month }} 月 {{ row.card.annual_fee_day }} 日</span
            ><span v-else class="fee-muted">未设置</span>
          </div>
          <div class="fee-next">
            <span class="fee-mobile-label">下次预计收取</span
            ><strong v-if="row.next">{{ row.next.date }}</strong
            ><span v-else class="fee-muted">待完善日期</span
            ><el-tag
              v-if="row.next && row.active"
              :type="soon(row) ? 'warning' : 'info'"
              size="small"
              >{{ row.next.days === 0 ? '今天' : `${row.next.days} 天后` }}</el-tag
            ><span v-if="!row.active" class="fee-muted">不计入提醒</span
            ><small v-if="row.next?.adjusted" class="fee-muted">非闰年按 2 月 28 日估算</small>
          </div>
          <div class="fee-benefits">
            <span class="fee-mobile-label">年费优惠信息</span>
            <p :class="{ 'fee-muted': !row.card.annual_fee_benefits }">
              {{ row.card.annual_fee_benefits || '未填写优惠信息' }}
            </p>
          </div>
          <div class="fee-actions">
            <el-button
              text
              type="primary"
              :disabled="loading"
              :aria-label="`管理年费 ${row.card.name} ${row.card.last_four}`"
              @click="open(row)"
              >管理</el-button
            >
          </div>
        </article>
      </div>
      <el-empty
        v-if="!loading && !loadError && !filtered.length"
        :description="
          totals.total ? '没有符合筛选条件的卡片' : '暂无卡片，请先在“账户与卡片”中添加'
        "
      />
      <div class="fee-footnote">
        <el-icon><Calendar /></el-icon>
        <p>
          按已录入的月、日估算下次收取日期，已过日期顺延至下一年。优惠条件请自行核实；本页不代表实际扣费或减免结果。
        </p>
      </div>
    </section>
    <el-dialog
      v-model="dialog"
      class="annual-fee-dialog"
      title="管理年费"
      width="min(520px, 94vw)"
      :close-on-click-modal="!saving"
      :close-on-press-escape="!saving"
      :show-close="!saving"
      :before-close="
        (done: () => void) => {
          if (!saving) done()
        }
      "
    >
      <template v-if="editing">
        <div class="fee-editor-identity">
          <strong>{{ editing.card.name }} · {{ editing.card.last_four }}</strong>
          <p>{{ editing.account.bank_name }} · {{ editing.account.cardholder_name }}</p>
        </div>
        <el-form label-position="top" @submit.prevent="save">
          <el-form-item label="年费收取日期（每年几月几日）"
            ><div class="fee-date-fields">
              <el-select
                v-model="form.month"
                aria-label="年费月份"
                placeholder="选择月份"
                clearable
                :disabled="saving"
                ><el-option
                  v-for="month in 12"
                  :key="month"
                  :label="`${month} 月`"
                  :value="month" /></el-select
              ><el-select
                v-model="form.day"
                aria-label="年费日期"
                placeholder="选择日期"
                clearable
                :disabled="!form.month || saving"
                ><el-option v-for="day in daysInMonth" :key="day" :label="`${day} 日`" :value="day"
              /></el-select>
            </div>
            <p class="fee-edit-hint">
              选填；清空月份可取消设置。2 月 29 日在非闰年按 2 月 28 日估算。
            </p></el-form-item
          >
          <el-form-item label="年费优惠信息" for="annual-fee-benefits"
            ><el-input
              id="annual-fee-benefits"
              v-model="form.benefits"
              type="textarea"
              :rows="5"
              maxlength="2000"
              show-word-limit
              :disabled="saving"
              placeholder="例如首年免年费，每年刷满 6 笔免次年年费"
          /></el-form-item>
        </el-form>
      </template>
      <template #footer
        ><el-button :disabled="saving" @click="dialog = false">取消</el-button
        ><el-button type="primary" :loading="saving" @click="save">保存</el-button></template
      >
    </el-dialog>
  </div>
</template>

<style scoped>
.fee-load-error {
  margin-bottom: 20px;
}
.fee-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}
.fee-stat {
  padding: 20px 22px;
}
.fee-stat > span {
  font-size: 13px;
  color: #637367;
}
.fee-stat > strong {
  display: block;
  margin: 12px 0 8px;
  font:
    32px/1.2 Georgia,
    'Songti SC',
    serif;
  color: #245745;
}
.fee-stat small {
  margin-left: 8px;
  font:
    12px 'Microsoft YaHei',
    sans-serif;
  color: #737e74;
}
.fee-stat p {
  font-size: 12px;
}
.fee-stat-soon {
  background: #f7f2e5;
  border-color: #e5d8b9;
}
.fee-stat-soon > strong {
  color: #92652b;
}
.fee-panel {
  padding: 24px;
  container-type: inline-size;
}
.fee-filters {
  display: grid;
  grid-template-columns: minmax(220px, 1.6fr) repeat(4, minmax(120px, 1fr));
  gap: 14px;
}
.fee-filters > div {
  min-width: 0;
}
.fee-filters label {
  display: block;
  color: #637367;
  font-size: 12px;
  margin-bottom: 8px;
}
.fee-filters .el-select {
  width: 100%;
}
.fee-list-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin: 18px 0 12px;
  font-size: 12px;
  color: #737e74;
}
.fee-list-toolbar strong {
  color: #245745;
}
.fee-list-heading,
.fee-row {
  display: grid;
  grid-template-columns:
    minmax(150px, 1.3fr) minmax(105px, 0.8fr) minmax(120px, 0.9fr) minmax(150px, 1.5fr)
    56px;
  gap: 20px;
  align-items: center;
}
.fee-list-heading {
  padding: 12px 16px;
  background: #f2f4e9;
  font-size: 12px;
  color: #6b7868;
}
.fee-row {
  padding: 20px 16px;
  border-bottom: 1px solid #e4e8dd;
}
.fee-row > div {
  min-width: 0;
}
.fee-row-soon {
  background: #fdfbf5;
  box-shadow: inset 3px 0 #c29b52;
}
.fee-card-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  line-height: 1.7;
}
.fee-card-title strong {
  overflow-wrap: anywhere;
  font-size: 14px;
}
.fee-identity p {
  font-size: 12px;
  margin-top: 6px;
  overflow-wrap: anywhere;
}
.is-inactive {
  color: #c45656;
}
.fee-row-inactive {
  background: #f8f8f4;
}
.fee-recurring {
  font-size: 13px;
}
.fee-next {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  font-size: 12px;
}
.fee-next strong {
  font-size: 14px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.fee-benefits p {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: #394d40;
  font-size: 13px;
  line-height: 1.8;
}
.fee-benefits .fee-muted,
.fee-muted {
  color: #7b847b;
  font-size: 12px;
}
.fee-actions {
  text-align: right;
}
.fee-mobile-label {
  display: none;
}
.fee-footnote {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding-top: 18px;
  color: #8b7958;
}
.fee-footnote .el-icon {
  flex-shrink: 0;
  margin-top: 3px;
}
.fee-footnote p {
  font-size: 12px;
}
.fee-editor-identity {
  background: #f2f6ef;
  border-left: 3px solid #245745;
  padding: 14px 16px;
  border-radius: 8px;
  margin-bottom: 22px;
  overflow-wrap: anywhere;
}
.fee-editor-identity p {
  font-size: 12px;
  margin-top: 4px;
}
.fee-date-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  width: 100%;
}
.fee-edit-hint {
  font-size: 12px;
  margin-top: 8px;
}
:global(.annual-fee-dialog) {
  max-height: 90dvh;
  margin-block: 5vh;
  display: flex;
  flex-direction: column;
}
:global(.annual-fee-dialog .el-dialog__body) {
  overflow-y: auto;
  min-height: 0;
}
@container (max-width: 900px) {
  .fee-filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .fee-search {
    grid-column: 1 / -1;
  }
}
@container (max-width: 740px) {
  .fee-list-heading {
    display: none;
  }
  .fee-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px;
    padding: 20px 0;
  }
  .fee-identity,
  .fee-benefits {
    grid-column: 1 / -1;
  }
  .fee-row-soon {
    box-shadow: none;
  }
  .fee-mobile-label {
    display: block;
    color: #7b847b;
    font-size: 12px;
    margin-bottom: 6px;
  }
  .fee-actions {
    grid-column: 1 / -1;
  }
}
@media (max-width: 1100px) {
  .fee-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 520px) {
  .annual-fees-page .page-heading {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .annual-fees-page h1 {
    font-size: 25px;
  }
  .fee-summary {
    gap: 10px;
  }
  .fee-stat {
    padding: 14px;
  }
  .fee-stat > strong {
    font-size: 28px;
  }
  .fee-panel {
    padding: 16px;
  }
}
</style>
