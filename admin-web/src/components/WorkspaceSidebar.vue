<script setup lang="ts">
import { useRoute } from 'vue-router'
import {
  DataBoard,
  CreditCard,
  Calendar,
  Document,
  Setting,
  Message,
  MagicStick,
  List,
  ArrowRight,
  SwitchButton,
} from '@element-plus/icons-vue'
import { username } from '../api'
defineEmits<{ navigate: []; signOut: [] }>()
const route = useRoute()
const navigation = [
  { path: '/', label: '账单概览', icon: DataBoard },
  { path: '/statements', label: '账单管理', icon: Document },
  { path: '/annual-fees', label: '年费管理', icon: Calendar },
  { path: '/imports', label: '邮件中心', icon: Message },
  { path: '/models', label: '模型与核对', icon: MagicStick },
  { path: '/tasks', label: '任务队列', icon: List },
  { path: '/accounts', label: '账户与卡片', icon: CreditCard },
  { path: '/settings', label: '安全与设置', icon: Setting },
]
</script>
<template>
  <router-link to="/" class="brand" aria-label="账序首页" @click="$emit('navigate')">
    <span class="brand-symbol" aria-hidden="true">序</span>
    <div><strong>账序</strong><small>BILLS · PERSONAL</small></div>
  </router-link>
  <div class="nav-caption">我的账单工作台</div>
  <nav aria-label="主导航">
    <router-link
      v-for="(item, index) in navigation"
      :key="item.path"
      :to="item.path"
      :aria-label="item.label"
      :title="item.label"
      class="nav-item"
      :class="{ active: route.path === item.path, 'nav-section-start': index === 3 || index === 6 }"
      :aria-current="route.path === item.path ? 'page' : undefined"
      @click="$emit('navigate')"
    >
      <el-icon aria-hidden="true"><component :is="item.icon" /></el-icon>
      <span>{{ item.label }}</span>
      <el-icon v-if="route.path === item.path" class="nav-arrow" aria-hidden="true"
        ><ArrowRight
      /></el-icon>
    </router-link>
  </nav>
  <div class="sidebar-note">
    <span class="status-dot" />个人专属 · 单机管理
    <p>整理账单，让每一次还款<br />都有清晰的记录。</p>
  </div>
  <div class="sidebar-user">
    <span class="avatar" aria-hidden="true">{{ username.slice(0, 1).toUpperCase() }}</span>
    <div>
      <strong :title="username">{{ username }}</strong
      ><small>管理员</small>
    </div>
    <el-button text circle aria-label="退出登录" @click="$emit('signOut')">
      <el-icon><SwitchButton /></el-icon>
    </el-button>
  </div>
</template>
