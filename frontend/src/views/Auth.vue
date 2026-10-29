<template>
  <div class="auth-page"><a-card title="账号登录" class="auth-card">
    <a-tabs v-model:activeKey="mode"><a-tab-pane key="login" tab="登录"/><a-tab-pane key="register" tab="注册"/></a-tabs>
    <a-form :model="form" layout="vertical" @finish="submit">
      <a-form-item label="用户名" name="username" :rules="[{ required: true, min: 3, message: '用户名至少 3 位' }]" ><a-input v-model:value="form.username" placeholder="字母、数字或下划线"/></a-form-item>
      <a-form-item label="密码" name="password" :rules="[{ required: true, min: 8, message: '密码至少 8 位' }]" ><a-input-password v-model:value="form.password" placeholder="至少 8 位"/></a-form-item>
      <a-button html-type="submit" type="primary" block :loading="loading">{{ mode === 'login' ? '登录' : '注册并登录' }}</a-button>
    </a-form><p>登录后才能生成、保存和编辑旅行计划。</p>
  </a-card></div>
</template>
<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { authRequest } from '@/services/auth'
const router = useRouter()
const route = useRoute()
const mode = ref('login')
const form = reactive({ username: '', password: '' })
const loading = ref(false)

const safeRedirect = () => {
  const redirect = route.query.redirect
  return typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')
    ? redirect
    : '/'
}

const submit = async () => {
  loading.value = true
  try {
    await authRequest(
      mode.value === 'login' ? '/login' : '/register',
      form.username,
      form.password
    )
    message.success(
      route.query.continue === 'trip'
        ? '登录成功，正在继续生成旅行计划'
        : '登录成功'
    )
    await router.replace(safeRedirect())
  } catch (error: any) {
    message.error(error.response?.data?.detail || '登录失败')
  } finally {
    loading.value = false
  }
}
</script>
<style scoped>.auth-page{display:grid;place-items:center;min-height:65vh}.auth-card{width:380px}p{color:#888;font-size:13px}</style>
