<template>
  <view class="history-page">
    <view class="history-content">
      <view class="top-bar">
        <view>
          <text class="title">我的行程</text>
          <text class="subtitle">把想去和走过的路，都收进手账</text>
        </view>
      </view>

      <view class="filters">
        <view
          v-for="item in filters"
          :key="item.value"
          class="filter"
          :class="{ active: filter === item.value }"
          @click="filter = item.value"
        >
          {{ item.label }}
        </view>
      </view>

      <view v-if="!session" class="empty-card">
        <text class="empty-title">登录后查看跨端行程</text>
        <text class="empty-copy">Web 与小程序中生成的行程会同步到同一个账户。</text>
        <button class="primary-button" @click="goAccount">登录 / 注册</button>
      </view>

      <template v-else>
        <view
          v-if="showActiveTask"
          class="trip-card"
          @click="openActiveTask"
        >
          <view class="trip-head">
            <text class="trip-title">{{ activeTask.city || '旅行计划' }}</text>
            <text class="status generating">生成中</text>
          </view>
          <text class="trip-date">{{ activeTask.updated_at ? formatTime(activeTask.updated_at) : '刚刚创建' }}</text>
          <text class="trip-summary">{{ activeTask.message || '任务已保存，可继续查看生成进度。' }}</text>
          <view class="progress-row">
            <view class="progress-track">
              <view class="progress-value" :style="{ width: `${activeTask.progress || 0}%` }"></view>
            </view>
            <text class="progress-text">{{ activeTask.progress || 0 }}%</text>
          </view>
        </view>

        <view
          v-for="entry in filteredHistory"
          :key="entry.id"
          class="trip-card"
          @click="openPlan(entry)"
        >
          <view class="trip-head">
            <text class="trip-title">{{ entry.plan.city }} {{ entry.plan.days?.length || '' }} 日</text>
            <text class="status completed">已完成</text>
          </view>
          <text class="trip-date">{{ entry.plan.start_date }} — {{ entry.plan.end_date }}</text>
          <text class="trip-summary">{{ tripSummary(entry.plan) }}</text>
          <text class="view-link">查看行程 →</text>
        </view>

        <view v-if="!loading && !showActiveTask && filteredHistory.length === 0" class="empty-card">
          <text class="empty-title">还没有行程</text>
          <text class="empty-copy">创建第一段旅程，AI 会把结果保存在这里。</text>
          <button class="primary-button" @click="createTrip">创建新行程</button>
        </view>

        <view class="sync-card">
          <text class="sync-title">账户已同步</text>
          <text class="sync-copy">Web 与小程序中的行程会显示在这里</text>
        </view>
      </template>
    </view>

    <view class="bottom-nav">
      <view class="bottom-nav__item" @click="goHome">首页</view>
      <view class="bottom-nav__item active"><text class="nav-dot"></text>行程</view>
      <view class="bottom-nav__item" @click="goAccount">我的</view>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import { fetchAccountHistory, getSession } from '@/api/index.js'

const filters = [
  { label: '全部', value: 'all' },
  { label: '生成中', value: 'generating' },
  { label: '已完成', value: 'completed' }
]
const filter = ref('all')
const session = ref(null)
const history = ref([])
const activeTask = ref(null)
const loading = ref(false)

const showActiveTask = computed(() => {
  return !!session.value &&
    !!activeTask.value &&
    activeTask.value.user_id === session.value.user?.id &&
    ['queued', 'running'].includes(activeTask.value.status) &&
    ['all', 'generating'].includes(filter.value)
})

const filteredHistory = computed(() => {
  if (filter.value === 'generating') return []
  return history.value
})

onShow(async () => {
  session.value = getSession()
  try {
    const savedTask = JSON.parse(uni.getStorageSync('tripGenerationTask') || 'null')
    activeTask.value = session.value?.user?.id && savedTask?.user_id === session.value.user.id
      ? savedTask
      : null
  } catch {
    activeTask.value = null
  }
  if (!session.value) {
    history.value = []
    return
  }
  await loadHistory()
})

async function loadHistory() {
  loading.value = true
  try {
    history.value = await fetchAccountHistory()
  } catch (error) {
    uni.showToast({ title: error.message || '历史记录加载失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

function tripSummary(plan) {
  const parts = []
  if (plan.trip_mode === 'road_trip_loop') parts.push('环线旅行')
  if (plan.route_nodes?.length) parts.push(plan.route_nodes.slice(0, 3).join(' · '))
  if (!parts.length && plan.days?.[0]?.description) parts.push(plan.days[0].description)
  return parts.join(' · ') || plan.overall_suggestions || '个性化旅行计划'
}

function formatTime(value) {
  try {
    const date = new Date(value)
    return `${date.getMonth() + 1}月${date.getDate()}日 ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  } catch {
    return value
  }
}

function openActiveTask() {
  uni.navigateTo({ url: '/pages/generating/generating' })
}

function openPlan(entry) {
  uni.setStorageSync('tripPlan', JSON.stringify(entry.plan))
  uni.setStorageSync('tripPlanEntryId', entry.id)
  uni.navigateTo({ url: '/pages/result/result' })
}

function createTrip() {
  uni.reLaunch({ url: '/pages/index/index' })
}

function goHome() {
  uni.reLaunch({ url: '/pages/index/index' })
}

function goAccount() {
  uni.redirectTo({ url: '/pages/account/account' })
}
</script>

<style scoped>
.history-page {
  min-height: 100vh;
  background: var(--background);
}

.history-content {
  padding: calc(var(--status-bar-height) + 34rpx) 40rpx 180rpx;
}

.top-bar {
  display: flex;
  align-items: center;
  justify-content: flex-start;
}

.title,
.subtitle {
  display: block;
}

.title {
  color: var(--text);
  font-size: 48rpx;
  font-weight: 800;
  line-height: 1.2;
}

.subtitle {
  margin-top: 10rpx;
  color: var(--muted);
  font-size: 22rpx;
}

.filters {
  margin-top: 38rpx;
  padding: 6rpx;
  display: flex;
  border: 2rpx solid var(--border);
  border-radius: 28rpx;
  background: var(--surface);
}

.filter {
  flex: 1;
  padding: 18rpx 0;
  border-radius: 22rpx;
  color: var(--muted);
  font-size: 23rpx;
  text-align: center;
}

.filter.active {
  background: var(--brand);
  color: #fff;
  font-weight: 700;
}

.trip-card {
  position: relative;
  margin-top: 28rpx;
  padding: 32rpx;
  overflow: hidden;
  border: 2rpx solid var(--border);
  border-radius: 40rpx;
  background: var(--surface);
  box-shadow: 0 10rpx 24rpx rgba(36, 66, 77, 0.05);
}

.trip-card::before {
  content: "";
  position: absolute;
  left: 0;
  top: 30rpx;
  width: 8rpx;
  height: 62rpx;
  border-radius: 0 8rpx 8rpx 0;
  background: var(--accent);
}

.trip-head {
  display: flex;
  align-items: center;
  gap: 18rpx;
}

.trip-title {
  min-width: 0;
  flex: 1;
  color: var(--text);
  font-size: 32rpx;
  font-weight: 800;
}

.status {
  min-width: 118rpx;
  padding: 9rpx 16rpx;
  border-radius: 22rpx;
  font-size: 21rpx;
  font-weight: 700;
  text-align: center;
}

.status.generating {
  background: var(--accent-soft);
  color: #a45e16;
}

.status.completed {
  background: var(--success-soft);
  color: var(--success);
}

.trip-date,
.trip-summary,
.view-link {
  display: block;
}

.trip-date {
  margin-top: 14rpx;
  color: var(--muted);
  font-size: 22rpx;
}

.trip-summary {
  margin-top: 22rpx;
  overflow: hidden;
  color: #536b74;
  font-size: 23rpx;
  line-height: 1.55;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.progress-row {
  margin-top: 26rpx;
  display: flex;
  align-items: center;
  gap: 20rpx;
}

.progress-track {
  height: 8rpx;
  flex: 1;
  overflow: hidden;
  border-radius: 4rpx;
  background: var(--brand-soft);
}

.progress-value {
  height: 100%;
  background: var(--brand);
}

.progress-text {
  color: var(--brand);
  font-size: 22rpx;
  font-weight: 700;
}

.view-link {
  margin-top: 26rpx;
  color: var(--brand);
  font-size: 23rpx;
  font-weight: 700;
  text-align: right;
}

.empty-card {
  margin-top: 40rpx;
  padding: 64rpx 38rpx 46rpx;
  border: 2rpx solid #ead9a7;
  border-radius: 42rpx;
  background: #fff4ca;
  text-align: center;
}

.empty-title,
.empty-copy {
  display: block;
}

.empty-title {
  color: var(--text);
  font-size: 34rpx;
  font-weight: 800;
}

.empty-copy {
  margin: 18rpx 0 36rpx;
  color: #6f725f;
  font-size: 23rpx;
  line-height: 1.6;
}

.sync-card {
  margin-top: 28rpx;
  padding: 26rpx 28rpx;
  border-radius: 30rpx;
  background: var(--brand-soft);
}

.sync-title,
.sync-copy {
  display: block;
}

.sync-title {
  color: var(--brand-dark);
  font-size: 24rpx;
  font-weight: 700;
}

.sync-copy {
  margin-top: 8rpx;
  color: #52788b;
  font-size: 21rpx;
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
