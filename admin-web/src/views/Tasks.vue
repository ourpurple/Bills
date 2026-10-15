<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, allItems } from '../api'
import { dateTime, type Page } from '../types'
interface Job {
  id: string
  kind: string
  state: string
  mailbox_id: string | null
  source_id: string | null
  attempts: number
  max_attempts: number
  progress: number
  error_code: string | null
  error_message: string | null
  created_at: string
  finished_at: string | null
  available_at: string
  result: Record<string, unknown>
}
interface Mailbox {
  id: string
  name: string
}
interface Health {
  worker_online: boolean
  last_heartbeat: string | null
  tasks: Record<string, number>
}
const labels: Record<string, string> = {
  queued: '排队中',
  running: '执行中',
  retry_wait: '等待重试',
  succeeded: '任务完成',
  failed: '失败',
  unsupported: '待人工处理',
  cancelled: '已取消',
  mail_sync: '邮箱同步',
  mail_test: '连接测试',
  mail_fetch: '失败邮件重取',
  prepare_input: '通用资料转换',
  llm_parse: '大模型识别',
  llm_test: '模型连接测试',
}
const boxes = ref<Mailbox[]>([]),
  jobs = ref<Job[]>([])
const health = ref<Health | null>(null),
  loading = ref(false),
  clearing = ref(false)
const jobState = ref(''),
  jobPage = ref(1),
  jobTotal = ref(0)
const detail = ref<Job | null>(null),
  detailOpen = ref(false)
const pendingTotal = computed(() =>
  ['queued', 'running', 'retry_wait'].reduce(
    (total, state) => total + (health.value?.tasks[state] || 0),
    0,
  ),
)
const clearableTotal = computed(() =>
  ['succeeded', 'failed', 'unsupported', 'cancelled'].reduce(
    (total, state) => total + (health.value?.tasks[state] || 0),
    0,
  ),
)
const diagnostic = computed(() => {
  const value = detail.value?.result.diagnostics
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
})
const modelFailure = computed(
  () => !!detail.value?.error_code && ['llm_parse', 'llm_test'].includes(detail.value.kind),
)
const stages: Record<string, string> = {
  dns: '解析模型服务器地址',
  request: '连接模型服务 / 发送请求',
  response_headers: '等待模型服务响应',
  response_body: '接收模型响应内容',
}
const timeoutKinds: Record<string, string> = {
  request: '连接 / 初始响应超时',
  idle: '流式响应空闲超时',
  total: '流式请求总时限',
}
let timer: ReturnType<typeof setInterval> | undefined
let loadSequence = 0
function label(value: string) {
  return labels[value] || value
}
function taskSource(row: Job) {
  if (row.mailbox_id)
    return boxes.value.find((box) => box.id === row.mailbox_id)?.name || '邮箱任务'
  if (row.kind === 'llm_test') return '模型配置'
  if (row.source_id) return '来源资料'
  return '系统任务'
}
function color(state: string): 'success' | 'warning' | 'danger' | 'info' {
  if (state === 'succeeded') return 'success'
  if (state === 'failed') return 'danger'
  if (['unsupported', 'retry_wait'].includes(state)) return 'warning'
  return 'info'
}
async function load(silent = false) {
  const sequence = ++loadSequence
  if (!silent) loading.value = true
  try {
    const [b, h, j] = await Promise.all([
      allItems<Mailbox>('/admin/mailboxes'),
      api<Health>('/admin/import-status'),
      api<Page<Job>>(`/admin/tasks?page=${jobPage.value}&state=${jobState.value}`),
    ])
    if (sequence !== loadSequence) return
    boxes.value = b
    health.value = h
    jobs.value = j.items
    jobTotal.value = j.total
  } catch (e) {
    if (!silent && sequence === loadSequence) ElMessage.error((e as Error).message)
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}
async function retry(row: Job) {
  try {
    await ElMessageBox.confirm(
      '将创建新的重试任务，原失败记录保留。未修正的格式或凭证问题仍可能失败。',
      '重试任务',
    )
    await api(`/admin/tasks/${row.id}/retry`, 'POST')
    await load()
    ElMessage.success('重试已排队')
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error((e as Error).message)
  }
}
async function clearLogs() {
  if (clearing.value) return
  clearing.value = true
  try {
    await ElMessageBox.confirm(
      '将清除所有分页和筛选条件下已完成、失败、待人工处理及已取消任务的列表记录，清除后无法从列表查看详情或重试。排队中、执行中及等待重试的任务不受影响，账单、来源资料和模型调用记录会保留。是否继续？',
      '清除任务日志',
      { type: 'warning', confirmButtonText: '确认清除', cancelButtonText: '取消' },
    )
    // Ignore any refresh that started before the clear request.
    ++loadSequence
    const result = await api<{ cleared: number }>('/admin/tasks/clear-logs', 'POST')
    detailOpen.value = false
    detail.value = null
    jobPage.value = 1
    await load()
    if (result.cleared) ElMessage.success(`已清除 ${result.cleared} 条任务日志`)
    else ElMessage.info('暂无可清除的任务日志')
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error((e as Error).message)
  } finally {
    clearing.value = false
  }
}
function showJob(row: Job) {
  detail.value = row
  detailOpen.value = true
}
function changeJobFilter() {
  jobPage.value = 1
  void load()
}
onMounted(() => {
  void load()
  timer = setInterval(() => {
    if (!clearing.value) void load(true)
  }, 5000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>
<template>
  <div class="page-heading">
    <div>
      <div class="eyebrow">SYSTEM · TASK QUEUE</div>
      <h1>所有任务，统一查看。</h1>
      <p>集中管理邮箱同步、资料转换与模型识别等系统后台任务，查看进度、失败原因并重试。</p>
    </div>
    <el-button :loading="loading" :disabled="clearing" @click="load(false)">刷新状态</el-button>
  </div>
  <div class="import-health">
    <section class="panel import-stat">
      <span class="eyebrow">WORKER</span>
      <h2>{{ health?.worker_online ? '正在运行' : '尚未连接' }}</h2>
      <p>{{ dateTime(health?.last_heartbeat || '') }}</p>
    </section>
    <section class="panel import-stat">
      <span class="eyebrow">IN PROGRESS</span>
      <h2>{{ pendingTotal }} <small>项</small></h2>
      <p>排队、执行与等待重试中的任务</p>
    </section>
    <section class="panel import-stat">
      <span class="eyebrow">NEEDS ATTENTION</span>
      <h2>
        {{ (health?.tasks.failed || 0) + (health?.tasks.unsupported || 0) }} <small>项</small>
      </h2>
      <p>失败与不支持任务，需要检查</p>
    </section>
  </div>
  <el-alert
    v-if="health && !health.worker_online"
    class="section-gap"
    type="info"
    :closable="false"
    title="后台任务由独立 worker 执行；只启动网页和 API 不会处理任务。"
  />
  <section class="panel padded section-gap" v-loading="loading">
    <div class="section-heading task-queue-heading">
      <div>
        <h2>任务队列</h2>
        <p>跨模块统一管理，不局限于邮件中心。</p>
      </div>
      <el-button
        type="danger"
        plain
        :loading="clearing"
        :disabled="loading || !clearableTotal"
        @click="clearLogs"
        >清除任务日志</el-button
      >
    </div>
    <div class="filter-bar import-filters">
      <el-select
        v-model="jobState"
        :disabled="clearing"
        aria-label="任务状态筛选"
        @change="changeJobFilter"
        ><el-option label="全部状态" value="" /><el-option
          v-for="value in [
            'queued',
            'running',
            'retry_wait',
            'succeeded',
            'failed',
            'unsupported',
            'cancelled',
          ]"
          :key="value"
          :label="label(value)"
          :value="value" /></el-select
      ><span class="muted">系统全部后台任务 · 每 5 秒刷新 · 可清除已结束任务日志</span>
    </div>
    <el-table :data="jobs" empty-text="尚无任务"
      ><el-table-column label="时间" width="220"
        ><template #default="{ row }"
          ><span>创建：{{ dateTime(row.created_at) }}</span>
          <p class="muted">结束：{{ dateTime(row.finished_at) }}</p></template
        ></el-table-column
      ><el-table-column label="任务" min-width="165"
        ><template #default="{ row }"
          ><strong>{{ label(row.kind) }}</strong>
          <p>{{ taskSource(row) }}</p></template
        ></el-table-column
      ><el-table-column label="状态" width="120"
        ><template #default="{ row }"
          ><el-tag :type="color(row.state)">{{ label(row.state) }}</el-tag></template
        ></el-table-column
      ><el-table-column label="进度 / 尝试" width="160"
        ><template #default="{ row }"
          >{{ row.progress }} 已处理 / {{ row.attempts }} 次</template
        ></el-table-column
      ><el-table-column label="错误" min-width="200"
        ><template #default="{ row }"
          ><span class="import-code">{{ row.error_code || '—' }}</span>
          <p>{{ row.error_message }}</p></template
        ></el-table-column
      ><el-table-column label="操作" width="135"
        ><template #default="{ row }"
          ><el-button text @click="showJob(row)">详情</el-button
          ><el-button
            v-if="['failed', 'unsupported', 'cancelled'].includes(row.state)"
            text
            type="primary"
            @click="retry(row)"
            >重试</el-button
          ></template
        ></el-table-column
      ></el-table
    >
    <el-pagination
      class="pagination"
      v-model:current-page="jobPage"
      :total="jobTotal"
      :disabled="clearing"
      :page-size="20"
      layout="prev,pager,next"
      @current-change="load(false)"
    />
  </section>
  <el-dialog v-model="detailOpen" title="任务详情" width="min(600px,94vw)"
    ><template v-if="detail"
      ><p>{{ label(detail.kind) }} · {{ label(detail.state) }}</p>
      <p>创建时间：{{ dateTime(detail.created_at) }}</p>
      <p>结束时间：{{ dateTime(detail.finished_at) }}</p>
      <p>下次可执行：{{ dateTime(detail.available_at) }}</p>
      <p class="import-error">{{ detail.error_code }} {{ detail.error_message }}</p>
      <el-descriptions v-if="diagnostic" title="失败诊断" :column="1" border>
        <el-descriptions-item v-if="diagnostic.stage" label="失败阶段">
          {{ stages[String(diagnostic.stage)] || '未知阶段' }}
        </el-descriptions-item>
        <el-descriptions-item v-if="typeof diagnostic.http_status === 'number'" label="HTTP 状态码">
          {{ diagnostic.http_status }}
        </el-descriptions-item>
        <el-descriptions-item v-if="typeof diagnostic.elapsed_ms === 'number'" label="请求耗时">
          {{ (diagnostic.elapsed_ms / 1000).toFixed(1) }} 秒
        </el-descriptions-item>
        <el-descriptions-item
          v-if="typeof diagnostic.timeout_seconds === 'number'"
          label="配置超时"
        >
          {{ diagnostic.timeout_seconds }} 秒
        </el-descriptions-item>
        <el-descriptions-item v-if="diagnostic.timeout_kind" label="超时类型">
          {{ timeoutKinds[String(diagnostic.timeout_kind)] || '未知类型' }}
        </el-descriptions-item>
        <el-descriptions-item
          v-if="typeof diagnostic.total_timeout_seconds === 'number'"
          label="流式总时限"
        >
          {{ diagnostic.total_timeout_seconds }} 秒
        </el-descriptions-item>
        <el-descriptions-item v-if="typeof diagnostic.request_bytes === 'number'" label="请求大小">
          {{ (diagnostic.request_bytes / 1024).toFixed(1) }} KB
        </el-descriptions-item>
      </el-descriptions>
      <el-alert
        v-else-if="modelFailure && Object.keys(detail.result).length === 0"
        type="info"
        :closable="false"
        title="该历史任务未记录详细诊断，更新后重试可查看失败阶段和耗时。"
      />
      <pre v-else class="source-text">{{ JSON.stringify(detail.result, null, 2) }}</pre>
      <p v-if="modelFailure" class="muted">
        建议先运行模型连接测试。若连接测试成功、账单识别仍失败，请结合耗时检查模型或网关超时及输入长度；减少输入可能遗漏账单内容，需人工核对。
      </p>
    </template></el-dialog
  >
</template>

<style scoped>
@media (max-width: 600px) {
  .task-queue-heading {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
