<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, allItems } from '../api'
import type { Account, Bank, Page, Statement } from '../types'
import { amountText, dateTime } from '../types'
import type {
  Candidate,
  ModelCall,
  ModelProfile,
  ModelRun,
  ModelStatus,
  PublishResult,
  ReviewFields,
} from '../model-types'
import { detailState, modelState, warningText } from '../model-types'
const route = useRoute(),
  router = useRouter()
const status = ref<ModelStatus | null>(null),
  candidates = ref<Candidate[]>([]),
  calls = ref<ModelCall[]>([])
const accounts = ref<Account[]>([]),
  banks = ref<Bank[]>([])
const tab = ref('review'),
  state = ref('pending'),
  page = ref(1),
  total = ref(0),
  callPage = ref(1),
  callTotal = ref(0)
const runFilter = ref(String(route.query.run || '')),
  run = ref<ModelRun | null>(null)
const loading = ref(false),
  busy = ref(false),
  configurationOpen = ref(false)
const defaultConfig = () => ({
  base_url: '',
  model: '',
  api_key: '',
  active: false,
  auto_parse: false,
  auto_publish_profiles: [] as ModelProfile[],
  timeout_seconds: 60,
  max_tokens: 8192,
  input_chars: 120000,
  daily_call_limit: 50,
  task_call_limit: 2,
  json_mode: false,
  input_price: '',
  output_price: '',
  price_currency: 'CNY',
})
const configurationVersion = ref(0)
const form = ref(defaultConfig()),
  privacyMode = ref('retain'),
  privacy = ref('')
const profileBank = ref(''),
  profileMedia = ref('message/rfc822'),
  verified = ref(false)
const drawer = ref(false),
  selected = ref<Candidate | null>(null),
  review = ref<ReviewFields | null>(null)
const old = ref<Statement | null>(null),
  reason = ref(''),
  acknowledge = ref(false),
  action = ref('create')
const source = ref<{
  input: { parts: { name: string; text: string }[]; warnings: string[] }
} | null>(null)
const sourceError = ref(''),
  reviewTab = ref('summary'),
  transactionPage = ref(1)
const dirty = computed(
  () =>
    selected.value &&
    review.value &&
    JSON.stringify(review.value) !== JSON.stringify(selected.value.fields),
)
const pending = computed(() => selected.value?.state === 'pending')
const hasWarnings = computed(() => selected.value?.warnings.some((w) => w !== 'EXISTING_STATEMENT'))
const currentTransactions = computed(
  () =>
    review.value?.transactions.slice(
      (transactionPage.value - 1) * 20,
      transactionPage.value * 20,
    ) || [],
)
let timer: ReturnType<typeof setInterval> | undefined
async function load(quiet = false) {
  if (loading.value || busy.value) return
  if (!quiet) loading.value = true
  try {
    const [s, rows, history] = await Promise.all([
      api<ModelStatus>('/admin/model-config'),
      api<Page<Candidate>>(
        `/admin/candidates?page=${page.value}&page_size=20&state=${state.value}&run_id=${encodeURIComponent(runFilter.value)}`,
      ),
      api<Page<ModelCall>>(`/admin/model-calls?page=${callPage.value}&page_size=20`),
    ])
    status.value = s
    candidates.value = rows.items
    total.value = rows.total
    calls.value = history.items
    callTotal.value = history.total
    if (runFilter.value) run.value = await api<ModelRun>(`/admin/model-runs/${runFilter.value}`)
  } catch (e) {
    if (!quiet) ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}
function closeConfiguration() {
  configurationOpen.value = false
  form.value.api_key = ''
  privacy.value = ''
}
function configure() {
  const c = status.value?.config
  configurationVersion.value = c?.version || 0
  form.value = {
    ...defaultConfig(),
    ...(c?.configured
      ? Object.fromEntries(
          Object.keys(defaultConfig()).map((k) => [
            k,
            (c as unknown as Record<string, unknown>)[k] ??
              (defaultConfig() as Record<string, unknown>)[k],
          ]),
        )
      : {}),
    api_key: '',
  }
  privacyMode.value = 'retain'
  privacy.value = ''
  verified.value = false
  configurationOpen.value = true
}
async function saveConfiguration() {
  if (busy.value) return
  try {
    if (form.value.auto_parse && !status.value?.config.auto_parse)
      await ElMessageBox.confirm(
        '自动识别会把已准备的历史资料和新资料发送给模型服务商，并可能产生费用。确认允许吗？',
        '启用自动识别',
        { type: 'warning' },
      )
    busy.value = true
    await api('/admin/model-config', 'PUT', {
      ...form.value,
      expected_version: configurationVersion.value,
      api_key: form.value.api_key || null,
      input_price: form.value.input_price || null,
      output_price: form.value.output_price || null,
      privacy_values:
        privacyMode.value === 'retain'
          ? null
          : privacyMode.value === 'clear'
            ? []
            : privacy.value.split('\n').filter((x) => x.length),
    })
    form.value.api_key = ''
    privacy.value = ''
    configurationOpen.value = false
    ElMessage.success('模型配置已保存，旧任务不会使用新配置提交结果')
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error((e as Error).message)
  } finally {
    busy.value = false
    await load()
  }
}
async function deleteConfiguration() {
  if (busy.value || !status.value?.config.configured) return
  const version = configurationOpen.value ? configurationVersion.value : status.value.config.version
  try {
    await ElMessageBox.confirm(
      '删除后将清除模型地址、名称、API Key 和隐私替换设置，关闭自动识别并取消尚未发送的模型任务。历史账单、候选和调用记录不会删除。已发送的请求无法撤回，其结果不会继续提交。确认删除吗？',
      '删除模型配置',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
    busy.value = true
    await api('/admin/model-config', 'DELETE', { expected_version: version })
    closeConfiguration()
    form.value = defaultConfig()
    ElMessage.success('模型配置已删除，历史账单与调用记录已保留')
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error((e as Error).message)
  } finally {
    busy.value = false
    await load()
  }
}
function addProfile() {
  if (!profileBank.value || !verified.value) {
    ElMessage.warning('请选择银行，并确认已对真实样本完成验证')
    return
  }
  if (!form.value.active) {
    ElMessage.warning('请先启用模型')
    return
  }
  if (
    !form.value.auto_publish_profiles.some(
      (p) => p.bank_code === profileBank.value && p.media_type === profileMedia.value,
    )
  )
    form.value.auto_publish_profiles.push({
      bank_code: profileBank.value,
      media_type: profileMedia.value,
      prompt_version: 'bill-prompt-v1',
      converter_version: 'generic-v1',
      verified: true,
    })
  verified.value = false
}
async function connectionTest() {
  if (busy.value) return
  busy.value = true
  try {
    const t = await api<{ id: string }>('/admin/model-config/test', 'POST')
    ElMessage.success(`连接测试已排队，不会发送账单；可在导入任务中查看 ${t.id.slice(0, 8)}`)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    busy.value = false
    await load()
  }
}
async function openCandidate(id: string) {
  try {
    const c = await api<Candidate>(`/admin/candidates/${id}`)
    selected.value = c
    review.value = JSON.parse(JSON.stringify(c.fields))
    reason.value = ''
    acknowledge.value = false
    action.value = c.existing_statement ? 'revise' : 'create'
    source.value = null
    sourceError.value = ''
    old.value = null
    reviewTab.value = 'summary'
    transactionPage.value = 1
    drawer.value = true
    if (c.existing_statement)
      old.value = await api<Statement>(`/admin/statements/${c.existing_statement.id}`)
    if (c.source_id) {
      try {
        source.value = await api(`/admin/sources/${c.source_id}/preview`)
      } catch (e) {
        sourceError.value = (e as Error).message
      }
    }
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}
async function saveReview() {
  if (!selected.value || !review.value || busy.value) return
  if (!reason.value.trim()) {
    ElMessage.warning('请填写核对说明')
    return
  }
  busy.value = true
  try {
    selected.value = await api<Candidate>(`/admin/candidates/${selected.value.id}`, 'PATCH', {
      expected_version: selected.value.version,
      fields: review.value,
      reason: reason.value,
    })
    review.value = JSON.parse(JSON.stringify(selected.value.fields))
    acknowledge.value = false
    action.value = selected.value.existing_statement ? 'revise' : 'create'
    old.value = selected.value.existing_statement
      ? await api<Statement>(`/admin/statements/${selected.value.existing_statement.id}`)
      : null
    ElMessage.success('人工核对已保存；原始模型结果保持不变')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    busy.value = false
    await load()
  }
}
async function publish() {
  const c = selected.value
  if (!c || busy.value) return
  if (dirty.value) {
    ElMessage.warning('请先保存核对修改')
    return
  }
  if (!reason.value.trim()) {
    ElMessage.warning('请填写核对说明')
    return
  }
  try {
    await ElMessageBox.confirm(
      action.value === 'revise'
        ? '确认修订已有账单？原有还款记录保留，不重复创建账单。'
        : action.value === 'duplicate'
          ? '确认该候选与已有账单相同？不会改动已有账单。'
          : '确认发布账单？金额与到期日以你的核对结果为准。',
      '发布确认',
      { type: 'warning' },
    )
    busy.value = true
    const r = await api<PublishResult>(`/admin/candidates/${c.id}/publish`, 'POST', {
      expected_version: c.version,
      expected_statement_version: c.existing_statement?.version || null,
      action: action.value,
      acknowledge_warnings: acknowledge.value,
      reason: reason.value,
    })
    drawer.value = false
    ElMessage.success(r.duplicate ? '已标记相同，未重复入账' : '账单已发布，还款标注不代表银行到账')
    await router.push(`/statements?detail=${r.statement.id}`)
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error((e as Error).message)
  } finally {
    busy.value = false
    await load()
  }
}
async function reject() {
  if (!selected.value || busy.value) return
  if (!reason.value.trim()) {
    ElMessage.warning('请填写忽略原因')
    return
  }
  try {
    await ElMessageBox.confirm('忽略这个候选，不会删除原邮件或已有账单。', '忽略候选', {
      type: 'warning',
    })
    busy.value = true
    await api(`/admin/candidates/${selected.value.id}/reject`, 'POST', {
      expected_version: selected.value.version,
      reason: reason.value,
    })
    drawer.value = false
    ElMessage.success('候选已忽略')
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error((e as Error).message)
  } finally {
    busy.value = false
    await load()
  }
}
async function refreshCandidate() {
  if (!selected.value) return
  if (dirty.value) {
    try {
      await ElMessageBox.confirm('刷新会放弃尚未保存的修改。', '重新读取', { type: 'warning' })
    } catch {
      return
    }
  }
  await openCandidate(selected.value.id)
}
function filter() {
  page.value = 1
  void load()
}
function clearRun() {
  runFilter.value = ''
  run.value = null
  void router.replace('/models')
  filter()
}
onMounted(async () => {
  if (runFilter.value) state.value = ''
  await load()
  try {
    ;[accounts.value, banks.value] = await Promise.all([
      allItems<Account>('/admin/accounts'),
      api<{ items: Bank[] }>('/admin/banks').then((r) => r.items),
    ])
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
  timer = setInterval(() => void load(true), 5000)
})
onUnmounted(() => clearInterval(timer))
</script>
<template>
  <div class="models-page">
    <div class="page-heading">
      <div>
        <div class="eyebrow">THE REVIEW · VERIFIED BEFORE FILED</div>
        <h1>模型整理，你来定稿。</h1>
        <p>账单字段仅由大模型提取。原文、证据和人工修改各自留档，不确定的内容不直接入账。</p>
      </div>
      <el-button :loading="loading" @click="load()">刷新状态</el-button>
    </div>
    <div class="model-metrics" v-if="status">
      <article>
        <small>等待核对</small><strong>{{ status.pending_candidates }}<i> 份</i></strong>
      </article>
      <article>
        <small>今日已预留调用 · 上海时间</small
        ><strong
          >{{ status.usage.reserved_calls
          }}<i> / {{ status.config.daily_call_limit || '—' }}</i></strong
        >
      </article>
      <article>
        <small>费用估算 · 不是服务商账单</small
        ><strong class="model-cost">{{
          Object.entries(status.usage.cost_estimates)
            .map(([c, v]) => amountText(v, c))
            .join(' / ') || '暂无可估算费用'
        }}</strong>
        <p v-if="status.usage.unknown_cost_calls">
          {{ status.usage.unknown_cost_calls }} 次费用未知，不计作零费用
        </p>
      </article>
    </div>
    <section class="panel padded section-gap">
      <div class="model-config-bar">
        <div>
          <h3>{{ status?.config.configured ? status.config.model : '先配置你的模型服务' }}</h3>
          <p class="muted">
            {{ status?.config.active ? '模型已启用' : '模型未启用' }} ·
            {{ status?.config.auto_parse ? '自动发送已开启' : '自动发送关闭' }} ·
            {{
              status?.config.auto_publish_profiles?.length
                ? '仅已验证格式自动发布'
                : '全部候选人工发布'
            }}
          </p>
        </div>
        <div class="model-actions">
          <el-button :disabled="!status?.config.configured || busy" @click="connectionTest"
            >测试连接</el-button
          ><el-button
            v-if="status?.config.configured"
            type="danger"
            plain
            :disabled="busy"
            @click="deleteConfiguration"
            >删除模型</el-button
          ><el-button
            type="primary"
            :disabled="busy || !status?.master_key_configured"
            @click="configure"
            >配置模型</el-button
          >
        </div>
      </div>
      <el-alert
        v-if="status && !status.master_key_configured"
        type="warning"
        :closable="false"
        title="请先配置部署主密钥，再保存模型凭证。"
      />
    </section>
    <section v-if="runFilter" class="panel padded section-gap">
      <div class="model-config-bar">
        <div>
          <strong>本次识别 · {{ run ? modelState(run.state) : '读取中' }}</strong>
          <p class="muted">
            {{
              run?.error_code ||
              run?.task?.error_message ||
              '候选将在识别完成后出现；非账单资料不产生候选。'
            }}
          </p>
        </div>
        <el-button text @click="clearRun">查看全部候选</el-button>
      </div>
      <router-link v-if="run?.state === 'failed' || run?.state === 'cancelled'" to="/imports"
        >前往导入任务检查或重试</router-link
      >
    </section>
    <section class="panel padded section-gap" v-loading="loading">
      <el-tabs v-model="tab"
        ><el-tab-pane label="候选核对" name="review"
          ><div class="filter-bar model-filter">
            <el-select v-model="state" aria-label="候选状态" placeholder="全部候选" @change="filter"
              ><el-option label="全部候选" value="" /><el-option
                v-for="s in ['pending', 'published', 'duplicate', 'rejected']"
                :key="s"
                :label="modelState(s)"
                :value="s" /></el-select
            ><span class="muted">{{ total }} 份候选 · 不同币种分别入账</span>
          </div>
          <el-table :data="candidates" empty-text="暂无候选，请先在邮箱与导入中识别资料"
            ><el-table-column label="识别账户" min-width="190"
              ><template #default="{ row }"
                ><strong
                  >{{
                    banks.find((b) => b.code === row.bank_code)?.name || row.bank_code || '银行未知'
                  }}
                  · {{ row.holder_name || '持卡人未知' }}</strong
                >
                <p class="muted">
                  卡尾号 {{ row.card_last_four.join(' / ') || '未知' }} ·
                  {{ row.fields.statement_date || '日期未知' }}
                </p></template
              ></el-table-column
            ><el-table-column label="本期应还" min-width="140"
              ><template #default="{ row }">{{
                amountText(row.fields.amount, row.fields.currency || 'CNY')
              }}</template></el-table-column
            ><el-table-column label="核对状态" min-width="155"
              ><template #default="{ row }"
                ><el-tag :type="row.state === 'pending' ? 'warning' : 'success'">{{
                  modelState(row.state)
                }}</el-tag>
                <p class="muted">
                  {{ row.warnings.length }} 项待注意 · {{ row.transaction_count }} 笔交易
                </p></template
              ></el-table-column
            ><el-table-column label="操作" width="90"
              ><template #default="{ row }"
                ><el-button text @click="openCandidate(row.id)">{{
                  row.state === 'pending' ? '核对' : '查看'
                }}</el-button></template
              ></el-table-column
            ></el-table
          ><el-pagination
            class="section-gap"
            v-model:current-page="page"
            :page-size="20"
            :total="total"
            layout="prev, pager, next"
            @current-change="load()"
        /></el-tab-pane>
        <el-tab-pane label="调用记录" name="calls"
          ><p class="muted">
            失败和中断也占用调用预算。费用为按填写单价计算的估算；缺少用量或单价时显示未知。
          </p>
          <el-table :data="calls" empty-text="尚无模型调用"
            ><el-table-column label="时间 / 模型" min-width="210"
              ><template #default="{ row }"
                >{{ dateTime(row.created_at) }}
                <p class="muted">
                  {{ row.model }} · 配置 v{{ row.config_version }} ·
                  {{ row.kind === 'llm_test' ? '连接测试' : '账单识别' }}
                </p></template
              ></el-table-column
            ><el-table-column label="状态" min-width="120"
              ><template #default="{ row }"
                >{{ modelState(row.state) }}
                <p class="muted">{{ row.error_code }}</p></template
              ></el-table-column
            ><el-table-column label="输入 / 输出 token" min-width="145"
              ><template #default="{ row }"
                >{{ row.input_tokens ?? '未知' }} / {{ row.output_tokens ?? '未知' }}</template
              ></el-table-column
            ><el-table-column label="估算费用" min-width="145"
              ><template #default="{ row }">{{
                row.cost_estimate === null
                  ? '未知'
                  : amountText(row.cost_estimate, row.price_currency)
              }}</template></el-table-column
            ></el-table
          ><el-pagination
            class="section-gap"
            v-model:current-page="callPage"
            :page-size="20"
            :total="callTotal"
            layout="prev, pager, next"
            @current-change="load()" /></el-tab-pane
      ></el-tabs>
    </section>
    <el-dialog
      v-model="configurationOpen"
      title="模型配置 · 密钥不回显"
      @closed="closeConfiguration"
      width="min(780px,95vw)"
      :close-on-click-modal="false"
      ><el-alert
        type="info"
        :closable="false"
        title="支持公网 HTTP / HTTPS，兼容 /v1/chat/completions。建议使用 HTTPS；HTTP 会明文传输 API Key 和资料文本。启用识别意味着将资料文本发送给你的服务商，请确认隐私与费用政策。"
      /><el-form label-position="top" class="section-gap" @submit.prevent="saveConfiguration"
        ><div class="model-form-grid">
          <el-form-item label="HTTP / HTTPS 基础地址"
            ><el-input
              v-model="form.base_url"
              placeholder="https://你的服务商/v1"
              autocomplete="off" /></el-form-item
          ><el-form-item label="模型名称"
            ><el-input v-model="form.model" autocomplete="off"
          /></el-form-item>
        </div>
        <el-form-item
          :label="status?.config.configured ? 'API Key · 留空保留已有密钥' : 'API Key · 首次必填'"
          ><el-input
            v-model="form.api_key"
            type="password"
            show-password
            autocomplete="new-password"
        /></el-form-item>
        <div class="model-switches">
          <el-checkbox
            v-model="form.active"
            @change="
              (v: unknown) => {
                if (!v) {
                  form.auto_parse = false
                  form.auto_publish_profiles = []
                }
              }
            "
            >启用模型</el-checkbox
          ><el-checkbox v-model="form.auto_parse" :disabled="!form.active"
            >自动发送已准备的资料（含历史）</el-checkbox
          ><el-checkbox v-model="form.json_mode">服务商支持 JSON 响应模式</el-checkbox>
        </div>
        <div class="model-form-grid">
          <el-form-item label="请求总超时（秒）"
            ><el-input-number v-model="form.timeout_seconds" :min="5" :max="120" /></el-form-item
          ><el-form-item label="最大输出 token"
            ><el-input-number v-model="form.max_tokens" :min="512" :max="32768" /></el-form-item
          ><el-form-item label="最大输入字符"
            ><el-input-number v-model="form.input_chars" :min="1000" :max="250000" /></el-form-item
          ><el-form-item label="每日调用上限（含测试、重试）"
            ><el-input-number v-model="form.daily_call_limit" :min="1" :max="1000" /></el-form-item
          ><el-form-item label="单任务最多调用次数"
            ><el-input-number v-model="form.task_call_limit" :min="1" :max="3" /></el-form-item
          ><el-form-item label="价格币种"
            ><el-select v-model="form.price_currency"
              ><el-option label="CNY" value="CNY" /><el-option
                label="USD"
                value="USD" /></el-select></el-form-item
          ><el-form-item label="每百万输入 token 单价（可留空）"
            ><el-input v-model="form.input_price" /></el-form-item
          ><el-form-item label="每百万输出 token 单价（可留空）"
            ><el-input v-model="form.output_price"
          /></el-form-item>
        </div>
        <el-form-item label="发送前替换的已知私人文本"
          ><el-select v-model="privacyMode"
            ><el-option label="保留已有列表（不回显）" value="retain" /><el-option
              label="用新列表替换"
              value="replace" /><el-option
              label="清空已有列表"
              value="clear" /></el-select></el-form-item
        ><el-input
          v-if="privacyMode === 'replace'"
          v-model="privacy"
          type="textarea"
          :rows="3"
          placeholder="每行一个明确文本，例如完整姓名；不是自动隐私识别。"
        />
        <p class="muted">
          只替换你明确填写的文本，不自动发现敏感信息；本地恢复占位符后再核对结果。
        </p>
        <el-collapse class="section-gap"
          ><el-collapse-item title="可选：只对已验证的格式自动发布" name="profiles"
            ><el-alert
              type="warning"
              :closable="false"
              title="默认不自动发布。请用真实脱敏样本验证后才添加格式；缺字段、缺证据、未唯一匹配、部分输入或已有账单均转人工核对。"
            />
            <div v-for="(p, i) in form.auto_publish_profiles" :key="i" class="model-profile">
              <span
                >{{ banks.find((b) => b.code === p.bank_code)?.name || p.bank_code }} ·
                {{ p.media_type === 'application/pdf' ? 'PDF' : '邮件' }} · {{ p.prompt_version }} /
                {{ p.converter_version }}</span
              ><el-button text type="danger" @click="form.auto_publish_profiles.splice(i, 1)"
                >移除</el-button
              >
            </div>
            <div class="model-form-grid section-gap">
              <el-select v-model="profileBank" placeholder="已验证银行"
                ><el-option
                  v-for="b in banks.filter((b) => b.active)"
                  :key="b.code"
                  :value="b.code"
                  :label="b.name" /></el-select
              ><el-select v-model="profileMedia"
                ><el-option label="邮件（含其中附件）" value="message/rfc822" /><el-option
                  label="独立 PDF"
                  value="application/pdf"
              /></el-select>
            </div>
            <el-checkbox v-model="verified"
              >我已验证该银行、来源类型和当前提示词 / 转换版本</el-checkbox
            ><el-button :disabled="!verified || !form.active" @click="addProfile"
              >添加已验证格式</el-button
            ></el-collapse-item
          ></el-collapse
        ></el-form
      ><template #footer
        ><el-button
          v-if="status?.config.configured"
          type="danger"
          plain
          :disabled="busy"
          @click="deleteConfiguration"
          >删除模型</el-button
        ><el-button :disabled="busy" @click="closeConfiguration">取消</el-button
        ><el-button type="primary" :loading="busy" @click="saveConfiguration"
          >保存模型配置</el-button
        ></template
      ></el-dialog
    >
    <el-drawer
      v-model="drawer"
      title="候选核对档案"
      size="min(920px,98vw)"
      :close-on-click-modal="false"
      ><template v-if="selected && review"
        ><div class="model-review-head">
          <div>
            <h2>{{ selected.holder_name || '持卡人待确认' }} · {{ modelState(selected.state) }}</h2>
            <p class="muted">
              候选 v{{ selected.version }} ·
              {{ selected.match_state === 'unique' ? '唯一账户匹配' : '账户需要人工确认' }} ·
              原始结果不覆盖
            </p>
          </div>
          <el-button :disabled="busy" @click="refreshCandidate">重新读取</el-button>
        </div>
        <div v-if="selected.warnings.length" class="model-warnings">
          <strong>发布前请逐项核对</strong>
          <ul>
            <li v-for="w in selected.warnings" :key="w">{{ warningText(w) }}</li>
          </ul>
        </div>
        <el-tabs v-model="reviewTab"
          ><el-tab-pane label="摘要与核对" name="summary"
            ><div v-if="old" class="model-old">
              <strong>已有账单 · v{{ old.version }} · 还款标注保留</strong>
              <p>
                当前应还 {{ amountText(old.amount, old.currency) }} → 候选
                {{ amountText(review.amount, review.currency || 'CNY') }}
              </p>
              <p>
                当前到期 {{ old.due_date || '未知' }} → 候选 {{ review.due_date || '未知' }} ·
                已标记 {{ amountText(old.paid, old.currency) }}
              </p>
            </div>
            <el-form label-position="top" :disabled="!pending || busy"
              ><el-form-item label="还款账户"
                ><el-select v-model="review.account_id" filterable placeholder="请选择有效账户"
                  ><el-option
                    v-for="a in accounts.filter((a) => a.effective_active)"
                    :key="a.id"
                    :value="a.id"
                    :label="`${a.bank_name} · ${a.cardholder_name} · ${a.name} · ${a.cards.map((c) => c.last_four).join('/')}`" /></el-select
              ></el-form-item>
              <div class="model-form-grid">
                <el-form-item label="账单日期"
                  ><el-date-picker
                    v-model="review.statement_date"
                    type="date"
                    value-format="YYYY-MM-DD" /></el-form-item
                ><el-form-item label="币种"
                  ><el-select v-model="review.currency"
                    ><el-option
                      v-for="c in ['CNY', 'USD', 'EUR', 'HKD', 'JPY']"
                      :key="c"
                      :label="c"
                      :value="c" /></el-select></el-form-item
                ><el-form-item label="本期应还"
                  ><el-input
                    :model-value="review.amount ?? ''"
                    @update:model-value="
                      (v: string) => review && (review.amount = v || null)
                    " /></el-form-item
                ><el-form-item label="最低还款"
                  ><el-input
                    :model-value="review.minimum ?? ''"
                    @update:model-value="
                      (v: string) => review && (review.minimum = v || null)
                    " /></el-form-item
                ><el-form-item label="还款到期日"
                  ><el-date-picker
                    v-model="review.due_date"
                    value-format="YYYY-MM-DD" /></el-form-item
                ><el-form-item label="账期开始"
                  ><el-date-picker
                    v-model="review.period_start"
                    value-format="YYYY-MM-DD" /></el-form-item
                ><el-form-item label="账期结束"
                  ><el-date-picker v-model="review.period_end" value-format="YYYY-MM-DD"
                /></el-form-item></div
            ></el-form>
            <h3>模型原始字段与引用</h3>
            <p class="muted">
              引用存在不代表金额或日期一定识别正确，请对照原文；人工修改与原始结果分别保存。
            </p>
            <div class="model-evidence" v-for="(e, k) in selected.extraction?.evidence" :key="k">
              <strong>{{ k }} · {{ JSON.stringify(selected.extraction?.[k]) }}</strong
              ><small>资料片段 {{ e.part }}</small>
              <blockquote>{{ e.quote }}</blockquote>
            </div></el-tab-pane
          >
          <el-tab-pane :label="`交易详单 · ${review.transactions.length}`" name="transactions"
            ><el-select
              v-model="review.transaction_status"
              :disabled="!pending"
              aria-label="详单状态"
              ><el-option
                v-for="s in ['not_provided', 'partial', 'complete']"
                :key="s"
                :label="detailState(s)"
                :value="s"
            /></el-select>
            <p class="muted">
              重复交易按原顺序保留；未提供详单不等于零消费。更改状态不能凭空补全缺失交易。
            </p>
            <div class="model-transaction" v-for="(t, i) in currentTransactions" :key="i">
              <strong>第 {{ (transactionPage - 1) * 20 + i + 1 }} 笔</strong
              ><el-form :disabled="!pending || busy" label-position="top"
                ><div class="model-form-grid">
                  <el-form-item label="描述"><el-input v-model="t.description" /></el-form-item
                  ><el-form-item label="绝对金额"
                    ><el-input
                      :model-value="t.amount ?? ''"
                      @update:model-value="(v: string) => (t.amount = v || null)" /></el-form-item
                  ><el-form-item label="交易日"
                    ><el-date-picker
                      v-model="t.transaction_date"
                      value-format="YYYY-MM-DD" /></el-form-item
                  ><el-form-item label="记账日"
                    ><el-date-picker
                      v-model="t.posted_date"
                      value-format="YYYY-MM-DD" /></el-form-item
                  ><el-form-item label="币种"
                    ><el-select v-model="t.currency" clearable @clear="t.currency = null"
                      ><el-option
                        v-for="c in ['CNY', 'USD', 'EUR', 'HKD', 'JPY']"
                        :key="c"
                        :value="c"
                        :label="c" /></el-select></el-form-item
                  ><el-form-item label="方向"
                    ><el-select v-model="t.direction"
                      ><el-option label="支出" value="debit" /><el-option
                        label="入账 / 退款"
                        value="credit" /><el-option label="未知" value="unknown" /></el-select
                  ></el-form-item></div
              ></el-form>
              <blockquote v-if="t.evidence">
                片段 {{ t.evidence.part }}：{{ t.evidence.quote }}
              </blockquote>
            </div>
            <el-pagination
              v-model:current-page="transactionPage"
              :page-size="20"
              :total="review.transactions.length"
              layout="prev, pager, next"
          /></el-tab-pane>
          <el-tab-pane label="安全原文" name="source"
            ><p class="muted">只展示转换后的纯文本，不执行邮件 HTML，不加载外链。</p>
            <p v-if="sourceError">{{ sourceError }}</p>
            <p v-if="!source && !sourceError">读取原资料中…</p>
            <section v-for="(p, i) in source?.input.parts" :key="i">
              <h3>片段 {{ i + 1 }} · {{ p.name }}</h3>
              <pre class="source-text">{{ p.text }}</pre>
            </section></el-tab-pane
          ><el-tab-pane label="人工修改历史" name="history"
            ><el-empty
              v-if="!selected.revisions?.length"
              description="尚无人工修改"
              :image-size="60"
            />
            <div v-for="r in selected.revisions" :key="r.version" class="record-row">
              <div>
                <strong>v{{ r.version }} · {{ r.reason }}</strong>
                <p>
                  {{ dateTime(r.created_at) }} · 应还
                  {{ amountText(r.fields.amount, r.fields.currency || 'CNY') }}
                </p>
              </div>
            </div></el-tab-pane
          ></el-tabs
        >
        <div v-if="pending" class="model-publish">
          <el-input
            v-model="reason"
            type="textarea"
            :rows="2"
            placeholder="填写核对说明或忽略原因（必填）"
            aria-label="核对说明"
          /><el-checkbox v-if="hasWarnings" v-model="acknowledge"
            >我已逐项核对并接受上述不确定信息</el-checkbox
          ><el-radio-group v-if="selected.existing_statement" v-model="action"
            ><el-radio value="revise">明确修订已有账单</el-radio
            ><el-radio value="duplicate">与已有账单相同</el-radio></el-radio-group
          >
          <p v-if="dirty" class="import-error">有未保存的修改，请先保存核对，再发布。</p>
          <div class="model-actions">
            <el-button :loading="busy" @click="saveReview">保存核对修改</el-button
            ><el-button
              type="primary"
              :disabled="Boolean(dirty) || (hasWarnings && !acknowledge)"
              :loading="busy"
              @click="publish"
              >{{
                selected.existing_statement
                  ? action === 'revise'
                    ? '确认修订并发布'
                    : '标记为相同'
                  : '确认发布账单'
              }}</el-button
            ><el-button text type="danger" :disabled="busy" @click="reject">忽略候选</el-button>
          </div>
        </div>
        <el-button
          v-else-if="selected.statement_id"
          type="primary"
          @click="router.push(`/statements?detail=${selected.statement_id}`)"
          >查看已关联账单</el-button
        ></template
      ></el-drawer
    >
  </div>
</template>
<style scoped>
.model-metrics {
  display: grid;
  grid-template-columns: 1fr 1fr 1.35fr;
  gap: 18px;
}
.model-metrics article {
  padding: 24px;
  background: var(--paper, #fffdf8);
  border: 1px solid var(--border, #e4e5dd);
  border-top: 3px solid #547060;
}
.model-metrics small {
  display: block;
  color: #6f7971;
}
.model-metrics strong {
  display: block;
  font-family: Georgia, 'Noto Serif SC', serif;
  font-size: 34px;
  margin-top: 12px;
  color: #284e3c;
}
.model-metrics i {
  font-size: 16px;
  font-weight: normal;
  font-style: normal;
}
.model-metrics .model-cost {
  font-size: 19px;
  line-height: 1.5;
}
.model-metrics p {
  font-size: 12px;
  color: #8a6950;
  margin-bottom: 0;
}
.model-config-bar,
.model-review-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}
.model-config-bar h3,
.model-review-head h2 {
  margin: 0 0 8px;
}
.model-config-bar p {
  margin: 0;
  line-height: 1.6;
}
.model-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.model-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}
.model-form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 18px;
}
.model-form-grid :deep(.el-date-editor),
.model-form-grid :deep(.el-input-number) {
  width: 100%;
}
.model-form-grid :deep(.el-input-number) {
  max-width: 240px;
}
.model-switches {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin: 12px 0 20px;
}
.model-filter {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.model-filter :deep(.el-select) {
  display: inline-block;
  width: min(180px, 100%);
  flex: 0 0 auto;
}
.model-warnings,
.model-old {
  background: #f8f0df;
  padding: 18px;
  border-left: 3px solid #aa8450;
  margin: 18px 0;
  line-height: 1.8;
}
.model-warnings ul {
  padding-left: 20px;
  margin: 8px 0 0;
}
.model-old {
  background: #edf3ee;
  border-color: #547060;
}
.model-old p {
  margin: 6px 0;
}
.model-evidence {
  padding: 16px 0;
  border-bottom: 1px solid #e4e5dd;
  overflow-wrap: anywhere;
}
.model-evidence strong,
.model-evidence small {
  display: block;
}
.model-evidence small {
  margin-top: 6px;
  color: #6f7971;
}
blockquote {
  margin: 10px 0;
  padding: 10px 14px;
  border-left: 2px solid #b9c9b8;
  background: #f6f8f3;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.model-publish {
  padding-top: 22px;
  margin-top: 20px;
  border-top: 1px solid #d9ded4;
}
.model-publish :deep(.el-checkbox) {
  height: auto;
  white-space: normal;
  margin: 16px 0;
}
.model-publish :deep(.el-checkbox__label) {
  white-space: normal;
  line-height: 1.7;
}
.model-publish :deep(.el-radio-group) {
  display: flex;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.model-profile {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
}
.model-transaction {
  border-top: 1px solid #d9ded4;
  margin-top: 18px;
  padding-top: 18px;
}
.model-transaction strong {
  display: block;
  margin-bottom: 15px;
}
@media (max-width: 760px) {
  .model-metrics {
    grid-template-columns: 1fr;
    gap: 10px;
  }
  .model-metrics article {
    padding: 18px;
  }
  .model-metrics strong {
    font-size: 28px;
  }
  .model-config-bar,
  .model-review-head {
    align-items: flex-start;
    flex-direction: column;
  }
  .model-form-grid {
    grid-template-columns: 1fr;
  }
  .model-profile {
    align-items: flex-start;
  }
  .model-profile span {
    overflow-wrap: anywhere;
  }
  .model-publish :deep(.el-radio) {
    margin-right: 12px;
  }
}
</style>
