<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { api, allItems } from '../api'
import { amountText, modeText, type Bank, type Holder, type Account, type Card } from '../types'
const banks = ref<Bank[]>([]),
  holders = ref<Holder[]>([]),
  accounts = ref<Account[]>([])
const sortedAccounts = computed(() =>
  [...accounts.value].sort((a, b) => (a.statement_day ?? 32) - (b.statement_day ?? 32)),
)
const tab = ref('accounts'),
  loading = ref(false),
  dialog = ref(false),
  saving = ref(false),
  entity = ref(''),
  editing = ref('')
const deleting = ref('')
const cardDetailsVisible = ref(false)
const privateDetails = ref<{ card_number: string | null; cvv: string | null } | null>(null)
const privateLoading = ref(false)
const editorPrivateLoading = ref(false)
let privateRequest = 0
let editorPrivateRequest = 0
const cardTypes = [
  { value: 'credit', label: '信用卡' },
  { value: 'debit', label: '借记卡' },
]
const cardNetworks = [
  { value: 'unionpay', label: '银联' },
  { value: 'visa', label: 'VISA' },
  { value: 'mastercard', label: '万事达' },
  { value: 'amex', label: 'AE（美国运通）' },
]
function optionLabel(options: { value: string; label: string }[], value?: string | null) {
  return options.find((option) => option.value === value)?.label || '未设置'
}
const selectedAccountId = ref('')
const selectedCardId = ref('')
const selectedAccount = computed(() =>
  accounts.value.find((account) => account.id === selectedAccountId.value),
)
const selectedCard = computed(() =>
  selectedAccount.value?.cards.find((card) => card.id === selectedCardId.value),
)
const displayCardNumber = computed(() => {
  const number = privateDetails.value?.card_number
  if (number) return number.match(/[0-9]{1,4}/g)?.join(' ') || number
  return selectedCard.value?.has_card_number
    ? `•••• •••• •••• ${selectedCard.value.last_four}`
    : '未填写'
})
const managedCards = computed(() =>
  entity.value === 'account' && editing.value
    ? (accounts.value.find((account) => account.id === editing.value)?.cards ?? [])
    : [],
)
function viewCard(account: Account, card: Card) {
  privateRequest++
  privateDetails.value = null
  privateLoading.value = false
  selectedAccountId.value = account.id
  selectedCardId.value = card.id
  cardDetailsVisible.value = true
}
watch(cardDetailsVisible, (visible) => {
  if (!visible) {
    privateRequest++
    privateDetails.value = null
    privateLoading.value = false
  }
})
async function revealCardDetails() {
  if (!selectedCard.value) return
  if (privateDetails.value) {
    privateDetails.value = null
    return
  }
  const request = ++privateRequest
  const id = selectedCard.value.id
  privateLoading.value = true
  try {
    const result = await api<{ card_number: string | null; cvv: string | null }>(
      `/admin/cards/${id}/private`,
      'POST',
    )
    if (request === privateRequest && cardDetailsVisible.value) privateDetails.value = result
  } catch (e) {
    if (request === privateRequest) ElMessage.error((e as Error).message)
  } finally {
    if (request === privateRequest) privateLoading.value = false
  }
}
const form = ref<Record<string, any>>({})
watch(dialog, (visible) => {
  if (!visible) {
    editorPrivateRequest++
    editorPrivateLoading.value = false
    form.value.card_number = undefined
    form.value.cvv = undefined
  }
})
async function loadPrivateForEdit() {
  const target = form.value
  const request = ++editorPrivateRequest
  editorPrivateLoading.value = true
  try {
    const result = await api<{ card_number: string | null; cvv: string | null }>(
      `/admin/cards/${editing.value}/private`,
      'POST',
    )
    if (request === editorPrivateRequest && dialog.value && form.value === target) {
      target.card_number = result.card_number || ''
      target.cvv = result.cvv || ''
    }
  } catch (e) {
    if (request === editorPrivateRequest) ElMessage.error((e as Error).message)
  } finally {
    if (request === editorPrivateRequest) editorPrivateLoading.value = false
  }
}
const annualFeeDays = computed(() =>
  form.value.annual_fee_month ? new Date(2000, form.value.annual_fee_month, 0).getDate() : 31,
)
watch(
  () => form.value.annual_fee_month,
  () => {
    if (!form.value.annual_fee_month || form.value.annual_fee_day > annualFeeDays.value)
      form.value.annual_fee_day = null
  },
)
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
  editorPrivateRequest++
  editorPrivateLoading.value = false
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
        card_number: '',
        expiry: '',
        cvv: '',
        card_type: 'credit',
        card_network: null,
        credit_limit: null,
        annual_fee_month: null,
        annual_fee_day: null,
        annual_fee_benefits: '',
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
  const cardDetails = {
    ...(f.card_number !== undefined
      ? { card_number: String(f.card_number || '').replace(/[ -]/g, '') || null }
      : {}),
    ...(f.cvv !== undefined ? { cvv: f.cvv?.trim() || null } : {}),
    expiry: f.expiry?.trim() || null,
    card_type: f.card_type || null,
    card_network: f.card_network || null,
    credit_limit: String(f.credit_limit ?? '').trim() || null,
    annual_fee_month: f.annual_fee_month || null,
    annual_fee_day: f.annual_fee_day || null,
    annual_fee_benefits: f.annual_fee_benefits?.trim() || null,
  }
  if (entity.value === 'card') {
    if (cardDetails.card_number && !/^[0-9]{12,19}$/.test(cardDetails.card_number)) {
      ElMessage.warning('卡号请输入 12–19 位数字，可使用空格或短横线分隔')
      return
    }
    if (cardDetails.card_number) {
      const tail = cardDetails.card_number.slice(-4)
      if (f.last_four && f.last_four !== tail) {
        ElMessage.warning('卡号与卡片尾号不一致，请核对')
        return
      }
      f.last_four = tail
    }
    if (!editing.value && !/^[0-9]{4}$/.test(f.last_four || '')) {
      ElMessage.warning('请填写 4 位卡片尾号，或填写完整卡号自动提取尾号')
      return
    }
    if (cardDetails.expiry && !/^(0[1-9]|1[0-2])\/[0-9]{2}$/.test(cardDetails.expiry)) {
      ElMessage.warning('有效期请按 MM/YY 填写，例如 09/29')
      return
    }
    if (cardDetails.cvv && !/^[0-9]{3,4}$/.test(cardDetails.cvv)) {
      ElMessage.warning('CVV 请输入 3 或 4 位数字')
      return
    }
    if (
      cardDetails.credit_limit !== null &&
      !/^\d{1,12}(\.\d{1,2})?$/.test(cardDetails.credit_limit)
    ) {
      ElMessage.warning('额度请输入非负金额，最多 12 位整数和 2 位小数')
      return
    }
    if (!!cardDetails.annual_fee_month !== !!cardDetails.annual_fee_day) {
      ElMessage.warning('请完整选择年费收取月份和日期，或清空月份以取消设置')
      return
    }
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
    card: { name: f.name, account_id: f.account_id, last_four: f.last_four, ...cardDetails },
  }
  const updates: Record<string, unknown> = {
    bank: { name: f.name, default_mode: f.default_mode, active: f.active },
    holder: { name: f.name, active: f.active },
    account: { name: f.name, active: f.active, statement_day: f.statement_day || null },
    card: { name: f.name, active: f.active, ...cardDetails },
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
              v-for="a in sortedAccounts"
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
                  :type="c.active ? undefined : 'danger'"
                  :title="`${c.name} · ${c.last_four}${c.active ? '' : '（停用）'}`"
                  @click="viewCard(a, c)"
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
  <el-dialog
    v-model="cardDetailsVisible"
    class="card-details-dialog"
    title="卡片详情"
    width="min(560px, 94vw)"
    destroy-on-close
  >
    <template v-if="selectedCard && selectedAccount">
      <div class="card-detail-heading">
        <div class="card-detail-identity">
          <h2>{{ selectedCard.name }}</h2>
          <span class="card-detail-tail" :aria-label="`卡片尾号 ${selectedCard.last_four}`"
            >· {{ selectedCard.last_four }}</span
          >
        </div>
        <el-tag :type="selectedCard.active ? 'success' : 'danger'" size="small">
          {{ selectedCard.active ? '有效' : '已停用' }}
        </el-tag>
      </div>
      <dl class="card-detail-list">
        <div>
          <dt>银行</dt>
          <dd>{{ selectedAccount.bank_name }}</dd>
        </div>
        <div>
          <dt>持卡人</dt>
          <dd>{{ selectedAccount.cardholder_name }}</dd>
        </div>
        <div>
          <dt>卡片类型</dt>
          <dd>{{ optionLabel(cardTypes, selectedCard.card_type) }}</dd>
        </div>
        <div>
          <dt>卡组织</dt>
          <dd>{{ optionLabel(cardNetworks, selectedCard.card_network) }}</dd>
        </div>
      </dl>
      <section class="card-private-block" aria-label="卡片安全信息">
        <dl class="card-detail-list card-private-fields">
          <div class="card-detail-number">
            <dt>卡号</dt>
            <dd
              class="private-value card-number-value"
              :class="{ 'is-empty': !selectedCard.has_card_number }"
            >
              {{ displayCardNumber }}
            </dd>
          </div>
          <div>
            <dt>有效期</dt>
            <dd>{{ selectedCard.expiry || '未填写' }}</dd>
          </div>
          <div>
            <dt>CVV</dt>
            <dd class="private-value">
              {{ privateDetails?.cvv || (selectedCard.has_cvv ? '•••' : '未填写') }}
            </dd>
          </div>
        </dl>
        <div class="private-controls" v-if="selectedCard.has_card_number || selectedCard.has_cvv">
          <el-button size="small" :loading="privateLoading" @click="revealCardDetails">{{
            privateDetails ? '隐藏卡号 / CVV' : '查看卡号 / CVV'
          }}</el-button>
          <span class="card-field-hint">敏感信息默认隐藏，请留意周围环境。</span>
        </div>
      </section>
      <dl class="card-detail-list">
        <div>
          <dt>还款模式</dt>
          <dd>{{ modeText(selectedAccount.mode) }}</dd>
        </div>
        <div>
          <dt>预计出账日</dt>
          <dd>
            {{
              selectedAccount.statement_day ? `每月 ${selectedAccount.statement_day} 日` : '未设置'
            }}
          </dd>
        </div>
        <div>
          <dt>额度</dt>
          <dd class="card-detail-limit" :class="{ 'is-empty': selectedCard.credit_limit == null }">
            {{
              selectedCard.credit_limit == null ? '未设置' : amountText(selectedCard.credit_limit)
            }}
          </dd>
        </div>
        <div>
          <dt>年费收取日期</dt>
          <dd>
            {{
              selectedCard.annual_fee_month && selectedCard.annual_fee_day
                ? `每年 ${selectedCard.annual_fee_month} 月 ${selectedCard.annual_fee_day} 日`
                : '未设置'
            }}
          </dd>
        </div>
        <div class="card-detail-benefits">
          <dt>年费优惠信息</dt>
          <dd :class="{ 'is-empty': !selectedCard.annual_fee_benefits }">
            {{ selectedCard.annual_fee_benefits || '未填写' }}
          </dd>
        </div>
      </dl>
    </template>
    <template #footer>
      <el-button @click="cardDetailsVisible = false">关闭</el-button>
    </template>
  </el-dialog>
  <el-dialog
    v-model="dialog"
    :title="title"
    class="card-editor-dialog"
    width="min(520px, 94vw)"
    destroy-on-close
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
        </p>
        <section v-if="editing" class="card-management" aria-label="卡片管理">
          <h3>卡片管理</h3>
          <p class="card-field-hint">在此编辑或删除卡片；列表中的卡片标签仅用于查看详情。</p>
          <div v-for="c in managedCards" :key="c.id" class="card-management-item">
            <span>{{ c.name }} · {{ c.last_four }}{{ c.active ? '' : '（停用）' }}</span>
            <div class="card-management-actions">
              <el-button
                text
                :disabled="saving || !!deleting"
                :aria-label="`编辑卡片 ${c.name}`"
                @click="open('card', c)"
                >编辑</el-button
              >
              <el-button
                text
                type="danger"
                :loading="deleting === c.id"
                :disabled="saving || (!!deleting && deleting !== c.id)"
                :aria-label="`删除卡片 ${c.name}`"
                @click="remove('card', c)"
                >删除</el-button
              >
            </div>
          </div>
          <p v-if="!managedCards.length" class="card-field-hint">暂未添加卡片</p>
        </section></template
      ><el-form-item v-if="entity === 'card' && !editing" label="卡片尾号（4 位）" required
        ><el-input
          v-model="form.last_four"
          maxlength="4"
          placeholder="填写 4 位尾号，或填写完整卡号自动提取" /></el-form-item
      ><template v-if="entity === 'card'">
        <div class="card-identity-fields">
          <el-form-item label="卡片类型" for="card-type">
            <el-select
              id="card-type"
              v-model="form.card_type"
              aria-label="卡片类型"
              clearable
              placeholder="选择信用卡或借记卡"
            >
              <el-option
                v-for="option in cardTypes"
                :key="option.value"
                :value="option.value"
                :label="option.label"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="卡组织" for="card-network">
            <el-select
              id="card-network"
              v-model="form.card_network"
              aria-label="卡组织"
              clearable
              placeholder="选择卡组织"
            >
              <el-option
                v-for="option in cardNetworks"
                :key="option.value"
                :value="option.value"
                :label="option.label"
              />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="卡号" for="card-number">
          <el-input
            id="card-number"
            v-model="form.card_number"
            type="password"
            show-password
            inputmode="numeric"
            autocomplete="off"
            maxlength="32"
            :disabled="editorPrivateLoading"
            :placeholder="
              form.has_card_number && form.card_number === undefined
                ? '已加密保存，不修改则保留'
                : '选填，12–19 位数字'
            "
          />
          <el-button
            v-if="form.has_card_number && form.card_number === undefined"
            text
            type="danger"
            @click="form.card_number = ''"
            >清除已保存卡号</el-button
          >
        </el-form-item>
        <div class="card-identity-fields">
          <el-form-item label="有效期" for="card-expiry">
            <el-input
              id="card-expiry"
              v-model="form.expiry"
              maxlength="5"
              placeholder="MM/YY，例如 09/29"
              autocomplete="off"
            />
          </el-form-item>
          <el-form-item label="CVV" for="card-cvv">
            <el-input
              id="card-cvv"
              v-model="form.cvv"
              type="password"
              show-password
              inputmode="numeric"
              autocomplete="off"
              maxlength="4"
              :disabled="editorPrivateLoading"
              :placeholder="
                form.has_cvv && form.cvv === undefined
                  ? '已加密保存，不修改则保留'
                  : '3 或 4 位安全码'
              "
            />
            <el-button
              v-if="form.has_cvv && form.cvv === undefined"
              text
              type="danger"
              @click="form.cvv = ''"
              >清除已保存 CVV</el-button
            >
          </el-form-item>
        </div>
        <div class="card-private-hint">
          <p class="card-field-hint">
            选填；卡号与 CVV 加密保存，需服务端配置 BILLS_MASTER_KEY。避免在公共设备查看。
          </p>
          <el-button
            v-if="editing && (form.has_card_number || form.has_cvv)"
            size="small"
            :loading="editorPrivateLoading"
            :disabled="saving"
            @click="loadPrivateForEdit"
            >读取已保存卡号 / CVV</el-button
          >
        </div>
        <el-form-item label="额度（元）" for="card-credit-limit">
          <el-input
            id="card-credit-limit"
            v-model="form.credit_limit"
            inputmode="decimal"
            maxlength="15"
            clearable
            placeholder="选填，例如 50000.00"
          >
            <template #prefix>¥</template>
          </el-input>
        </el-form-item>
        <el-form-item label="年费收取日期（每年几月几日）">
          <div class="annual-fee-date">
            <el-select
              v-model="form.annual_fee_month"
              aria-label="年费月份"
              clearable
              placeholder="选择月份"
            >
              <el-option v-for="month in 12" :key="month" :label="`${month} 月`" :value="month" />
            </el-select>
            <el-select
              v-model="form.annual_fee_day"
              aria-label="年费日期"
              :disabled="!form.annual_fee_month"
              clearable
              placeholder="选择日期"
            >
              <el-option
                v-for="day in annualFeeDays"
                :key="day"
                :label="`${day} 日`"
                :value="day"
              />
            </el-select>
          </div>
          <div class="card-field-hint">选填；仅记录月、日，清空月份可取消设置。</div>
        </el-form-item>
        <el-form-item label="年费优惠信息" for="card-annual-fee-benefits">
          <el-input
            id="card-annual-fee-benefits"
            v-model="form.annual_fee_benefits"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-word-limit
            placeholder="选填，例如首年免年费，每年刷满 6 笔免次年年费"
          />
        </el-form-item> </template
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
      ><el-button
        type="primary"
        :loading="saving"
        :disabled="!!deleting || editorPrivateLoading"
        @click="save"
        >保存</el-button
      ></template
    ></el-dialog
  >
</template>

<style scoped>
:global(.card-editor-dialog) {
  display: flex;
  flex-direction: column;
  max-height: 90dvh;
  margin-block: 5vh;
}
:global(.card-editor-dialog .el-dialog__header),
:global(.card-editor-dialog .el-dialog__footer) {
  flex-shrink: 0;
}
:global(.card-editor-dialog .el-dialog__body) {
  min-height: 0;
  overflow-y: auto;
}

.card-identity-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}
.card-identity-fields .el-select {
  width: 100%;
}
.card-private-hint {
  margin: 0 0 20px;
  padding: 12px 14px;
  border: 1px solid #e4e9df;
  border-radius: 8px;
  background: #f7f9f4;
}
.card-private-hint p {
  margin: 0 0 8px;
}
.card-detail-list .private-value {
  font-variant-numeric: tabular-nums;
  letter-spacing: 1px;
}
.card-detail-list .card-detail-number {
  grid-column: 1 / -1;
}
@media (max-width: 420px) {
  .card-identity-fields {
    grid-template-columns: minmax(0, 1fr);
  }
}

:global(.card-details-dialog) {
  display: flex;
  flex-direction: column;
  max-height: 90dvh;
  margin-block: 5vh;
  padding: 20px;
  border: 1px solid #e4e9df;
  border-radius: 14px;
}
:global(.card-details-dialog .el-dialog__header),
:global(.card-details-dialog .el-dialog__footer) {
  flex-shrink: 0;
}
:global(.card-details-dialog .el-dialog__header) {
  padding-bottom: 12px;
}
:global(.card-details-dialog .el-dialog__title) {
  font-size: 19px;
}
:global(.card-details-dialog .el-dialog__footer) {
  padding-top: 12px;
}
:global(.card-details-dialog .el-dialog__body) {
  min-height: 0;
  overflow-y: auto;
  scrollbar-gutter: stable;
}
.card-detail-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 0 0 8px;
  padding: 11px 14px;
  border: 1px solid #e2e9df;
  border-left: 3px solid var(--el-color-primary);
  border-radius: 8px;
  background: #f2f6ef;
}
.card-detail-identity {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 2px 8px;
  min-width: 0;
}
.card-detail-heading h2 {
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: 19px;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.card-detail-tail {
  color: #6c7e71;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.card-detail-heading .el-tag {
  flex-shrink: 0;
}
.card-detail-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 20px;
  margin: 0;
}
.card-detail-list > div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
  padding: 8px 0;
  border-bottom: 1px solid #e9ede5;
  line-height: 1.5;
}
.card-detail-list dt {
  flex-shrink: 0;
  color: #748178;
  font-size: 12px;
}
.card-detail-list dd {
  min-width: 0;
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: 14px;
  font-weight: 500;
  text-align: right;
  overflow-wrap: anywhere;
}
.card-private-block {
  margin: 10px 0 2px;
  padding: 10px 12px;
  border: 1px solid #e0e8df;
  border-radius: 10px;
  background: #f2f6ef;
}
.card-private-fields > div {
  padding: 6px 0;
  border-bottom: 0;
}
.card-private-fields > .card-detail-number {
  justify-content: flex-start;
  gap: 16px;
  padding: 0 0 8px;
  border-bottom: 1px solid #e0e8df;
}
.card-detail-list .card-number-value:not(.is-empty) {
  color: var(--el-color-primary);
  font-family: Consolas, 'Liberation Mono', monospace;
  font-size: 22px;
  font-weight: 600;
  line-height: 1.4;
  letter-spacing: 0.02em;
  text-align: left;
  overflow-wrap: normal;
  word-break: normal;
}
.card-private-block .private-controls {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  flex-wrap: wrap;
  gap: 6px 10px;
  margin-top: 2px;
  padding-top: 8px;
  border-top: 1px solid #e0e8df;
}
.private-controls .card-field-hint {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
}
.card-detail-list .card-detail-limit {
  color: var(--el-color-primary);
  font-size: 17px;
  font-variant-numeric: tabular-nums;
}
.card-detail-list .is-empty {
  color: #8a938b;
  font-size: 13px;
  font-weight: 400;
}
.card-detail-list > .card-detail-benefits {
  grid-column: 1 / -1;
  flex-direction: column;
  align-items: stretch;
  gap: 4px;
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid #e9e7dc;
  border-radius: 8px;
  background: #faf9f4;
}
.card-detail-benefits dd {
  white-space: pre-wrap;
  text-align: left;
  line-height: 1.6;
  font-weight: 400;
}
@media (max-width: 560px) {
  :global(.card-details-dialog) {
    padding: 16px;
  }
  .card-detail-list {
    column-gap: 16px;
  }
  .card-detail-list > div {
    flex-direction: column;
    align-items: stretch;
    gap: 3px;
    padding-block: 8px;
  }
  .card-detail-list dd {
    text-align: left;
  }
  .card-detail-list > .card-detail-number {
    flex-direction: row;
    align-items: baseline;
    gap: 12px;
    padding: 0 0 8px;
  }
  .card-private-block {
    padding: 10px;
  }
  .card-private-fields > div:not(.card-detail-number) {
    padding-block: 6px;
  }
  .card-detail-list .card-number-value:not(.is-empty) {
    font-size: 18px;
  }
}
.card-management {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--el-border-color-lighter);
}
.card-management h3 {
  margin: 0;
  font-size: 14px;
}
.card-management-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 4px 12px;
  padding: 8px 0;
  overflow-wrap: anywhere;
}
.card-management-item > span {
  min-width: 0;
  flex: 1 1 160px;
}
.card-management-actions {
  display: flex;
  margin-left: auto;
}
.card-management-actions .el-button {
  margin: 0;
}
.annual-fee-date {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  width: 100%;
}
.card-field-hint {
  margin-top: 6px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}

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
