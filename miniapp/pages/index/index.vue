<template>
  <view class="home-page">
    <view class="home-content">
      <view class="top-bar">
        <view class="avatar" @click="goAccount">
          <text>{{ avatarText }}</text>
        </view>
        <view class="brand-copy">
          <text class="brand-title">漫游手账</text>
          <text class="brand-subtitle">AI 帮你把远方排成日程</text>
        </view>
      </view>

      <view class="hero-card">
        <text class="hero-eyebrow">TODAY'S IDEA</text>
        <text class="hero-title">下一站，交给 AI</text>
        <text class="hero-copy">说出想去的地方，我们一起把路画出来。</text>
        <view class="hero-art" aria-hidden="true">
          <view class="hero-sun"></view>
          <view class="hero-route"></view>
          <view class="hero-pin"></view>
          <view class="hero-moon"></view>
        </view>
      </view>

      <view class="quick-card">
        <text class="card-title">创建新行程</text>

        <text class="field-label">目的地</text>
        <view class="input-shell">
          <input
            v-model="form.city"
            class="city-input"
            placeholder="例如：南京、江西、西藏"
            placeholder-style="color:#718188"
          />
          <text v-if="form.city" class="clear-text" @click="form.city = ''">×</text>
          <text v-else class="location-mark">⌖</text>
        </view>
        <text v-if="destinationHint" class="destination-hint">{{ destinationHint }}</text>

        <text class="field-label date-label">出行日期</text>
        <view class="date-row">
          <picker mode="date" :value="form.start_date" @change="onStartDateChange">
            <view class="date-value" :class="{ muted: !form.start_date }">
              {{ formatDate(form.start_date) || '开始日期' }}
            </view>
          </picker>
          <text class="date-separator">—</text>
          <picker
            mode="date"
            :value="form.end_date"
            :start="form.start_date || today"
            @change="onEndDateChange"
          >
            <view class="date-value" :class="{ muted: !form.end_date }">
              {{ formatDate(form.end_date) || '结束日期' }}
            </view>
          </picker>
          <view class="days-pill">{{ travelDays }}天</view>
        </view>
        <button class="primary-button start-button" @click="startPlanning">
          <text>开始规划</text>
          <text class="button-arrow">→</text>
        </button>
      </view>

      <text class="shortcut-title">不知道省内先去哪？让 AI 先画一条路线</text>
      <view class="shortcut-grid">
        <view class="shortcut-card intelligent" @click="startMultiCity">
          <text class="shortcut-name multi">智能串联</text>
          <text class="shortcut-copy">省份 / 区域\n先推荐可编辑节点</text>
          <view class="shortcut-route"><text>●﹏●</text></view>
        </view>
        <view class="shortcut-card continue-card" @click="continueTrip">
          <text class="shortcut-name continue">继续行程</text>
          <text class="shortcut-copy">{{ pendingLabel }}</text>
          <text class="continue-status">{{ pendingTask ? '查看最新进度' : '查看我的行程' }}</text>
        </view>
      </view>

      <view class="paper-note">
        <view class="paper-tape"></view>
        <text class="note-quote">“行程不是赶路，是把喜欢的地方连起来。”</text>
        <text class="note-meta">— 今天也去看看世界</text>
      </view>
    </view>

    <view v-if="showRegionChooser" class="mode-mask" @click="showRegionChooser = false">
      <view class="mode-sheet" @click.stop>
        <text class="mode-title">{{ form.city.trim() }}想怎么玩？</text>
        <text class="mode-copy">检测到省级或区域目的地，建议先确认跨城路线，再生成每天的详细安排。</text>
        <view class="mode-option recommended" @click="chooseRegionMode('multi_city')">
          <view>
            <text class="mode-name">AI 推荐省内路线</text>
            <text class="mode-desc">根据天数推荐城市与顺序，之后可以编辑</text>
          </view>
          <text class="recommend-tag">推荐</text>
        </view>
        <view class="mode-option" @click="chooseRegionMode('road_trip_loop')">
          <view>
            <text class="mode-name">规划自驾环线</text>
            <text class="mode-desc">返回出发地，按每日驾驶时长控制路线</text>
          </view>
        </view>
        <view class="mode-option" @click="chooseRegionMode('manual')">
          <view>
            <text class="mode-name">我来填写具体城市</text>
            <text class="mode-desc">例如：南昌/景德镇/婺源</text>
          </view>
        </view>
      </view>
    </view>

    <view class="bottom-nav">
      <view class="bottom-nav__item active"><text class="nav-dot"></text>首页</view>
      <view class="bottom-nav__item" @click="goHistory">行程</view>
      <view class="bottom-nav__item" @click="goAccount">我的</view>
    </view>
  </view>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { onHide, onShow } from '@dcloudio/uni-app'
import { getSession, getTripPlanTask, saveHistoryEntry } from '@/api/index.js'

const today = new Date().toISOString().slice(0, 10)
const session = ref(null)
const pendingTask = ref(null)
const showRegionChooser = ref(false)
let taskRefreshTimer = null

const REGION_NAMES = new Set([
  '河北', '山西', '辽宁', '吉林', '黑龙江', '江苏', '浙江', '安徽', '福建', '江西',
  '山东', '河南', '湖北', '湖南', '广东', '海南', '四川', '贵州', '云南', '陕西',
  '甘肃', '青海', '台湾', '内蒙古', '广西', '西藏', '宁夏', '新疆',
  '川西', '青甘', '北疆', '南疆'
])

const form = reactive({
  city: '',
  start_date: '',
  end_date: ''
})

const travelDays = computed(() => {
  if (!form.start_date || !form.end_date) return 1
  const days = Math.round((new Date(form.end_date) - new Date(form.start_date)) / 86400000) + 1
  return days > 0 ? days : 1
})

const destinationHint = computed(() => {
  const destinations = parseDestinations(form.city)
  if (destinations.length > 1) return `已识别 ${destinations.length} 个城市，将先确认跨城顺序`
  if (isRegionDestination(form.city)) return '已识别为省级或区域目的地，将先由 AI 推荐城市路线'
  return ''
})

const avatarText = computed(() => session.value?.user?.username?.slice(0, 1).toUpperCase() || '旅')
const pendingLabel = computed(() => {
  if (!session.value) return '登录后查看行程'
  if (!pendingTask.value) return '查看历史行程'
  const statusMap = {
    queued: '等待生成',
    running: '生成中',
    succeeded: '已完成',
    degraded: '已完成，部分信息待确认',
    failed: '生成失败'
  }
  const status = statusMap[pendingTask.value.status] || '状态待刷新'
  return `${pendingTask.value.city || '旅行'} · ${status}`
})

onShow(async () => {
  session.value = getSession()
  try {
    const draftActive = uni.getStorageSync('tripDraftActive') === '1'
    const savedDraft = JSON.parse(uni.getStorageSync('tripDraft') || 'null')
    if (draftActive && savedDraft) {
      form.city = savedDraft.city || form.city
      form.start_date = savedDraft.start_date || form.start_date
      form.end_date = savedDraft.end_date || form.end_date
    } else {
      form.city = ''
      form.start_date = ''
      form.end_date = ''
    }
    const savedTask = JSON.parse(uni.getStorageSync('tripGenerationTask') || 'null')
    const currentUserId = session.value?.user?.id
    pendingTask.value = currentUserId && savedTask?.user_id === currentUserId ? savedTask : null
  } catch {
    pendingTask.value = null
  }
  await refreshPendingTask()
  scheduleTaskRefresh()
})

onHide(() => {
  stopTaskRefresh()
})

function stopTaskRefresh() {
  if (taskRefreshTimer) clearTimeout(taskRefreshTimer)
  taskRefreshTimer = null
}

function scheduleTaskRefresh() {
  stopTaskRefresh()
  if (!['queued', 'running'].includes(pendingTask.value?.status)) return
  taskRefreshTimer = setTimeout(async () => {
    await refreshPendingTask()
    scheduleTaskRefresh()
  }, 3000)
}

async function refreshPendingTask() {
  const savedTask = pendingTask.value
  if (!session.value || !savedTask?.task_id) return

  try {
    const response = await getTripPlanTask(savedTask.task_id)
    const refreshedTask = {
      ...savedTask,
      status: response.status,
      progress: response.progress || 0,
      message: response.message || savedTask.message,
      updated_at: new Date().toISOString()
    }

    if (['succeeded', 'degraded'].includes(response.status) && response.data) {
      uni.setStorageSync('tripPlan', JSON.stringify(response.data))
      uni.setStorageSync('tripPlanEntryId', savedTask.task_id)
      if (!savedTask.history_saved) {
        try {
          await saveHistoryEntry(savedTask.task_id, response.data)
          refreshedTask.history_saved = true
        } catch (error) {
          console.warn('后台完成行程的历史记录同步失败:', error)
        }
      }
      uni.removeStorageSync('tripDraft')
      uni.removeStorageSync('tripDraftActive')
      uni.removeStorageSync('pendingTripRequest')
    }

    pendingTask.value = refreshedTask
    uni.setStorageSync('tripGenerationTask', JSON.stringify(refreshedTask))
  } catch (error) {
    if (error.status === 404) {
      pendingTask.value = null
      uni.removeStorageSync('tripGenerationTask')
    }
  }
}

function formatDate(value) {
  if (!value) return ''
  const [, month, day] = value.split('-')
  return `${Number(month)}月${Number(day)}日`
}

function onStartDateChange(event) {
  form.start_date = event.detail.value
  if (form.end_date && form.end_date < form.start_date) form.end_date = ''
}

function onEndDateChange(event) {
  form.end_date = event.detail.value
}

function normalizeRegionName(value) {
  return String(value || '')
    .trim()
    .replace(/维吾尔自治区|壮族自治区|回族自治区|自治区|特别行政区|省/g, '')
}

function parseDestinations(value) {
  return String(value || '')
    .split(/[，,、/；;]+/)
    .map(item => item.trim())
    .filter(Boolean)
}

function isRegionDestination(value) {
  const normalized = normalizeRegionName(value)
  return REGION_NAMES.has(normalized)
}

function validateBase() {
  if (!form.city.trim()) {
    uni.showToast({ title: '请输入目的地', icon: 'none' })
    return false
  }
  if (!form.start_date || !form.end_date) {
    uni.showToast({ title: '请选择完整出行日期', icon: 'none' })
    return false
  }
  if (form.end_date < form.start_date) {
    uni.showToast({ title: '结束日期不能早于开始日期', icon: 'none' })
    return false
  }
  return true
}

function startPlanning() {
  if (!validateBase()) return
  const destinations = parseDestinations(form.city)
  if (destinations.length > 1) {
    goNext('multi_city', { destinations, routeSource: 'manual' })
    return
  }
  if (isRegionDestination(form.city)) {
    showRegionChooser.value = true
    return
  }
  goNext('single_city')
}

function startMultiCity() {
  if (!validateBase()) return
  const destinations = parseDestinations(form.city)
  if (destinations.length > 1) {
    goNext('multi_city', { destinations, routeSource: 'manual' })
    return
  }
  if (isRegionDestination(form.city)) {
    goNext('multi_city', { routeSource: 'ai_region' })
    return
  }
  uni.showModal({
    title: '请输入省份或多个城市',
    content: '例如输入“江西”，由 AI 推荐城市；或输入“南昌/景德镇/婺源”自行指定节点。',
    showCancel: false
  })
}

function chooseRegionMode(mode) {
  showRegionChooser.value = false
  if (mode === 'manual') {
    form.city = ''
    uni.showToast({ title: '请填写两个及以上城市', icon: 'none' })
    return
  }
  goNext(mode, { routeSource: 'ai_region' })
}

function goNext(mode, options = {}) {
  if (!validateBase()) return
  if (mode !== 'single_city' && travelDays.value < 3) {
    uni.showModal({
      title: '跨城路线至少需要 3 天',
      content: '当前天数较短，建议改为具体城市进行单城规划。',
      confirmText: '普通规划',
      success: result => {
        if (result.confirm) goNext('single_city')
      }
    })
    return
  }

  const draft = {
    city: form.city.trim(),
    start_date: form.start_date,
    end_date: form.end_date,
    travel_days: travelDays.value,
    trip_mode: mode,
    transportation: mode === 'road_trip_loop' ? '自驾' : '公共交通',
    accommodation: '经济型酒店',
    preferences: [],
    free_text_input: '',
    arrival_transport: undefined,
    arrival_time: '',
    arrival_location: '',
    departure_transport: undefined,
    departure_time: '',
    departure_location: '',
    destinations: options.destinations || [],
    return_to_start: mode === 'road_trip_loop',
    intercity_transport: mode === 'road_trip_loop' ? 'self_drive' : 'auto',
    max_drive_hours_per_day: 6,
    route_source: options.routeSource || undefined
  }
  uni.setStorageSync('tripDraft', JSON.stringify(draft))
  uni.setStorageSync('tripDraftActive', '1')
  uni.navigateTo({
    url: mode === 'single_city' ? '/pages/preferences/preferences' : '/pages/route/route'
  })
}

function continueTrip() {
  if (!session.value) {
    uni.navigateTo({ url: '/pages/account/account' })
    return
  }
  if (pendingTask.value && !['succeeded', 'failed'].includes(pendingTask.value.status)) {
    uni.navigateTo({ url: '/pages/generating/generating' })
    return
  }
  goHistory()
}

function goHistory() {
  uni.navigateTo({ url: '/pages/history/history' })
}

function goAccount() {
  uni.navigateTo({ url: '/pages/account/account' })
}
</script>

<style scoped>
.home-page {
  min-height: 100vh;
  background: #f8f7fc;
}

.home-content {
  padding: calc(var(--status-bar-height) + 28rpx) 32rpx 170rpx;
}

.top-bar {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 18rpx;
  margin-bottom: 28rpx;
}

.brand-title {
  color: #252038;
  font-size: 36rpx;
  font-weight: 700;
}

.avatar {
  width: 72rpx;
  height: 72rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: #eae6ff;
  color: #6658e8;
  font-size: 27rpx;
  font-weight: 700;
}

.hero-card {
  position: relative;
  min-height: 340rpx;
  padding: 42rpx 38rpx;
  overflow: hidden;
  border-radius: 44rpx;
  background: linear-gradient(135deg, #7267f0, #8e5dc8);
  color: #fff;
}

.hero-title,
.hero-copy {
  position: relative;
  z-index: 2;
  display: block;
}

.hero-title {
  font-size: 48rpx;
  font-weight: 800;
}

.hero-copy {
  width: 520rpx;
  margin-top: 24rpx;
  color: #eeeafe;
  font-size: 26rpx;
  line-height: 1.65;
}

.ai-orb {
  position: absolute;
  right: 34rpx;
  bottom: 28rpx;
  width: 142rpx;
  height: 142rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.16);
  font-size: 42rpx;
  font-weight: 700;
}

.quick-card {
  margin-top: 32rpx;
  padding: 32rpx;
  border: 2rpx solid #e9e6f2;
  border-radius: 40rpx;
  background: #fff;
}

.card-title {
  display: block;
  margin-bottom: 28rpx;
  font-size: 34rpx;
  font-weight: 700;
}

.field-label {
  display: block;
  margin-bottom: 12rpx;
  color: #7d768f;
  font-size: 23rpx;
}

.date-label {
  margin-top: 28rpx;
}

.input-shell {
  height: 92rpx;
  padding: 0 24rpx;
  display: flex;
  align-items: center;
  border-radius: 24rpx;
  background: #f7f5fc;
}

.city-input {
  flex: 1;
  height: 92rpx;
  color: #302a45;
  font-size: 30rpx;
  font-weight: 500;
}

.clear-text {
  color: #6658e8;
  font-size: 24rpx;
}

.destination-hint {
  display: block;
  margin-top: 12rpx;
  color: #6658e8;
  font-size: 22rpx;
  line-height: 1.45;
}

.date-row {
  display: flex;
  align-items: center;
  gap: 14rpx;
}

.date-value {
  color: #302a45;
  font-size: 29rpx;
  font-weight: 600;
}

.date-value.muted {
  color: #aaa4b8;
  font-weight: 400;
}

.date-separator {
  color: #9a94a6;
}

.days-pill {
  margin-left: auto;
  padding: 10rpx 22rpx;
  border-radius: 28rpx;
  background: #efeaff;
  color: #6658e8;
  font-size: 23rpx;
  font-weight: 600;
}

.start-button {
  margin-top: 32rpx;
}

.start-button .button-arrow {
  position: absolute;
  right: 38rpx;
  font-size: 36rpx;
}

.shortcut-title {
  display: block;
  margin: 42rpx 8rpx 18rpx;
  color: #8a8497;
  font-size: 24rpx;
}

.shortcut-grid {
  display: flex;
  gap: 24rpx;
}

.shortcut-card {
  flex: 1;
  min-height: 132rpx;
  padding: 26rpx 24rpx;
  border: 2rpx solid #e9e6f2;
  border-radius: 32rpx;
  background: #fff;
}

.shortcut-card.wide {
  min-height: 104rpx;
  margin-top: 18rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.shortcut-arrow {
  color: #8a8497;
  font-size: 34rpx;
}

.shortcut-name,
.shortcut-copy {
  display: block;
}

.shortcut-name {
  font-size: 27rpx;
  font-weight: 700;
}

.shortcut-name.loop {
  color: #6658e8;
}

.shortcut-name.multi {
  color: #27875f;
}

.shortcut-name.continue {
  color: #e36d4f;
}

.shortcut-copy {
  margin-top: 14rpx;
  color: #7d768f;
  font-size: 22rpx;
}

.mode-mask {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: flex-end;
  background: rgba(31, 27, 45, 0.42);
}

.mode-sheet {
  width: 100%;
  padding: 36rpx 32rpx calc(36rpx + env(safe-area-inset-bottom));
  border-radius: 44rpx 44rpx 0 0;
  background: #fff;
}

.mode-title,
.mode-copy,
.mode-name,
.mode-desc {
  display: block;
}

.mode-title {
  color: #252038;
  font-size: 36rpx;
  font-weight: 800;
}

.mode-copy {
  margin: 12rpx 0 26rpx;
  color: #7d768f;
  font-size: 23rpx;
  line-height: 1.55;
}

.mode-option {
  min-height: 112rpx;
  margin-top: 16rpx;
  padding: 22rpx 24rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border: 2rpx solid #e9e6f2;
  border-radius: 28rpx;
  background: #faf9fd;
}

.mode-option.recommended {
  border-color: #bdb4f5;
  background: #f2efff;
}

.mode-name {
  color: #302a45;
  font-size: 27rpx;
  font-weight: 700;
}

.mode-desc {
  margin-top: 8rpx;
  color: #7d768f;
  font-size: 21rpx;
}

.recommend-tag {
  flex-shrink: 0;
  margin-left: 16rpx;
  padding: 7rpx 14rpx;
  border-radius: 18rpx;
  background: #6658e8;
  color: #fff;
  font-size: 19rpx;
}

/* 手绘旅行视觉覆盖 */
.home-page {
  background: var(--background);
}

.home-content {
  padding: calc(var(--status-bar-height) + 28rpx) 40rpx 180rpx;
}

.top-bar {
  gap: 26rpx;
  margin: 0 20rpx 34rpx;
}

.avatar {
  width: 76rpx;
  height: 76rpx;
  background: var(--brand-soft);
  color: var(--brand);
  font-size: 28rpx;
}

.brand-copy,
.brand-title,
.brand-subtitle {
  display: block;
}

.brand-title {
  color: var(--text);
  font-size: 42rpx;
  line-height: 1.15;
}

.brand-subtitle {
  margin-top: 6rpx;
  color: var(--muted);
  font-size: 24rpx;
}

.hero-card {
  min-height: 328rpx;
  padding: 40rpx;
  border-radius: 46rpx;
  background: #2f79ab;
}

.hero-eyebrow {
  position: relative;
  z-index: 2;
  display: block;
  color: #dceef4;
  font-size: 22rpx;
  font-weight: 700;
}

.hero-title {
  margin-top: 22rpx;
  font-size: 50rpx;
}

.hero-copy {
  width: 470rpx;
  margin-top: 22rpx;
  color: #fff;
  font-size: 29rpx;
  line-height: 1.35;
}

.hero-art {
  position: absolute;
  right: 30rpx;
  top: 48rpx;
  width: 190rpx;
  height: 220rpx;
}

.hero-sun {
  position: absolute;
  right: 18rpx;
  top: 18rpx;
  width: 56rpx;
  height: 56rpx;
  border-radius: 50%;
  background: #ffc342;
  box-shadow: 0 -24rpx 0 -21rpx #ffc342, 0 24rpx 0 -21rpx #ffc342,
    24rpx 0 0 -21rpx #ffc342, -24rpx 0 0 -21rpx #ffc342;
}

.hero-route {
  position: absolute;
  left: 18rpx;
  top: 96rpx;
  width: 128rpx;
  height: 70rpx;
  border-top: 6rpx dashed #ffc342;
  border-radius: 60%;
  transform: rotate(-18deg);
}

.hero-pin {
  position: absolute;
  left: 8rpx;
  bottom: 35rpx;
  width: 24rpx;
  height: 24rpx;
  border: 8rpx solid #fff;
  border-radius: 50%;
  background: var(--accent);
}

.hero-moon {
  position: absolute;
  right: 25rpx;
  bottom: 12rpx;
  width: 48rpx;
  height: 48rpx;
  border-radius: 50%;
  background: #fffdf3;
  box-shadow: 15rpx -5rpx 0 0 #2f79ab;
}

.quick-card {
  margin-top: 36rpx;
  padding: 36rpx;
  border-color: var(--border);
  background: var(--surface);
}

.card-title {
  color: var(--text);
  font-size: 34rpx;
}

.field-label {
  color: var(--muted);
  font-size: 25rpx;
}

.input-shell {
  height: 96rpx;
  border: 2rpx solid var(--border);
  border-radius: 28rpx;
  background: var(--background);
}

.city-input {
  color: var(--text);
}

.clear-text,
.location-mark {
  color: var(--accent);
  font-size: 34rpx;
}

.destination-hint {
  color: var(--brand);
}

.date-value {
  color: var(--text);
  font-size: 30rpx;
}

.days-pill {
  background: var(--brand-soft);
  color: var(--brand);
}

.start-button {
  margin-top: 30rpx;
  background: var(--accent);
}

.shortcut-title {
  margin: 42rpx 4rpx 18rpx;
  color: var(--muted);
  font-size: 25rpx;
}

.shortcut-grid {
  gap: 24rpx;
}

.shortcut-card {
  min-height: 200rpx;
  padding: 30rpx;
  border-color: var(--border);
  border-radius: 34rpx;
  background: var(--surface);
}

.shortcut-card.intelligent {
  position: relative;
  background: var(--brand-soft);
  border-color: transparent;
}

.shortcut-name {
  color: var(--text);
  font-size: 30rpx;
}

.shortcut-name.multi {
  color: var(--brand);
}

.shortcut-copy {
  margin-top: 20rpx;
  color: var(--muted);
  font-size: 24rpx;
  line-height: 1.35;
  white-space: pre-line;
}

.shortcut-route {
  position: absolute;
  right: 18rpx;
  bottom: 20rpx;
  color: var(--brand);
  font-size: 22rpx;
  transform: rotate(-12deg);
}

.continue-status {
  display: block;
  margin-top: 18rpx;
  color: var(--success);
  font-size: 23rpx;
}

.paper-note {
  position: relative;
  margin-top: 36rpx;
  padding: 34rpx 36rpx;
  border-radius: 34rpx;
  background: var(--accent-soft);
}

.paper-tape {
  position: absolute;
  top: -8rpx;
  right: 52rpx;
  width: 92rpx;
  height: 25rpx;
  background: rgba(255, 195, 66, 0.65);
  transform: rotate(5deg);
}

.note-quote,
.note-meta {
  display: block;
}

.note-quote {
  color: var(--text);
  font-size: 26rpx;
  font-weight: 700;
}

.note-meta {
  margin-top: 15rpx;
  color: var(--muted);
  font-size: 22rpx;
}

.bottom-nav__item.active {
  position: relative;
  color: var(--brand);
}

.nav-dot {
  position: absolute;
  top: -25rpx;
  left: 50%;
  width: 12rpx;
  height: 12rpx;
  border-radius: 50%;
  background: var(--accent);
  transform: translateX(-50%);
}

.mode-sheet {
  background: var(--surface);
}

.mode-option {
  border-color: var(--border);
  background: var(--background);
}

.mode-option.recommended {
  border-color: var(--brand);
  background: var(--brand-soft);
}

.recommend-tag {
  background: var(--brand);
}
</style>
