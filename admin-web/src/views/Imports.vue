<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, allItems, uploadFile } from '../api'
import { dateTime, type Page } from '../types'
interface Mailbox {
  id: string
  name: string
  address: string
  username: string
  protocol: 'imap' | 'pop3'
  host: string
  port: number
  tls_mode: 'ssl' | 'starttls'
  folder: string
  active: boolean
  interval_minutes: number
  initial_days: number
  sender_filters: string[]
  subject_filters: string[]
  config_version: number
  secret_configured: boolean
  paused_reason: string | null
  last_error_code: string | null
  last_success_at: string | null
  last_test_at: string | null
}
interface Source {
  id: string
  mailbox_id: string | null
  filename: string
  subject: string
  sender: string
  state: string
  reason: string | null
  size_bytes: number
  created_at: string
  input_id: string | null
  raw_available: boolean
}
interface Health {
  master_key_configured: boolean
  worker_online: boolean
  last_heartbeat: string | null
  ready_sources: number
  tasks: Record<string, number>
}
interface Preview {
  source: Source
  input: {
    state: string
    warnings: string[]
    parts: { name: string; text: string; media_type: string }[]
  }
}
const router = useRouter()
const labels: Record<string, string> = {
  queued: '排队中',
  running: '执行中',
  retry_wait: '等待重试',
  succeeded: '任务完成',
  failed: '失败',
  unsupported: '待人工处理',
  cancelled: '已取消',
  pending: '等待转换',
  ready: '文本已准备',
  partial: '部分文本已准备',
  skipped: '已筛除',
  fetch_failed: '取信失败',
  conversion_failed: '转换失败',
  mail_sync: '邮箱同步',
  mail_test: '连接测试',
  mail_fetch: '失败邮件重取',
  prepare_input: '通用资料转换',
  llm_parse: '大模型识别',
  llm_test: '模型连接测试',
}
const tab = ref('mailboxes'),
  boxes = ref<Mailbox[]>([]),
  sources = ref<Source[]>([])
const health = ref<Health | null>(null),
  loading = ref(false),
  saving = ref(false),
  uploadBusy = ref(false)
const sourceState = ref(''),
  sourcePage = ref(1),
  sourceTotal = ref(0)
const dialog = ref(false),
  editId = ref(''),
  editVersion = ref<number>(),
  backfillBox = ref<Mailbox | null>(null)
const range = ref<string[]>([]),
  preview = ref<Preview | null>(null),
  previewOpen = ref(false)
const actionBusy = ref('')
const reprocessBusy = ref('')
let timer: ReturnType<typeof setInterval> | undefined
const defaults = () => ({
  name: '',
  address: '',
  username: '',
  protocol: 'imap' as 'imap' | 'pop3',
  host: '',
  port: 993,
  tls_mode: 'ssl' as 'ssl' | 'starttls',
  folder: 'INBOX',
  active: true,
  interval_minutes: 30,
  initial_days: 90,
  secret: '',
  senderText: '',
  subjectText: '',
})
const form = reactive(defaults())
function label(value: string) {
  return labels[value] || value
}
function boxName(id: string | null) {
  return boxes.value.find((b) => b.id === id)?.name || '手工上传'
}
function color(state: string): 'success' | 'warning' | 'danger' | 'info' {
  if (['ready', 'succeeded'].includes(state)) return 'success'
  if (['failed', 'fetch_failed', 'conversion_failed'].includes(state)) return 'danger'
  if (['partial', 'unsupported', 'retry_wait'].includes(state)) return 'warning'
  return 'info'
}
async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const [b, h, s] = await Promise.all([
      allItems<Mailbox>('/admin/mailboxes'),
      api<Health>('/admin/import-status'),
      api<Page<Source>>(`/admin/sources?page=${sourcePage.value}&state=${sourceState.value}`),
    ])
    boxes.value = b
    health.value = h
    sources.value = s.items
    sourceTotal.value = s.total
  } catch (e) {
    if (!silent) ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}
function openBox(row?: Mailbox) {
  Object.assign(form, defaults())
  editId.value = row?.id || ''
  editVersion.value = row?.config_version
  if (row)
    Object.assign(form, row, {
      secret: '',
      senderText: row.sender_filters.join('\n'),
      subjectText: row.subject_filters.join('\n'),
    })
  dialog.value = true
}
function clearSecret() {
  form.secret = ''
}
function changeProtocol() {
  form.port =
    form.protocol === 'imap'
      ? form.tls_mode === 'ssl'
        ? 993
        : 143
      : form.tls_mode === 'ssl'
        ? 995
        : 110
  form.folder = 'INBOX'
}
async function saveBox() {
  saving.value = true
  try {
    const {
      name,
      address,
      username,
      protocol,
      host,
      port,
      tls_mode,
      folder,
      active,
      interval_minutes,
      initial_days,
      secret,
    } = form
    const payload = {
      name,
      address,
      username,
      protocol,
      host,
      port,
      tls_mode,
      folder,
      active,
      interval_minutes,
      initial_days,
      ...(secret ? { secret } : {}),
      ...(editId.value ? { expected_version: editVersion.value } : {}),
      sender_filters: form.senderText
        .split('\n')
        .map((v) => v.trim())
        .filter(Boolean),
      subject_filters: form.subjectText
        .split('\n')
        .map((v) => v.trim())
        .filter(Boolean),
    }
    await api(
      '/admin/mailboxes' + (editId.value ? '/' + editId.value : ''),
      editId.value ? 'PATCH' : 'POST',
      payload,
    )
    form.secret = ''
    dialog.value = false
    await load()
    ElMessage.success('邮箱配置已保存，凭证不会回显')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    saving.value = false
  }
}
async function action(box: Mailbox, kind: 'test' | 'sync', dates?: string[]) {
  actionBusy.value = box.id + kind
  try {
    await api(
      `/admin/mailboxes/${box.id}/${kind}`,
      'POST',
      kind === 'sync'
        ? dates?.length === 2
          ? { from_date: dates[0], to_date: dates[1] }
          : {}
        : undefined,
    )
    ElMessage.success('任务已排队；实际结果请查看任务队列')
    backfillBox.value = null
    await router.push('/tasks')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    actionBusy.value = ''
  }
}
function openBackfill(box: Mailbox) {
  range.value = []
  backfillBox.value = box
}
async function confirmBackfill() {
  if (range.value.length !== 2 || !backfillBox.value) {
    ElMessage.warning('请选择开始与结束日期')
    return
  }
  await action(backfillBox.value, 'sync', range.value)
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement,
    file = input.files?.[0]
  if (!file) return
  uploadBusy.value = true
  try {
    const result = await uploadFile<{ source: Source; duplicate: boolean }>(file)
    ElMessage.success(
      result.duplicate
        ? '该资料已经导入，不重复创建来源'
        : '资料已接收，等待 worker 转换；尚未生成账单',
    )
    tab.value = 'sources'
    sourcePage.value = 1
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    uploadBusy.value = false
    input.value = ''
  }
}
async function identify(row: Source, force = false) {
  if (force) {
    try {
      await ElMessageBox.confirm(
        '强制重新识别可能产生新的费用，旧候选会保留。确认继续吗？',
        '重新识别',
        { type: 'warning' },
      )
    } catch {
      return
    }
  }
  try {
    const run = await api<{ id: string }>(`/admin/sources/${row.id}/parse`, 'POST', { force })
    await router.push(`/models?run=${run.id}`)
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}
function warningText(code: string) {
  const messages: Record<string, string> = {
    NO_TEXT: '未提取到可用文字',
    TEXT_DECODE_FAILED: '正文字符解码失败，可尝试重新转换',
    TEXT_CHARSET_FALLBACK: '已使用兼容字符编码，请核对正文',
    TEXT_ALTERNATIVE_FALLBACK: '已改用备用正文，请核对内容',
    PDF_ENCRYPTED: 'PDF 附件已加密，需要先解密',
    ATTACHMENT_UNSUPPORTED: '有附件暂不支持转换',
    TEXT_TRUNCATED: '文字超过上限，内容已截断',
  }
  return messages[code] || code
}
async function reprocess(row: Source, mode: 'reconvert' | 'refetch') {
  if (reprocessBusy.value) return
  const refetch = mode === 'refetch'
  try {
    await ElMessageBox.confirm(
      refetch
        ? '将从原邮箱重新下载这一封邮件并转换，不会重复创建来源。本操作只收取和转换，自动模型识别仍遵循现有配置。原邮件需要仍在服务器上；若编码或文件格式仍不支持，可能仍需补充资料。'
        : '将使用已保存的原文件重新转换，不连接邮箱；自动模型识别仍遵循现有配置。已有模型结果和已确认账单会保留。',
      refetch ? '重新收取并转换' : '重新转换',
      { type: 'warning' },
    )
  } catch {
    return
  }
  reprocessBusy.value = row.id
  try {
    await api(`/admin/sources/${row.id}/reprocess`, 'POST', { mode })
    if (preview.value?.source.id === row.id) {
      previewOpen.value = false
      preview.value = null
    }
    ElMessage.success(refetch ? '重新收取任务已排队，下载后会自动转换' : '重新转换任务已排队')
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    reprocessBusy.value = ''
  }
}
async function showPreview(row: Source) {
  try {
    preview.value = await api<Preview>(`/admin/sources/${row.id}/preview`)
    previewOpen.value = true
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}
function changeSourceFilter() {
  sourcePage.value = 1
  void load()
}
onMounted(() => {
  void load()
  timer = setInterval(() => {
    void load(true)
  }, 5000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
  form.secret = ''
})
</script>
<template>
  <div class="page-heading">
    <div>
      <div class="eyebrow">THE INBOX · SOURCE ARCHIVE</div>
      <h1>先收齐资料，再核对账单。</h1>
      <p>多邮箱各自同步，原信留在服务器。准备资料后可明确交给模型识别，再到「模型与核对」定稿。</p>
    </div>
    <el-button :loading="loading" @click="load(false)">刷新状态</el-button>
  </div>
  <div class="import-health">
    <section class="panel import-stat">
      <span class="eyebrow">WORKER</span>
      <h2>{{ health?.worker_online ? '正在运行' : '尚未连接' }}</h2>
      <p>{{ dateTime(health?.last_heartbeat || '') }}</p>
    </section>
    <section class="panel import-stat">
      <span class="eyebrow">PREPARED SOURCES</span>
      <h2>{{ health?.ready_sources || 0 }} <small>份</small></h2>
      <p>文本已准备 · 不等于账单已解析</p>
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
    v-if="health && !health.master_key_configured"
    class="section-gap"
    type="warning"
    :closable="false"
    title="尚未配置主密钥：可以上传资料，但不能保存邮箱凭证。请先按部署说明配置 BILLS_MASTER_KEY。"
  />
  <el-alert
    v-if="health && !health.worker_online"
    class="section-gap"
    type="info"
    :closable="false"
    title="收信、连接测试和资料转换均由独立 worker 执行；只启动网页和 API 不会处理任务。"
  />
  <section class="panel padded section-gap" v-loading="loading">
    <el-tabs v-model="tab">
      <el-tab-pane label="邮箱配置" name="mailboxes">
        <div class="section-heading">
          <div>
            <h2>每个邮箱，独立进度。</h2>
            <p>仅支持密码或授权码；需要 OAuth 的邮箱暂不支持。</p>
          </div>
          <el-button type="primary" @click="openBox()">新增邮箱</el-button>
        </div>
        <div v-if="!boxes.length" class="import-empty">
          尚未配置邮箱。你也可以先在「来源资料」上传 .eml 或 PDF。
        </div>
        <article v-for="box in boxes" :key="box.id" class="mailbox-entry">
          <div class="mailbox-entry-head">
            <div>
              <h3>{{ box.name }}</h3>
              <p>
                {{ box.address }} · {{ box.protocol.toUpperCase() }} · {{ box.host }}:{{ box.port }}
              </p>
            </div>
            <el-tag :type="!box.active ? 'info' : box.paused_reason ? 'danger' : 'success'">{{
              !box.active ? '已停用' : box.paused_reason ? '因错误暂停' : '已启用'
            }}</el-tag>
          </div>
          <p>
            最近同步：{{ dateTime(box.last_success_at || '') }} · 间隔
            {{ box.interval_minutes }} 分钟 · 凭证{{
              box.secret_configured ? '已加密保存' : '未配置'
            }}
          </p>
          <p v-if="box.last_error_code" class="import-error">
            {{ box.last_error_code }} · 请检查配置，再测试连接。
          </p>
          <div class="import-actions">
            <el-button @click="openBox(box)">编辑邮箱</el-button
            ><el-button :loading="actionBusy === box.id + 'test'" @click="action(box, 'test')"
              >测试连接</el-button
            ><el-button
              :disabled="!box.active || !!box.paused_reason"
              :loading="actionBusy === box.id + 'sync'"
              @click="action(box, 'sync')"
              >立即同步</el-button
            ><el-button :disabled="!box.active || !!box.paused_reason" @click="openBackfill(box)"
              >历史补扫</el-button
            >
          </div>
        </article>
      </el-tab-pane>
      <el-tab-pane label="来源资料" name="sources">
        <div class="import-upload">
          <div>
            <h3>导入原邮件或文本型 PDF</h3>
            <p>
              .eml 上限 20 MiB，PDF 上限 10 MiB。加密、扫描或不支持的附件保留待处理，不使用本地 OCR
              或银行解析规则。
            </p>
          </div>
          <label class="import-file-label"
            >{{ uploadBusy ? '上传中…' : '选择资料文件'
            }}<input
              aria-label="选择资料文件"
              type="file"
              accept=".eml,.pdf"
              :disabled="uploadBusy"
              @change="upload"
          /></label>
        </div>
        <div class="filter-bar import-filters">
          <el-select v-model="sourceState" aria-label="资料状态筛选" @change="changeSourceFilter"
            ><el-option label="全部状态" value="" /><el-option
              v-for="value in [
                'pending',
                'ready',
                'partial',
                'skipped',
                'unsupported',
                'fetch_failed',
                'conversion_failed',
              ]"
              :key="value"
              :label="label(value)"
              :value="value" /></el-select
          ><span class="muted">{{ sourceTotal }} 份来源 · 模型识别默认人工触发</span>
        </div>
        <el-table :data="sources" empty-text="尚无来源资料"
          ><el-table-column label="来源" min-width="190"
            ><template #default="{ row }"
              ><strong>{{ row.subject || row.filename || '无主题邮件' }}</strong>
              <p class="muted">
                {{ boxName(row.mailbox_id) }} · {{ Math.ceil(row.size_bytes / 1024) }} KiB
              </p></template
            ></el-table-column
          ><el-table-column label="状态" width="170"
            ><template #default="{ row }"
              ><el-tag :type="color(row.state)">{{ label(row.state) }}</el-tag
              ><small class="import-code">{{ row.reason }}</small></template
            ></el-table-column
          ><el-table-column label="导入时间" min-width="160"
            ><template #default="{ row }">{{ dateTime(row.created_at) }}</template></el-table-column
          ><el-table-column label="操作" min-width="245"
            ><template #default="{ row }"
              ><el-button text :disabled="!row.input_id" @click="showPreview(row)"
                >安全预览</el-button
              ><el-button
                v-if="row.input_id && ['ready', 'partial'].includes(row.state)"
                text
                @click="identify(row)"
                >模型识别</el-button
              ><el-button
                v-if="row.input_id && ['ready', 'partial'].includes(row.state)"
                text
                @click="identify(row, true)"
                >重新识别</el-button
              ><el-button
                v-if="row.raw_available"
                text
                :disabled="row.state === 'pending' || !!reprocessBusy"
                :loading="reprocessBusy === row.id"
                @click="reprocess(row, 'reconvert')"
                >重新转换</el-button
              ><el-button
                v-if="row.mailbox_id"
                text
                :disabled="row.state === 'pending' || !!reprocessBusy"
                @click="reprocess(row, 'refetch')"
                >重新收取并转换</el-button
              ></template
            ></el-table-column
          ></el-table
        >
        <el-pagination
          class="pagination"
          v-model:current-page="sourcePage"
          :total="sourceTotal"
          :page-size="20"
          layout="prev,pager,next"
          @current-change="load(false)"
        />
      </el-tab-pane>
    </el-tabs>
  </section>
  <el-dialog
    v-model="dialog"
    :title="editId ? '编辑邮箱' : '新增邮箱'"
    width="min(680px, 94vw)"
    @closed="clearSecret"
  >
    <el-form label-position="top" @submit.prevent="saveBox"
      ><div class="import-form-grid">
        <el-form-item label="邮箱名称"><el-input v-model="form.name" maxlength="80" /></el-form-item
        ><el-form-item label="邮箱地址"
          ><el-input v-model="form.address" maxlength="254"
        /></el-form-item>
        <el-form-item label="协议"
          ><el-select v-model="form.protocol" @change="changeProtocol"
            ><el-option label="IMAP（推荐）" value="imap" /><el-option
              label="POP3（需支持 UIDL）"
              value="pop3" /></el-select></el-form-item
        ><el-form-item label="TLS 方式"
          ><el-select v-model="form.tls_mode" @change="changeProtocol"
            ><el-option label="隐式 TLS" value="ssl" /><el-option
              label="STARTTLS"
              value="starttls" /></el-select
        ></el-form-item>
        <el-form-item label="邮箱服务器"
          ><el-input
            v-model="form.host"
            placeholder="例如 imap.example.com，不填 URL" /></el-form-item
        ><el-form-item label="端口"
          ><el-input-number v-model="form.port" :min="1" :max="65535"
        /></el-form-item>
        <el-form-item label="登录用户名"
          ><el-input v-model="form.username" autocomplete="off" /></el-form-item
        ><el-form-item :label="editId ? '密码 / 授权码（留空不修改）' : '密码 / 授权码'"
          ><el-input
            v-model="form.secret"
            type="password"
            autocomplete="new-password"
            show-password
        /></el-form-item>
        <el-form-item label="IMAP 文件夹"
          ><el-input
            v-model="form.folder"
            :disabled="form.protocol === 'pop3'"
            placeholder="INBOX；ASCII 或 modified UTF-7" /></el-form-item
        ><el-form-item label="启用定时同步"><el-switch v-model="form.active" /></el-form-item>
        <el-form-item label="同步间隔（分钟）"
          ><el-input-number v-model="form.interval_minutes" :min="5" :max="1440" /></el-form-item
        ><el-form-item label="首次范围（最近天数）"
          ><el-input-number v-model="form.initial_days" :min="1" :max="3650"
        /></el-form-item>
        <el-form-item label="发件人包含（每行一词）"
          ><el-input
            v-model="form.senderText"
            type="textarea"
            :rows="3"
            placeholder="留空不过滤" /></el-form-item
        ><el-form-item label="主题包含（每行一词）"
          ><el-input
            v-model="form.subjectText"
            type="textarea"
            :rows="3"
            placeholder="仅筛选候选邮件，不推导银行或金额"
        /></el-form-item>
      </div>
      <p class="muted">
        修改目标服务器、用户名或文件夹会建立新同步身份并保留旧资料；修改筛选词后如需处理旧邮件，请显式补扫。仅连接公网邮箱，不能跳过证书校验。
      </p></el-form
    >
    <template #footer
      ><el-button @click="dialog = false">取消</el-button
      ><el-button type="primary" :loading="saving" @click="saveBox">保存邮箱</el-button></template
    >
  </el-dialog>
  <el-dialog
    :model-value="!!backfillBox"
    title="历史补扫"
    width="min(530px,94vw)"
    @close="backfillBox = null"
    ><p>
      补扫 {{ backfillBox?.name }} 的指定范围。POP3
      在本地筛选日期，可能仍需下载邮件；被其他客户端删除的历史原信不能保证找回。
    </p>
    <el-date-picker
      v-model="range"
      class="section-gap"
      type="daterange"
      value-format="YYYY-MM-DD"
      start-placeholder="开始日期"
      end-placeholder="结束日期"
    /><template #footer
      ><el-button type="primary" @click="confirmBackfill">提交补扫</el-button></template
    ></el-dialog
  >
  <el-drawer v-model="previewOpen" title="安全资料预览 · 不是账单解析结果" size="min(760px,96vw)"
    ><template v-if="preview"
      ><el-alert
        type="info"
        :closable="false"
        title="仅显示转换后的纯文本，不执行邮件 HTML、不加载外部资源。账单候选请在「模型与核对」查看。"
      />
      <p class="section-gap">{{ preview.source.subject || preview.source.filename }}</p>
      <p v-if="preview.input.warnings.length" class="import-error">
        注意：{{ preview.input.warnings.map(warningText).join(' / ') }}
      </p>
      <div class="section-gap">
        <el-button
          v-if="preview.source.raw_available"
          :disabled="preview.source.state === 'pending' || !!reprocessBusy"
          :loading="reprocessBusy === preview.source.id"
          @click="reprocess(preview.source, 'reconvert')"
          >重新转换</el-button
        >
        <el-button
          v-if="preview.source.mailbox_id"
          :disabled="preview.source.state === 'pending' || !!reprocessBusy"
          @click="reprocess(preview.source, 'refetch')"
          >重新收取并转换</el-button
        >
      </div>
      <section v-for="(part, index) in preview.input.parts" :key="index" class="section-gap">
        <h3>{{ part.name }} · {{ part.media_type }}</h3>
        <pre class="source-text">{{ part.text }}</pre>
      </section>
      <p v-if="!preview.input.parts.length">
        没有可转换文本。可先尝试重新转换或重新收取邮件；若仍无文本，请上传已解密的文本型 PDF
        或手工录入账单。
      </p></template
    ></el-drawer
  >
</template>
