import { createRouter, createWebHistory } from 'vue-router'
import { loadSession, username } from './api'
const Login = () => import('./views/Login.vue')
const Overview = () => import('./views/Overview.vue')
const Accounts = () => import('./views/Accounts.vue')
const Statements = () => import('./views/Statements.vue')
const Imports = () => import('./views/Imports.vue')
const Models = () => import('./views/Models.vue')
const Tasks = () => import('./views/Tasks.vue')
const Settings = () => import('./views/Settings.vue')
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: Login, meta: { title: '登录' } },
    { path: '/', component: Overview, meta: { title: '账单概览' } },
    { path: '/accounts', component: Accounts, meta: { title: '账户与卡片' } },
    { path: '/statements', component: Statements, meta: { title: '账单管理' } },
    { path: '/imports', component: Imports, meta: { title: '邮件中心' } },
    { path: '/models', component: Models, meta: { title: '模型与核对' } },
    { path: '/tasks', component: Tasks, meta: { title: '任务队列' } },
    { path: '/settings', component: Settings, meta: { title: '安全与设置' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
router.beforeEach(async (to) => {
  if (to.path === '/login') return
  if (!username.value) {
    try {
      await loadSession()
    } catch {
      return '/login'
    }
  }
})
window.addEventListener('bills-session-expired', () => {
  if (router.currentRoute.value.path !== '/login') void router.replace('/login')
})
export default router
