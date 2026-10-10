<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { login } from '../api'
const user = ref(''),
  password = ref(''),
  busy = ref(false),
  router = useRouter()
async function submit() {
  if (!user.value || !password.value) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  busy.value = true
  try {
    await login(user.value.trim(), password.value)
    password.value = ''
    await router.replace('/')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <div class="login-page">
    <section class="login-story">
      <div class="brand">
        <span class="brand-symbol">序</span>
        <div><strong>账序</strong><small>BILLS · PERSONAL</small></div>
      </div>
      <div class="login-copy">
        <div class="eyebrow">YOUR BILLS, IN ORDER.</div>
        <h1>每一笔账单，<br />都有条理。</h1>
        <p>把卡片、账单和还款记录放在一起。<br />少一点牵挂，多一点从容。</p>
        <div class="login-lines">
          <span>01 / 账户归集</span><span>02 / 账单整理</span><span>03 / 还款记录</span>
        </div>
      </div>
      <small>个人专属账单工作台 · 不连接银行支付</small>
    </section>
    <section class="login-form-wrap">
      <form class="login-form" @submit.prevent="submit">
        <div class="eyebrow">WELCOME BACK</div>
        <h2>登录你的工作台</h2>
        <p>使用在服务器上创建的管理员账号。</p>
        <label for="login-user">用户名</label
        ><el-input
          id="login-user"
          v-model="user"
          autocomplete="username"
          placeholder="输入管理员用户名"
          size="large"
          :maxlength="80"
        /><label for="login-password">密码</label
        ><el-input
          id="login-password"
          v-model="password"
          type="password"
          show-password
          autocomplete="current-password"
          placeholder="输入密码"
          size="large"
          :maxlength="256"
        /><el-button
          native-type="submit"
          type="primary"
          size="large"
          :loading="busy"
          class="login-submit"
          >进入工作台</el-button
        >
        <div class="login-hint">没有公开注册或默认密码。<br />首次使用请按部署说明创建管理员。</div>
      </form>
    </section>
  </div>
</template>
