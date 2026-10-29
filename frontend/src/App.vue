<template>
  <div id="app">
    <a-layout style="min-height: 100vh">
      <a-layout-header class="app-header">
        <a class="app-title" @click="router.push('/')">八花的智能旅行规划</a>
        <a-space>
          <template v-if="session">
            <span class="username">{{ session.user.username }}</span>
            <a-button size="small" @click="logout">&#x9000;&#x51FA;&#x767B;&#x5F55;</a-button>
          </template>
          <a-button v-else size="small" type="primary" @click="router.push('/auth')">&#x767B;&#x5F55; / &#x6CE8;&#x518C;</a-button>
        </a-space>
      </a-layout-header>
      <a-layout-content style="padding: 24px"><router-view /></a-layout-content>
      <a-layout-footer class="app-footer">
        <span>八花的智能旅行规划</span>
        <span class="footer-separator">·</span>
        <a
          href="https://beian.miit.gov.cn/"
          target="_blank"
          rel="noopener noreferrer"
        >藏ICP备2026000973号-1</a>
        <span class="footer-separator">·</span>
        <a href="#" @click.prevent="licenseOpen = true">来源与许可</a>
      </a-layout-footer>
    </a-layout>
    <a-modal v-model:open="licenseOpen" title="来源与许可" :footer="null">
      <p>
        本项目基于
        <a
          href="https://github.com/datawhalechina/hello-agents/tree/main/code/chapter13/helloagents-trip-planner"
          target="_blank"
          rel="noopener noreferrer"
        >Datawhale「HelloAgents 智能旅行助手」示例项目</a>
        改编并已修改，对 Agent 框架、数据服务和产品功能进行了扩展与调整。
      </p>
      <p>
        本项目对原示例项目的使用遵循
        <a
          href="https://creativecommons.org/licenses/by-nc-sa/4.0/deed.zh-hans"
          target="_blank"
          rel="noopener noreferrer"
        >CC BY-NC-SA 4.0</a>
        许可。本说明不表示项目完整源代码已公开发布。
      </p>
    </a-modal>
  </div>
</template>
<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { clearSession, getSession } from './services/auth'
const router = useRouter(); const session = ref(getSession()); const licenseOpen = ref(false)
const logout = () => { clearSession(); session.value = null; router.push('/') }
const refreshSession = () => { session.value = getSession() }
onMounted(() => window.addEventListener('trip-auth-changed', refreshSession))
onUnmounted(() => window.removeEventListener('trip-auth-changed', refreshSession))
</script>
<style>
#app { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; }
.app-header { background:#001529; padding:0 50px; display:flex; justify-content:space-between; align-items:center; }
.app-title { color:white; font-size:24px; font-weight:bold; cursor:pointer; }.username { color:rgba(255,255,255,.85); }
.app-footer { text-align:center; color:rgba(0,0,0,.62); }
.app-footer a { color:inherit; text-decoration:underline; text-underline-offset:2px; }
.footer-separator { margin:0 8px; }
</style>
