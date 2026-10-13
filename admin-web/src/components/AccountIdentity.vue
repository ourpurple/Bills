<script setup lang="ts">
import type { Account } from '../types'
defineProps<{ account: Account }>()
</script>
<template>
  <div class="statement-identity">
    <div v-for="card in account.cards" :key="card.id" class="statement-card">
      <span class="statement-card-name">{{ card.name }}</span>
      <span class="statement-card-tail">· 尾号 {{ card.last_four }}</span>
    </div>
    <div v-if="!account.cards.length" class="table-sub">暂未添加卡片</div>
    <div class="statement-account">{{ account.bank_name }} · {{ account.cardholder_name }}</div>
  </div>
</template>
<style scoped>
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
