<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { api, allItems } from '../api'
import { modeText, type Bank, type Holder, type Account, type Card } from '../types'
const banks = ref<Bank[]>([]),
  holders = ref<Holder[]>([]),
  accounts = ref<Account[]>([])
const tab = ref('accounts'),
  loading = ref(false),
  dialog = ref(false),
  saving = ref(false),
  entity = ref(''),
  editing = ref('')
const deleting = ref('')
const form = ref<Record<string, any>>({})
const title = computed(
  () =>
    `${editing.value ? '编辑' : '新增'}${({ bank: '银行', holder: '持卡人', account: '还款账户', card: '卡片' } as Record<string, string>)[entity.value]}`,
)
const modes = [
  { value: 'merged', label: '合并还款' },
  { value: 'independent', label: '独立还款' },
]
async function load() {
  loading.value = true
  try {
    const result = await Promise.all([
      allItems<Bank>('/admin/banks'),
      allItems<Holder>('/admin/cardholders'),
      allItems<Account>('/admin/accounts'),
    ])
    ;[banks.value, holders.value, accounts.value] = result as [Bank[], Holder[], Account[]]
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}
function open(kind: string, row?: any, accountId?: string) {
  entity.value = kind
  editing.value = row?.id || ''
  form.value = row
    ? { ...row }
    : {
        name: '',
        code: '',
        default_mode: 'merged',
        bank_id: '',
        cardholder_id: '',
        mode: null,
        statement_day: null,
        account_id: accountId || '',
        last_four: '',
        active: true,
      }
  dialog.value = true
}
async function save() {
  if (!form.value.name?.trim()) {
    ElMessage.warning('请填写名称')
    return
  }
  const f = form.value
  if (entity.value === 'account' && !editing.value && (!f.bank_id || !f.cardholder_id)) {
    ElMessage.warning('请选择银行和持卡人')
    return
  }
  const creates: Record<string, unknown> = {
    bank: { code: f.code, name: f.name, default_mode: f.default_mode },
    holder: { name: f.name },
    account: {
      name: f.name,
      bank_id: f.bank_id,
      cardholder_id: f.cardholder_id,
      mode: f.mode || null,
      statement_day: f.statement_day || null,
    },
    card: { name: f.name, account_id: f.account_id, last_four: f.last_four },
  }
  const updates: Record<string, unknown> = {
    bank: { name: f.name, default_mode: f.default_mode, active: f.active },
    holder: { name: f.name, active: f.active },
    account: { name: f.name, active: f.active, statement_day: f.statement_day || null },
    card: { name: f.name, active: f.active },
  }
  const paths: Record<string, string> = {
    bank: 'banks',
    holder: 'cardholders',
    account: 'accounts',
    card: 'cards',
  }
  saving.value = true
  try {
    await api(
      `/admin/${paths[entity.value]}${editing.value ? '/' + editing.value : ''}`,
      editing.value ? 'PATCH' : 'POST',
      editing.value ? updates[entity.value] : creates[entity.value],
    )
    dialog.value = false
    ElMessage.success('已保存')
    await load()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    saving.value = false
  }
}
async function remove(kind: 'account' | 'card', row: Account | Card) {
  if (deleting.value) return
  const isAccount = kind === 'account'
  const label = isAccount ? '账户' : '卡片'
  const message = isAccount
    ? `确定删除账户“${row.name}”及其下的所有卡片吗？此操作不可恢复。有关联账单或导入记录的账户不能删除，请改为停用。`
    : `确定删除卡片“${row.name}”（尾号 ${(row as Card).last_four}）吗？此操作不可恢复，所属账户和历史账单会保留。`
  // Lock before confirmation so repeated clicks cannot open competing delete prompts.
  deleting.value = row.id
  try {
    await ElMessageBox.confirm(message, `删除${label}`, {
      type: 'warning',
      confirmButtonText: '确认删除',
      cancelButtonText: '取消',
      confirmButtonClass: 'el-button--danger',
    })
    await api(`/admin/${isAccount ? 'accounts' : 'cards'}/${row.id}`, 'DELETE')
    if (editing.value === row.id && entity.value === kind) dialog.value = false
    ElMessage.success(`${label}已删除`)
    await load()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error((e as Error).message)
  } finally {
    deleting.value = ''
  }
}
onMounted(load)
</script>
<template>
  <div class="page-heading">
    <div>
      <div class="eyebrow">ACCOUNT COLLECTION</div>
      <h1>卡片有归属，账单不重复。</h1>
      <p>选择持卡人与银行，直接创建实际的还款账户。</p>
    </div>
    <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
  </div>
  <section class="panel padded" v-loading="loading">
    <el-tabs v-model="tab"
      ><el-tab-pane label="还款账户" name="accounts"
        ><div class="toolbar">
          <p>合并模式：多卡共用一份账单。独立模式：每个账户一张有效卡。</p>
          <el-button type="primary" :icon="Plus" @click="open('account')">新增账户</el-button>
        </div>
        <el-empty v-if="!accounts.length" description="先添加持卡人，再选择银行创建还款账户。" />
        <div v-if="accounts.length" class="account-list">
          <div class="account-list-header" aria-hidden="true">
            <span>账户 / 银行 / 持卡人</span>
            <span>还款模式</span>
            <span>卡片 · 尾号</span>
            <span>预计出账日</span>
            <span class="account-actions-label">操作</span>
          </div>
          <div class="account-rows">
            <article
              v-for="a in accounts"
              :key="a.id"
              class="account-card"
              :class="{ inactive: !a.effective_active }"
            >
              <div class="account-card-head">
                <div class="bank-badge" :class="a.bank_code">{{ a.bank_name.slice(0, 1) }}</div>
                <div class="account-identity">
                  <h3>{{ a.name }}</h3>
                  <span>{{ a.bank_name }} · {{ a.cardholder_name }}</span>
                </div>
              </div>
              <div class="account-mode">
                <el-tag size="small" :type="a.effective_active ? 'success' : 'info'">{{
                  a.effective_active ? modeText(a.mode) : '已停用（含上级）'
                }}</el-tag>
              </div>
              <div class="card-chips">
                <el-button
                  v-for="c in a.cards"
                  :key="c.id"
                  plain
                  size="small"
                  :class="{ inactive: !c.active }"
                  :title="`${c.name} · ${c.last_four}${c.active ? '' : '（停用）'}`"
                  @click="open('card', c)"
                  >{{ c.name }} · {{ c.last_four }}{{ c.active ? '' : '（停用）' }}</el-button
                ><span v-if="!a.cards.length" class="muted">暂未添加卡片</span>
              </div>
              <div class="account-statement-day">
                <span v-if="a.statement_day"
                  >每月 <strong>{{ a.statement_day }}</strong> 日出账</span
                >
                <span v-else class="muted">出账日未设置</span>
              </div>
              <div class="account-actions">
                <el-button text size="small" @click="open('account', a)">编辑</el-button
                ><el-button
                  text
                  size="small"
                  type="primary"
                  :disabled="!a.effective_active"
                  @click="open('card', undefined, a.id)"
                  >添加卡片</el-button
                ><el-button
                  text
                  size="small"
                  type="danger"
                  :loading="deleting === a.id"
                  :disabled="!!deleting && deleting !== a.id"
                  @click="remove('account', a)"
                  >删除</el-button
                >
              </div>
            </article>
          </div>
        </div></el-tab-pane
      >
      <el-tab-pane label="持卡人" name="holders"
        ><div class="toolbar">
          <p>持卡人是账单归属资料，不是独立登录用户。</p>
          <el-button type="primary" :icon="Plus" @click="open('holder')">新增持卡人</el-button>
        </div>
        <el-table :data="holders"
          ><el-table-column prop="name" label="昵称" /><el-table-column label="状态"
            ><template #default="{ row }">{{
              row.active ? '有效' : '已停用'
            }}</template></el-table-column
          ><el-table-column label="操作" width="100"
            ><template #default="{ row }"
              ><el-button text @click="open('holder', row)">编辑</el-button></template
            ></el-table-column
          ></el-table
        ></el-tab-pane
      >
      <el-tab-pane label="银行配置" name="banks"
        ><div class="toolbar">
          <p>默认模式只影响新建账户，不修改历史数据。实际模式可在创建账户时覆盖。</p>
          <el-button type="primary" :icon="Plus" @click="open('bank')">新增银行</el-button>
        </div>
        <el-table :data="banks"
          ><el-table-column prop="name" label="银行" /><el-table-column
            prop="code"
            label="代码"
          /><el-table-column label="默认模式"
            ><template #default="{ row }"
              ><el-tag :type="row.default_mode === 'merged' ? 'success' : 'info'">{{
                modeText(row.default_mode)
              }}</el-tag></template
            ></el-table-column
          ><el-table-column label="状态" width="100"
            ><template #default="{ row }">{{
              row.active ? '有效' : '已停用'
            }}</template></el-table-column
          ><el-table-column label="操作" width="100"
            ><template #default="{ row }"
              ><el-button text @click="open('bank', row)">编辑</el-button></template
            ></el-table-column
          ></el-table
        ></el-tab-pane
      ></el-tabs
    >
  </section>
  <el-dialog v-model="dialog" :title="title" width="min(520px, 94vw)"
    ><el-form label-position="top" @submit.prevent="save"
      ><el-form-item label="名称" required
        ><el-input v-model="form.name" maxlength="80" /></el-form-item
      ><el-form-item v-if="entity === 'bank' && !editing" label="银行代码（小写字母开头）" required
        ><el-input v-model="form.code" maxlength="32" /></el-form-item
      ><el-form-item v-if="entity === 'bank'" label="默认还款模式"
        ><el-select v-model="form.default_mode"
          ><el-option
            v-for="m in modes"
            :key="m.value"
            :label="m.label"
            :value="m.value" /></el-select></el-form-item
      ><template v-if="entity === 'account' && !editing"
        ><el-form-item label="银行" required
          ><el-select v-model="form.bank_id" filterable
            ><el-option
              v-for="b in banks.filter((b) => b.active)"
              :key="b.id"
              :label="b.name"
              :value="b.id" /></el-select></el-form-item
        ><el-form-item label="持卡人" required
          ><el-select v-model="form.cardholder_id" filterable
            ><el-option
              v-for="h in holders.filter((h) => h.active)"
              :key="h.id"
              :label="h.name"
              :value="h.id" /></el-select></el-form-item></template
      ><template v-if="entity === 'account'"
        ><el-form-item v-if="!editing" label="还款模式"
          ><el-select v-model="form.mode" clearable placeholder="沿用银行默认模式"
            ><el-option
              v-for="m in modes"
              :key="m.value"
              :label="m.label"
              :value="m.value" /></el-select></el-form-item
        ><el-form-item label="预计账单日（仅用于缺失提示）"
          ><el-input-number v-model="form.statement_day" :min="1" :max="31"
        /></el-form-item>
        <p v-if="editing" class="form-hint">
          已有账户不能直接切换模式或归属。请新建账户并停用旧账户。
        </p></template
      ><el-form-item v-if="entity === 'card' && !editing" label="卡片尾号（4 位）" required
        ><el-input
          v-model="form.last_four"
          maxlength="4"
          placeholder="仅保存尾号，不收集完整卡号" /></el-form-item
      ><el-form-item v-if="editing" label="有效状态"
        ><el-switch
          v-model="form.active"
          active-text="有效"
          inactive-text="停用" /></el-form-item></el-form
    ><template #footer
      ><el-button
        v-if="entity === 'card' && editing"
        type="danger"
        plain
        :loading="deleting === editing"
        :disabled="saving || (!!deleting && deleting !== editing)"
        @click="remove('card', form as Card)"
        >删除卡片</el-button
      ><el-button :disabled="!!deleting" @click="dialog = false">取消</el-button
      ><el-button type="primary" :loading="saving" :disabled="!!deleting" @click="save"
        >保存</el-button
      ></template
    ></el-dialog
  >
</template>

<style scoped>
.account-list {
  container-type: inline-size;
  padding: 0 22px 16px;
}
.account-list-header,
.account-card {
  display: grid;
  grid-template-columns: minmax(210px, 1.2fr) 112px minmax(140px, 1fr) 126px 188px;
  align-items: center;
  gap: 16px;
  padding: 14px 16px;
}
.account-list-header {
  color: #7b8779;
  font-size: 12px;
  padding-block: 10px;
}
.account-rows {
  overflow: hidden;
  border: 1px solid #e0e5d8;
  border-radius: 10px;
  background: #f7f9f0;
}
.account-card {
  border: 0;
  border-radius: 0;
  min-width: 0;
  background: transparent;
}
.account-card + .account-card {
  border-top: 1px solid #e0e5d8;
}
.account-card:hover {
  background: #f0f4e9;
}
.account-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.bank-badge {
  flex: 0 0 36px;
  width: 36px;
  height: 36px;
  font-size: 20px;
  border-radius: 8px;
}
.account-identity {
  min-width: 0;
  overflow-wrap: anywhere;
}
.account-identity h3 {
  font-size: 14px;
  line-height: 1.5;
}
.account-identity > span {
  color: #737e74;
  font-size: 12px;
  line-height: 1.6;
}
.account-mode {
  min-width: 0;
}
.card-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
  margin: 0;
}
.card-chips .el-button {
  max-width: 100%;
  height: 26px;
  margin: 0;
  padding: 4px 8px;
  font-size: 12px;
}
.card-chips :deep(.el-button > span) {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
}
.card-chips > .muted,
.account-statement-day {
  font-size: 12px;
}
.account-statement-day {
  color: #737e74;
  white-space: nowrap;
}
.account-statement-day strong {
  color: #253932;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.account-actions-label {
  text-align: right;
}
.account-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  flex-wrap: wrap;
  gap: 2px;
}
.account-actions .el-button {
  margin: 0;
  padding-inline: 8px;
  min-height: 28px;
}
@container (max-width: 900px) {
  .account-list-header {
    display: none;
  }
  .account-card {
    grid-template-columns: minmax(0, 1fr) auto 188px;
    gap: 10px 16px;
  }
  .account-card-head {
    grid-column: 1;
    grid-row: 1;
  }
  .account-mode {
    grid-column: 2;
    grid-row: 1;
  }
  .card-chips {
    grid-column: 1 / 3;
    grid-row: 2;
  }
  .account-statement-day {
    grid-column: 3;
    grid-row: 2;
    text-align: right;
  }
  .account-actions {
    grid-column: 3;
    grid-row: 1;
  }
}
@container (max-width: 600px) {
  .account-card {
    grid-template-columns: minmax(0, 1fr) auto;
    padding: 12px;
    gap: 10px;
  }
  .account-statement-day {
    grid-column: 1;
    grid-row: 3;
    text-align: left;
  }
  .account-actions {
    grid-column: 1 / 3;
    grid-row: 4;
  }
}
@container (min-width: 380px) and (max-width: 600px) {
  .account-actions {
    grid-column: 2;
    grid-row: 3;
  }
}
@media (max-width: 520px) {
  .account-list {
    padding: 0 0 8px;
  }
}
</style>
