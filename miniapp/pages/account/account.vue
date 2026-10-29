<template>
  <view class="account-page">
    <view class="page-header">
      <text class="back" @click="goBack">‹</text>
      <view>
        <text class="header-kicker">TRAVEL NOTES</text>
        <text class="header-title">{{ session ? '我的账户' : '登录 / 注册' }}</text>
      </view>
    </view>

    <template v-if="session">
      <view class="profile-card">
        <view class="profile-avatar">{{ displayName.slice(0, 1).toUpperCase() }}</view>
        <text class="profile-label">MY TRAVEL ACCOUNT</text>
        <text class="profile-name">{{ displayName }}</text>
        <text class="profile-copy">Web 与小程序行程已使用同一账户同步</text>
      </view>
      <view class="account-actions">
        <view class="account-row" @click="goHistory">
          <text>我的历史行程</text><text>›</text>
        </view>
        <view class="account-row" @click="handleWechatBinding">
          <text>{{ session.user.is_wechat_only ? '绑定已有 Web 账号' : '微信账号绑定' }}</text>
          <text :class="session.user.has_wechat_identity ? 'bound-tag' : 'pending-tag'">
            {{ session.user.is_wechat_only ? '去绑定' : (session.user.has_wechat_identity ? '已绑定' : '未绑定') }}
          </text>
        </view>
      </view>
      <view class="account-actions legal-actions">
        <view class="account-row" @click="openLegal('terms')"><text>用户协议</text><text>›</text></view>
        <view class="account-row" @click="openLegal('privacy')"><text>隐私政策</text><text>›</text></view>
        <view class="account-row" @click="openWechatPrivacyContract"><text>微信隐私保护指引</text><text>›</text></view>
        <button class="account-row feedback-row" open-type="feedback"><text>意见反馈</text><text>›</text></button>
      </view>
      <view v-if="showMergeForm" class="merge-card">
        <text class="merge-title">绑定已有 Web 账号</text>
        <text class="merge-copy">验证原账号后，当前微信行程会合并到该账号，以后两端使用同一份历史记录。</text>
        <input v-model="mergeUsername" class="auth-input" placeholder="Web 用户名" />
        <input v-model="mergePassword" class="auth-input" password placeholder="Web 账号密码" @confirm="submitMerge" />
        <button class="primary-button" :loading="binding" @click="submitMerge">确认绑定并合并</button>
      </view>
      <button class="secondary-button logout-button" @click="logout">退出登录</button>
      <button class="delete-button" :loading="deleting" @click="requestDeleteAccount">注销账号</button>
    </template>

    <template v-else>
      <view class="brand-mark"><text>• · · •</text></view>
      <text class="welcome">欢迎回来</text>
      <text class="welcome-copy">登录后可在 Web 与小程序间同步行程</text>

      <view class="benefits">
        <view v-for="item in benefits" :key="item" class="benefit-row">
          <view class="check">✓</view>
          <text>{{ item }}</text>
        </view>
      </view>

      <button class="wechat-button" :loading="loading" @click="wechatLogin">微信快捷登录</button>

      <view class="divider"><text>其他登录方式</text></view>

      <view class="auth-tabs">
        <text :class="{ active: mode === 'login' }" @click="mode = 'login'">账号登录</text>
        <text :class="{ active: mode === 'register' }" @click="mode = 'register'">注册账号</text>
      </view>

      <view class="auth-form">
        <input
          v-model="username"
          class="auth-input"
          placeholder="用户名（字母、数字或下划线）"
          placeholder-style="color:#aaa4b8"
        />
        <input
          v-model="password"
          class="auth-input"
          password
          placeholder="密码（至少 8 位）"
          placeholder-style="color:#aaa4b8"
          @confirm="submit"
        />
        <button class="primary-button" :loading="loading" @click="submit">
          {{ mode === 'login' ? '登录' : '注册并登录' }}
        </button>
      </view>

      <view class="binding-note">
        <text class="binding-title">已有 Web 账号？</text>
        <text class="binding-copy">直接使用相同用户名登录，两端会读取同一个用户及历史行程。</text>
      </view>
      <view class="agreement" @click="accepted = !accepted">
        <view class="agreement-check" :class="{ checked: accepted }">{{ accepted ? '✓' : '' }}</view>
        <view class="agreement-copy">
          <text>我已阅读并同意</text>
          <text class="legal-link" @click.stop="openLegal('terms')">《用户协议》</text>
          <text>和</text>
          <text class="legal-link" @click.stop="openLegal('privacy')">《隐私政策》</text>
        </view>
      </view>
      <text class="wechat-privacy-link" @click="openWechatPrivacyContract">查看《微信隐私保护指引》</text>
    </template>

    <view class="bottom-nav">
      <view class="bottom-nav__item" @click="goHome">首页</view>
      <view class="bottom-nav__item" @click="goHistory">行程</view>
      <view class="bottom-nav__item active"><text class="nav-dot"></text>我的</view>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import {
  clearSession,
  bindWechat,
  deleteAccount,
  getSession,
  LEGAL_CONSENT_VERSION,
  login,
  mergeWechatAccount,
  register,
  wechatLogin as requestWechatLogin
} from '@/api/index.js'

const session = ref(null)
const mode = ref('login')
const username = ref('')
const password = ref('')
const loading = ref(false)
const binding = ref(false)
const deleting = ref(false)
const accepted = ref(false)
const showMergeForm = ref(false)
const mergeUsername = ref('')
const mergePassword = ref('')
const displayName = computed(() => session.value?.user?.is_wechat_only ? '微信用户' : (session.value?.user?.username || '用户'))
const benefits = ['生成任务自动保存', '跨设备同步历史行程', '继续编辑已生成路线']

onShow(() => {
  session.value = getSession()
})

function finishLogin(response, message) {
  session.value = response
  uni.showToast({ title: message, icon: 'success' })
  const next = uni.getStorageSync('postLoginRoute')
  if (next) {
    uni.removeStorageSync('postLoginRoute')
    setTimeout(() => uni.redirectTo({ url: next }), 450)
  }
}

function getWechatCode() {
  return new Promise((resolve, reject) => {
    uni.login({
      provider: 'weixin',
      success(result) {
        if (result.code) resolve(result.code)
        else reject(new Error('微信未返回登录凭证，请重新尝试'))
      },
      fail() {
        reject(new Error('当前环境无法调用微信登录，请在微信开发者工具或真机中尝试'))
      }
    })
  })
}

async function submit() {
  if (!ensureAgreement()) return
  if (!/^[A-Za-z0-9_]{3,32}$/.test(username.value)) {
    uni.showToast({ title: '用户名需为 3–32 位字母、数字或下划线', icon: 'none' })
    return
  }
  if (password.value.length < 8) {
    uni.showToast({ title: '密码至少需要 8 位', icon: 'none' })
    return
  }

  loading.value = true
  try {
    const response = mode.value === 'login'
      ? await login(username.value, password.value, LEGAL_CONSENT_VERSION)
      : await register(username.value, password.value, LEGAL_CONSENT_VERSION)
    finishLogin(response, mode.value === 'login' ? '登录成功' : '注册成功')
  } catch (error) {
    uni.showModal({ title: mode.value === 'login' ? '登录失败' : '注册失败', content: error.message, showCancel: false })
  } finally {
    loading.value = false
  }
}

async function wechatLogin() {
  if (!ensureAgreement()) return
  if (loading.value) return
  loading.value = true
  try {
    const response = await requestWechatLogin(await getWechatCode(), LEGAL_CONSENT_VERSION)
    finishLogin(response, '微信登录成功')
  } catch (error) {
    uni.showModal({ title: '微信登录失败', content: error.message, showCancel: false })
  } finally {
    loading.value = false
  }
}

function logout() {
  clearSession({ clearTripData: true })
  session.value = null
  showMergeForm.value = false
  mergePassword.value = ''
  uni.showToast({ title: '已退出登录', icon: 'success' })
}

function goHistory() {
  uni.redirectTo({ url: '/pages/history/history' })
}

function goHome() {
  uni.reLaunch({ url: '/pages/index/index' })
}

function ensureAgreement() {
  if (accepted.value) return true
  uni.showToast({ title: '请先阅读并同意用户协议和隐私政策', icon: 'none' })
  return false
}

function openLegal(type) {
  uni.navigateTo({ url: `/pages/legal/legal?type=${type}` })
}

function openWechatPrivacyContract() {
  if (typeof wx !== 'undefined' && typeof wx.openPrivacyContract === 'function') {
    wx.openPrivacyContract({
      fail: () => uni.showToast({ title: '请先在微信公众平台配置隐私保护指引', icon: 'none' })
    })
    return
  }
  uni.showToast({ title: '请在微信小程序中查看', icon: 'none' })
}

function requestDeleteAccount() {
  if (deleting.value) return
  uni.showModal({
    title: '确认注销账号？',
    content: '注销后账号、微信绑定、历史行程和任务数据将被删除，且无法恢复。',
    confirmText: '确认注销',
    confirmColor: '#d84b55',
    success: async result => {
      if (!result.confirm) return
      deleting.value = true
      try {
        await deleteAccount()
        clearSession({ clearTripData: true })
        uni.showToast({ title: '账号已注销', icon: 'success' })
        setTimeout(() => uni.reLaunch({ url: '/pages/index/index' }), 450)
      } catch (error) {
        uni.showModal({ title: '暂时无法注销', content: error.message, showCancel: false })
      } finally {
        deleting.value = false
      }
    }
  })
}

async function handleWechatBinding() {
  if (session.value?.user?.is_wechat_only) {
    showMergeForm.value = !showMergeForm.value
    return
  }
  if (session.value?.user?.has_wechat_identity) {
    uni.showToast({ title: '当前账号已绑定微信', icon: 'none' })
    return
  }
  if (binding.value) return
  binding.value = true
  try {
    const response = await bindWechat(await getWechatCode())
    session.value = response
    uni.showToast({ title: '微信绑定成功', icon: 'success' })
  } catch (error) {
    uni.showModal({ title: '微信绑定失败', content: error.message, showCancel: false })
  } finally {
    binding.value = false
  }
}

async function submitMerge() {
  if (!/^[A-Za-z0-9_]{3,32}$/.test(mergeUsername.value) || mergePassword.value.length < 8) {
    uni.showToast({ title: '请填写正确的 Web 用户名和密码', icon: 'none' })
    return
  }
  if (binding.value) return
  binding.value = true
  try {
    const response = await mergeWechatAccount(mergeUsername.value, mergePassword.value)
    session.value = response
    showMergeForm.value = false
    mergePassword.value = ''
    uni.showToast({ title: '账号绑定成功', icon: 'success' })
  } catch (error) {
    uni.showModal({ title: '账号绑定失败', content: error.message, showCancel: false })
  } finally {
    binding.value = false
  }
}

function goBack() {
  const pages = getCurrentPages()
  if (pages.length > 1) uni.navigateBack()
  else uni.reLaunch({ url: '/pages/index/index' })
}
</script>

<style scoped>
.account-page {
  min-height: 100vh;
  padding: calc(var(--status-bar-height) + 18rpx) 40rpx calc(170rpx + env(safe-area-inset-bottom));
  background: var(--background);
}

.page-header {
  min-height: 88rpx;
  display: flex;
  align-items: center;
  gap: 18rpx;
}

.back {
  width: 64rpx;
  height: 64rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2rpx solid var(--border);
  border-radius: 50%;
  background: var(--surface);
  color: var(--text);
  font-size: 48rpx;
  line-height: 54rpx;
}

.header-kicker,
.header-title {
  display: block;
}

.header-kicker {
  color: var(--brand);
  font-size: 18rpx;
  font-weight: 700;
  letter-spacing: 2rpx;
}

.header-title {
  margin-top: 4rpx;
  color: var(--text);
  font-size: 36rpx;
  font-weight: 800;
}

.brand-mark {
  width: 176rpx;
  height: 148rpx;
  margin: 44rpx auto 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 3rpx solid var(--brand);
  border-radius: 48rpx 54rpx 44rpx 56rpx;
  background: var(--brand-soft);
  color: var(--accent);
  font-size: 34rpx;
  font-weight: 800;
  letter-spacing: 4rpx;
  box-shadow: 9rpx 9rpx 0 #fff0c7;
  transform: rotate(-2deg);
}

.welcome,
.welcome-copy {
  display: block;
  text-align: center;
}

.welcome {
  margin-top: 40rpx;
  color: var(--text);
  font-size: 44rpx;
  font-weight: 800;
}

.welcome-copy {
  margin-top: 12rpx;
  color: var(--muted);
  font-size: 24rpx;
}

.benefits {
  margin-top: 42rpx;
  padding: 24rpx 30rpx;
  border: 2rpx solid #ead9a7;
  border-radius: 38rpx;
  background: #fff4ca;
}

.benefit-row {
  min-height: 62rpx;
  display: flex;
  align-items: center;
  gap: 20rpx;
  color: var(--text);
  font-size: 24rpx;
  font-weight: 600;
}

.check {
  width: 42rpx;
  height: 42rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--success-soft);
  color: var(--success);
  font-size: 21rpx;
  font-weight: 700;
}

.wechat-button {
  height: 104rpx;
  line-height: 104rpx;
  margin: 34rpx 0 0;
  border-radius: 34rpx;
  background: #07c160;
  color: #fff;
  font-size: 29rpx;
  font-weight: 700;
}

.divider {
  height: 70rpx;
  display: flex;
  align-items: center;
  color: var(--muted);
  font-size: 21rpx;
}

.divider::before,
.divider::after {
  content: "";
  height: 2rpx;
  flex: 1;
  background: var(--border);
}

.divider text {
  padding: 0 24rpx;
}

.auth-tabs {
  display: flex;
  justify-content: center;
  gap: 72rpx;
}

.auth-tabs text {
  padding: 10rpx 0;
  color: var(--muted);
  font-size: 26rpx;
}

.auth-tabs text.active {
  border-bottom: 4rpx solid var(--accent);
  color: var(--brand);
  font-weight: 700;
}

.auth-form {
  margin-top: 24rpx;
}

.auth-input {
  height: 92rpx;
  margin-bottom: 18rpx;
  padding: 0 28rpx;
  border: 2rpx solid var(--border);
  border-radius: 28rpx;
  background: var(--surface);
  color: var(--text);
  font-size: 25rpx;
}

.binding-note {
  margin-top: 34rpx;
  padding: 26rpx;
  border-radius: 30rpx;
  background: var(--accent-soft);
}

.binding-title,
.binding-copy {
  display: block;
}

.binding-title {
  color: #9a5c17;
  font-size: 25rpx;
  font-weight: 700;
}

.binding-copy {
  margin-top: 10rpx;
  color: #805f3b;
  font-size: 21rpx;
  line-height: 1.55;
}

.agreement {
  margin-top: 28rpx;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  gap: 12rpx;
  color: var(--muted);
  font-size: 19rpx;
  line-height: 1.55;
}

.agreement-check {
  width: 28rpx;
  height: 28rpx;
  margin-top: 1rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border: 2rpx solid var(--border);
  border-radius: 7rpx;
  color: #fff;
  font-size: 19rpx;
}

.agreement-check.checked {
  border-color: var(--brand);
  background: var(--brand);
}

.agreement-copy {
  display: flex;
  flex-wrap: wrap;
}

.legal-link,
.wechat-privacy-link {
  color: var(--brand);
}

.wechat-privacy-link {
  display: block;
  margin-top: 12rpx;
  font-size: 19rpx;
  text-align: center;
}

.profile-card {
  margin-top: 34rpx;
  padding: 44rpx 30rpx 38rpx;
  text-align: center;
  border: 2rpx solid #bed9e4;
  border-radius: 44rpx;
  background: var(--brand-soft);
}

.profile-avatar {
  width: 132rpx;
  height: 132rpx;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  border: 3rpx solid var(--brand);
  background: var(--surface);
  color: var(--brand);
  font-size: 48rpx;
  font-weight: 800;
}

.profile-label,
.profile-name,
.profile-copy {
  display: block;
}

.profile-label {
  margin-top: 22rpx;
  color: var(--brand);
  font-size: 18rpx;
  font-weight: 700;
  letter-spacing: 2rpx;
}

.profile-name {
  margin-top: 8rpx;
  color: var(--text);
  font-size: 36rpx;
  font-weight: 800;
}

.profile-copy {
  margin-top: 12rpx;
  color: #52788b;
  font-size: 23rpx;
}

.account-actions {
  margin-top: 30rpx;
  border: 2rpx solid var(--border);
  border-radius: 38rpx;
  background: var(--surface);
  overflow: hidden;
}

.account-row {
  min-height: 100rpx;
  padding: 0 28rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 2rpx dashed var(--border);
  color: var(--text);
  font-size: 26rpx;
}

.account-row:last-child {
  border-bottom: none;
}

.pending-tag {
  padding: 8rpx 16rpx;
  border-radius: 20rpx;
  background: var(--accent-soft);
  color: #c46b18;
  font-size: 20rpx;
}

.bound-tag {
  padding: 8rpx 16rpx;
  border-radius: 20rpx;
  background: var(--success-soft);
  color: var(--success);
  font-size: 20rpx;
}

.merge-card {
  margin-top: 24rpx;
  padding: 30rpx 26rpx;
  border: 2rpx solid var(--border);
  border-radius: 34rpx;
  background: var(--surface);
}

.merge-title,
.merge-copy {
  display: block;
}

.merge-title {
  color: var(--text);
  font-size: 28rpx;
  font-weight: 700;
}

.merge-copy {
  margin: 12rpx 0 24rpx;
  color: var(--muted);
  font-size: 22rpx;
  line-height: 1.55;
}

.logout-button {
  margin-top: 34rpx;
}

.legal-actions {
  margin-top: 22rpx;
}

.feedback-row {
  width: 100%;
  margin: 0;
  border-radius: 0;
  background: transparent;
  line-height: normal;
  text-align: left;
}

.feedback-row::after {
  border: none;
}

.delete-button {
  margin-top: 16rpx;
  border: none;
  background: transparent;
  color: #d84b55;
  font-size: 24rpx;
}

.delete-button::after {
  border: none;
}

.nav-dot {
  width: 12rpx;
  height: 12rpx;
  margin: 0 auto 7rpx;
  display: block;
  border-radius: 50%;
  background: var(--accent);
}
</style>
