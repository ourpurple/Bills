<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
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
import { ElMessage } from 'element-plus'
import { logout, username } from './api'
const route = useRoute(),
  router = useRouter()
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
const heading = computed(() => route.meta.title)
async function signOut() {
  try {
    await logout()
    await router.replace('/login')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}
</script>
<template>
  <router-view v-if="route.path === '/login'" />
  <div v-else class="workspace">
    <aside class="sidebar">
      <router-link to="/" class="brand"
        ><span class="brand-symbol">序</span>
        <div><strong>账序</strong><small>BILLS · PERSONAL</small></div></router-link
      >
      <div class="nav-caption">我的账单工作台</div>
      <nav aria-label="主导航">
        <router-link
          v-for="item in navigation"
          :key="item.path"
          :to="item.path"
          :aria-label="item.label"
          :title="item.label"
          class="nav-item"
          :class="{ active: route.path === item.path }"
          ><el-icon><component :is="item.icon" /></el-icon><span>{{ item.label }}</span
          ><el-icon v-if="route.path === item.path" class="nav-arrow"><ArrowRight /></el-icon
        ></router-link>
      </nav>
      <div class="sidebar-note">
        <span class="status-dot" />个人专属 · 单机管理
        <p>整理账单，让每一次还款<br />都有清晰的记录。</p>
      </div>
      <div class="sidebar-user">
        <span class="avatar">{{ username.slice(0, 1).toUpperCase() }}</span>
        <div>
          <strong>{{ username }}</strong
          ><small>管理员</small>
        </div>
        <el-button text circle aria-label="退出登录" @click="signOut"
          ><el-icon><SwitchButton /></el-icon
        ></el-button>
      </div>
    </aside>
    <section class="main-shell">
      <header class="topbar">
        <span>个人财务 <span class="crumb">/</span> {{ heading }}</span
        ><span class="privacy-badge"><span class="status-dot" />仅记录，不执行支付</span>
      </header>
      <main class="page-content"><router-view /></main>
      <footer class="page-footer">
        <span>账序 · 私人账单档案</span><span>v0.3.0 · 模型识别与核对阶段</span>
      </footer>
    </section>
  </div>
</template>
