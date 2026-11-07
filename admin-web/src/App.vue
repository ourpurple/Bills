<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Menu, Lock } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { logout } from './api'
import WorkspaceSidebar from './components/WorkspaceSidebar.vue'
import { version } from '../package.json'
const route = useRoute(),
  router = useRouter()
const heading = computed(() => route.meta.title)
const breakpoint = window.matchMedia('(max-width: 1023px)')
const mobile = ref(breakpoint.matches),
  menuOpen = ref(false)
const updateBreakpoint = () => {
  mobile.value = breakpoint.matches
  menuOpen.value = false
}
onMounted(() => breakpoint.addEventListener('change', updateBreakpoint))
onUnmounted(() => breakpoint.removeEventListener('change', updateBreakpoint))
watch(
  () => route.fullPath,
  () => {
    menuOpen.value = false
  },
)
async function signOut() {
  try {
    await logout()
    menuOpen.value = false
    await router.replace('/login')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}
</script>
<template>
  <router-view v-if="route.path === '/login'" />
  <div v-else class="workspace">
    <a class="skip-link" href="#main-content">跳至页面内容</a>
    <aside v-if="!mobile" class="sidebar">
      <WorkspaceSidebar @sign-out="signOut" />
    </aside>
    <el-drawer
      v-if="mobile"
      v-model="menuOpen"
      direction="ltr"
      size="min(304px, 88vw)"
      class="navigation-drawer"
      title="我的账单工作台"
      :z-index="2100"
    >
      <WorkspaceSidebar @navigate="menuOpen = false" @sign-out="signOut" />
    </el-drawer>
    <section class="main-shell">
      <header class="topbar">
        <div class="topbar-path">
          <el-button
            v-if="mobile"
            class="menu-trigger"
            :icon="Menu"
            aria-label="打开主导航"
            :aria-expanded="menuOpen"
            @click="menuOpen = true"
            >菜单</el-button
          >
          <span class="breadcrumb"
            ><span class="crumb-root">个人财务 <span class="crumb">/</span></span
            >{{ heading }}</span
          >
        </div>
        <span class="privacy-badge"
          ><el-icon aria-hidden="true"><Lock /></el-icon>仅记录，不执行支付</span
        >
      </header>
      <main id="main-content" class="page-content" tabindex="-1"><router-view /></main>
      <footer class="page-footer">
        <span>账序 · 私人账单档案</span><span>v{{ version }} · 记录有据，核对有序</span>
      </footer>
    </section>
  </div>
</template>
