<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, Refresh, Plus } from '@element-plus/icons-vue'
import AccountIdentity from '../components/AccountIdentity.vue'
import { api } from '../api'
import { amountText, stateText, type Overview, type Statement } from '../types'
const data = ref<Overview | null>(null),
  loading = ref(false),
  loadError = ref(''),
  router = useRouter()
const cny = computed(() => data.value?.totals.find((t) => t.currency === 'CNY')?.amount ?? '0.00')
const due = (n: number | null) =>
  n === null ? '日期未知' : n < 0 ? `已过期 ${-n} 天` : n === 0 ? '今天到期' : `${n} 天后到期`
const isUrgent = (s: Statement) =>
  Number(s.remaining) > 0 &&
  (s.overdue || (s.days_until_due !== null && s.days_until_due >= 0 && s.days_until_due <= 7))
async function load() {
  loading.value = true
  loadError.value = ''
  try {
    data.value = await api<Overview>('/admin/overview')
  } catch (e) {
    loadError.value = (e as Error).message
    ElMessage.error(loadError.value)
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>
<template>
  <div class="page-heading">
    <div>
      <div class="eyebrow">THE BIG PICTURE</div>
      <h1>让账单，井然有序。</h1>
      <p>当前账单与还款进度，一目了然。</p>
    </div>
    <div class="heading-actions">
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button
      ><el-button type="primary" :icon="Plus" @click="router.push('/statements?new=1')"
        >录入账单</el-button
      >
    </div>
  </div>
  <div v-if="data" v-loading="loading">
    <section class="summary-grid" aria-label="账单概况" :aria-busy="loading">
      <div class="total-panel">
        <div class="total-label">当前账单记录待还 <span>CNY</span></div>
        <div class="total-number"><small>¥</small>{{ amountText(cny).replace('¥ ', '') }}</div>
        <div class="total-bottom">
          <span>{{ data.account_count }} 个追踪中的还款账户</span><span>最新确认账单汇总</span>
        </div>
        <div v-if="data.totals.some((t) => t.currency !== 'CNY')" class="foreign-totals">
          <span v-for="t in data.totals.filter((t) => t.currency !== 'CNY')" :key="t.currency">{{
            amountText(t.amount, t.currency)
          }}</span>
        </div>
      </div>
      <div class="stat-panel">
        <span class="stat-label"
          ><span class="stat-index" aria-hidden="true">01 / PRIORITY</span>未来 7 天到期</span
        ><strong>{{ data.due_soon_count }}<small>笔</small></strong
        ><span class="stat-foot">优先安排近期还款</span>
      </div>
      <div class="stat-panel">
        <span class="stat-label"
          ><span class="stat-index" aria-hidden="true">02 / REVIEW</span>待核对账单</span
        ><strong>{{ data.pending_count }}<small>笔</small></strong
        ><router-link to="/statements?status=draft" class="stat-foot link"
          >前往确认 <el-icon><ArrowRight /></el-icon
        ></router-link>
      </div>
    </section>
    <div v-if="data.warnings.length" class="warning-strip" role="note" aria-label="账单风险提示">
      <span class="warning-mark">!</span>
      <div>
        <strong>{{ data.warnings.length }} 项账单状态需要留意</strong>
        <p>
          {{
            data.warnings
              .slice(0, 3)
              .map((w) => `${w.account_name}：${w.message}`)
              .join('；')
          }}
        </p>
      </div>
    </div>
    <div class="section-heading">
      <h2>
        当前账单<span class="count-pill">{{ data.latest_statements.length }}</span>
      </h2>
      <router-link to="/statements" class="link"
        >全部账单 <el-icon><ArrowRight /></el-icon
      ></router-link>
    </div>
    <section class="panel">
      <div v-if="data.latest_statements.length" class="bill-list-heading" aria-hidden="true">
        <span>账户与本期账单</span><span>到期时间 / 记录待还</span>
      </div>
      <el-empty
        v-if="!data.latest_statements.length"
        description="还没有已确认账单。先添加账户，再录入第一份账单。"
        ><el-button @click="router.push('/accounts')">添加账户</el-button></el-empty
      >
      <div v-for="s in data.latest_statements" :key="s.id" class="bill-row">
        <div class="bank-badge" :class="s.account.bank_code">
          {{ s.account.bank_name.slice(0, 1) }}
        </div>
        <div class="bill-identity">
          <AccountIdentity :account="s.account" />
          <small
            >{{ s.account.mode === 'merged' ? '多卡合并还款' : '独立还款' }} · 账单日：{{
              s.statement_date
            }}</small
          >
          <div v-if="s.needs_update" class="bill-update-notice">
            <el-tag type="warning">待更新账单</el-tag>
            <small>本期应出账：{{ s.expected_statement_date }}</small>
          </div>
        </div>
        <div class="bill-deadline" :class="{ urgent: isUrgent(s) }">
          <el-tag v-if="s.payment_status === 'settled'" type="success">{{
            s.needs_update ? '旧账单已结清' : stateText(s)
          }}</el-tag>
          <strong v-else class="bill-due-countdown">
            <template
              v-if="Number(s.remaining) > 0 && s.days_until_due !== null && s.days_until_due !== 0"
            >
              <span v-if="s.days_until_due < 0">已过期</span>
              <span class="bill-due-number">{{ Math.abs(s.days_until_due) }}</span>
              <span>{{ s.days_until_due < 0 ? '天' : '天后到期' }}</span>
            </template>
            <template v-else>{{
              Number(s.remaining) === 0 ? stateText(s) : due(s.days_until_due)
            }}</template>
          </strong>
          <span class="bill-due-date">{{ s.due_date || '待确认' }}</span>
        </div>
        <div class="bill-amount" :class="{ urgent: isUrgent(s) }">
          <span>{{ s.needs_update ? '旧账单待还' : '记录待还' }}</span
          ><strong>{{ amountText(s.remaining, s.currency) }}</strong
          ><small v-if="s.payment_status === 'partial'"
            >已标记 {{ amountText(s.paid, s.currency) }}</small
          >
        </div>
        <el-button
          text
          @click="router.push({ path: '/statements', query: { account_id: s.account.id } })"
          >查看<el-icon><ArrowRight /></el-icon
        ></el-button>
      </div>
    </section>
    <div v-if="data.historical_unresolved.length" class="history-notice">
      <strong>{{ data.historical_unresolved.length }} 份历史账单尚未标记结清</strong>
      <p>可能已结转到当前账单，不重复计入上方总额。请到历史账单中单独核对。</p>
    </div>
    <p class="fine-print">{{ data.disclaimer }} 金额来自人工确认或用户记录，不代表银行到账状态。</p>
  </div>
  <el-alert
    v-else-if="loadError"
    title="账单概况暂时无法加载"
    type="error"
    show-icon
    :closable="false"
  >
    <p>{{ loadError }}</p>
    <el-button :loading="loading" @click="load">重新加载</el-button>
  </el-alert>
  <el-skeleton v-else :rows="8" :loading="loading" animated />
</template>
