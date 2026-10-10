<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { dateTime, type Page } from '../types'
interface Device {
  id: string
  name: string
  expires_at: string
  revoked_at: string | null
}
interface Event {
  id: string
  action: string
  object_id: string
  created_at: string
}
const devices = ref<Device[]>([]),
  events = ref<Event[]>([]),
  code = ref(''),
  busy = ref(false),
  loading = ref(false),
  eventTotal = ref(0),
  eventPage = ref(1)
async function loadEvents() {
  const data = await api<Page<Event>>(`/admin/audit-events?page=${eventPage.value}`)
  events.value = data.items
  eventTotal.value = data.total
}
async function load() {
  loading.value = true
  try {
    devices.value = (await api<Page<Device>>('/admin/devices')).items
    await loadEvents()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}
async function pair() {
  busy.value = true
  try {
    code.value = (await api<{ code: string }>('/admin/devices/pairing-codes', 'POST')).code
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    busy.value = false
  }
}
async function revoke(id: string) {
  try {
    await ElMessageBox.confirm('撤销后该设备无法继续访问账单。', '撤销设备')
    await api(`/admin/devices/${id}/revoke`, 'POST')
    await load()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error((e as Error).message)
  }
}
onMounted(load)
</script>
<template>
  <div class="page-heading">
    <div>
      <div class="eyebrow">PRIVATE BY DESIGN</div>
      <h1>你的数据，你来掌握。</h1>
      <p>明确的访问边界，可追溯的操作记录。</p>
    </div>
  </div>
  <section class="panel padded" v-loading="loading">
    <div class="section-heading">
      <div>
        <h2>设备访问</h2>
        <p class="muted">手机凭证仅能读账单、写还款与计划，不能管理邮箱或银行账户。</p>
      </div>
      <el-button type="primary" :loading="busy" @click="pair">生成配对码</el-button>
    </div>
    <el-alert v-if="code" type="success" :closable="false"
      ><template #title>一次性配对码 · 10 分钟有效</template
      ><code class="pairing-code">{{ code }}</code>
      <p>仅在可信设备上使用，配对成功后失效。在安卓 App 中输入服务地址和此配对码连接。</p></el-alert
    ><el-table :data="devices" empty-text="尚无配对设备"
      ><el-table-column prop="name" label="设备名称" /><el-table-column label="有效期"
        ><template #default="{ row }">{{ dateTime(row.expires_at) }}</template></el-table-column
      ><el-table-column label="状态"
        ><template #default="{ row }">{{
          row.revoked_at ? '已撤销' : '未撤销'
        }}</template></el-table-column
      ><el-table-column label="操作" width="100"
        ><template #default="{ row }"
          ><el-button v-if="!row.revoked_at" text type="danger" @click="revoke(row.id)"
            >撤销</el-button
          ></template
        ></el-table-column
      ></el-table
    >
  </section>
  <section class="panel padded section-gap">
    <div class="section-heading">
      <h2>操作记录</h2>
      <span class="muted">不包含密码、密钥或邮件正文</span>
    </div>
    <el-table :data="events"
      ><el-table-column label="时间" min-width="150"
        ><template #default="{ row }">{{ dateTime(row.created_at) }}</template></el-table-column
      ><el-table-column prop="action" label="操作" min-width="150" /><el-table-column
        prop="object_id"
        label="对象标识"
        min-width="180" /></el-table
    ><el-pagination
      class="pagination"
      v-model:current-page="eventPage"
      :total="eventTotal"
      :page-size="20"
      layout="prev,pager,next"
      @current-change="loadEvents"
    />
  </section>
  <section class="development-note">
    <div class="eyebrow">NEXT MILESTONE</div>
    <h3>模型核对与安卓展示已实现，提醒继续开发。</h3>
    <p>
      邮箱与导入页面支持多邮箱、资料转换与任务管理，需要启动独立
      worker。模型识别须单独配置并启用，识别结果默认人工核对；安卓仅展示和记录还款。提醒尚未实现，保存计划不会发送通知，资料准备完成不代表账单已经生成。
    </p>
  </section>
</template>
