<template>
  <view class="preferences-page">
    <view class="page-header">
      <text class="back" @click="goBack">‹</text>
      <text class="header-title">再说说你的节奏</text>
      <text class="step">第 2 / 3 步</text>
    </view>
    <view class="progress-track"><view class="progress-value"></view></view>

    <scroll-view class="content" scroll-y>
      <view class="timing-card">
        <text class="section-title no-margin">先看抵达与离开</text>
        <text class="timing-intro">{{ boundarySummary }}，AI 会据此留出交通、入住和返程时间</text>
        <view class="timing-column">
          <text class="timing-label arrival">抵达 {{ arrivalBoundaryCity }}</text>
          <text class="timing-date">{{ formatShortDate(draft.start_date) }}</text>
          <picker class="timing-picker" mode="selector" :range="arrivalOptions" :value="arrivalIndex" @change="onArrivalTransportChange">
            <view class="timing-select-row">
              <text class="select-caption">交通方式</text>
              <text class="select-value">{{ arrivalOptions[arrivalIndex] }} ⌄</text>
            </view>
          </picker>
          <picker
            class="timing-picker"
            mode="time"
            :value="draft.arrival_time"
            :disabled="draft.arrival_transport === '已在此地'"
            @change="draft.arrival_time = $event.detail.value"
          >
            <view class="timing-select-row" :class="{ disabled: draft.arrival_transport === '已在此地' }">
              <text class="select-caption">抵达时间</text>
              <text class="select-value">{{ draft.arrival_transport === '已在此地' ? '无需填写' : (draft.arrival_time || '请选择') }} ›</text>
            </view>
          </picker>
          <picker
            v-if="draft.arrival_transport !== '已在此地' && draft.arrival_transport !== '自驾'"
            class="timing-picker"
            mode="selector"
            :range="arrivalHubLabels"
            :value="arrivalHubIndex"
            :disabled="arrivalHubsLoading || arrivalHubLabels.length <= 1"
            @change="onArrivalHubChange"
          >
            <view class="timing-select-row hub-row" :class="{ disabled: arrivalHubsLoading }">
              <text class="select-caption">{{ arrivalBoundaryCity }}到达站点</text>
              <text class="select-value">{{ arrivalHubDisplay }} ›</text>
            </view>
          </picker>
        </view>
        <view class="timing-column">
          <text class="timing-label">离开 {{ departureBoundaryCity }}</text>
          <text class="timing-date">{{ formatShortDate(draft.end_date) }}</text>
          <picker class="timing-picker" mode="selector" :range="departureOptions" :value="departureIndex" @change="onDepartureTransportChange">
            <view class="timing-select-row">
              <text class="select-caption">交通方式</text>
              <text class="select-value">{{ departureOptions[departureIndex] }} ⌄</text>
            </view>
          </picker>
          <picker class="timing-picker" mode="time" :value="draft.departure_time" @change="draft.departure_time = $event.detail.value">
            <view class="timing-select-row">
              <text class="select-caption">离开时间</text>
              <text class="select-value">{{ draft.departure_time || '请选择' }} ›</text>
            </view>
          </picker>
          <picker
            v-if="draft.departure_transport !== '自驾'"
            class="timing-picker"
            mode="selector"
            :range="departureHubLabels"
            :value="departureHubIndex"
            :disabled="departureHubsLoading || departureHubLabels.length <= 1"
            @change="onDepartureHubChange"
          >
            <view class="timing-select-row hub-row" :class="{ disabled: departureHubsLoading }">
              <text class="select-caption">{{ departureBoundaryCity }}离开站点</text>
              <text class="select-value">{{ departureHubDisplay }} ›</text>
            </view>
          </picker>
        </view>
        <text class="playable-tip">{{ playableTip }}</text>
      </view>

      <view class="pace-card">
        <text class="section-title no-margin">旅行节奏</text>
        <view class="pace-options">
          <view
            v-for="pace in paceOptions"
            :key="pace"
            class="pace-chip"
            :class="{ active: selectedPace === pace }"
            @click="selectPace(pace)"
          >{{ pace }}</view>
        </view>
      </view>

      <view class="preference-card">
        <view class="section-row">
          <text class="section-title no-margin">更喜欢什么</text>
          <text class="section-tip">可多选，之后也能修改</text>
        </view>
        <view class="chips">
          <view
            v-for="item in preferenceOptions"
            :key="item"
            class="chip"
            :class="{ active: draft.preferences.includes(item) }"
            @click="togglePreference(item)"
          >{{ item }}</view>
        </view>
        <textarea
          v-model="draft.free_text_input"
          class="note-input"
          maxlength="500"
          placeholder="补充：不早起，想体验当地早餐"
          placeholder-style="color:#718188"
        />
      </view>

      <view class="config-card compact">
        <picker mode="selector" :range="transportOptions" :value="transportIndex" @change="onTransportChange">
          <view class="config-cell">
            <text class="cell-label">市内交通</text>
            <text class="cell-value">{{ transportOptions[transportIndex] }} ›</text>
          </view>
        </picker>
        <view class="config-divider"></view>
        <picker mode="selector" :range="accommodationOptions" :value="accommodationIndex" @change="onAccommodationChange">
          <view class="config-cell">
            <text class="cell-label">住宿偏好</text>
            <text class="cell-value">{{ accommodationOptions[accommodationIndex] }} ›</text>
          </view>
        </picker>
      </view>

      <view class="account-state" :class="{ guest: !session }">
        <text>{{ session ? '✓ 已登录，将保存到你的行程' : '登录后可跨设备保存行程' }}</text>
        <text>{{ session ? '已同步' : '稍后登录' }}</text>
      </view>

      <view class="ai-note">AI 生成内容仅供参考，出发前请核对开放时间。</view>

      <view class="bottom-space"></view>
    </scroll-view>

    <view class="footer-action">
      <button class="primary-button" @click="continuePlanning">
        {{ primaryActionLabel }}
      </button>
    </view>
  </view>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import { fetchTransportHubs, getSession } from '@/api/index.js'

const transportOptions = ['公共交通', '自驾', '步行 + 公交', '智能混合']
const accommodationOptions = ['经济型酒店', '舒适型酒店', '豪华酒店', '特色民宿']
const preferenceOptions = ['人文历史', '自然风景', '当地美食', '亲子', '小众路线', '摄影', '夜游']
const paceOptions = ['轻松慢游', '适中', '紧凑']
const pacePreferenceMap = {
  轻松慢游: '慢节奏',
  适中: '适中节奏',
  紧凑: '紧凑行程'
}
const arrivalOptions = ['高铁', '飞机', '火车', '自驾', '已在此地']
const departureOptions = ['高铁', '飞机', '火车', '自驾']
const session = ref(null)
const selectedPace = ref('轻松慢游')
const arrivalHubs = ref([])
const departureHubs = ref([])
const arrivalHubsLoading = ref(false)
const departureHubsLoading = ref(false)

const draft = reactive({
  city: '',
  start_date: '',
  end_date: '',
  travel_days: 1,
  trip_mode: 'single_city',
  transportation: '公共交通',
  accommodation: '经济型酒店',
  preferences: [],
  free_text_input: '',
  arrival_transport: '高铁',
  arrival_time: '',
  arrival_location: '',
  arrival_location_poi_id: '',
  arrival_location_longitude: null,
  arrival_location_latitude: null,
  departure_transport: '高铁',
  departure_time: '',
  departure_location: '',
  departure_location_poi_id: '',
  departure_location_longitude: null,
  departure_location_latitude: null,
  destinations: [],
  return_to_start: false,
  intercity_transport: 'auto',
  max_drive_hours_per_day: 6
})

onLoad(async () => {
  try {
    const saved = JSON.parse(uni.getStorageSync('tripDraft') || 'null')
    if (!saved) {
      uni.showToast({ title: '请先填写目的地和日期', icon: 'none' })
      setTimeout(() => uni.navigateBack(), 500)
      return
    }
    Object.assign(draft, saved)
    if (!draft.arrival_transport) draft.arrival_transport = '高铁'
    if (!draft.departure_transport) {
      draft.departure_transport = draft.arrival_transport === '已在此地'
        ? '高铁'
        : draft.arrival_transport
    }
    const matchedPace = paceOptions.find(pace => draft.preferences.includes(pacePreferenceMap[pace]))
    if (matchedPace) selectedPace.value = matchedPace
    await Promise.all([
      loadTransportHubs('arrival'),
      loadTransportHubs('departure')
    ])
  } catch {
    uni.navigateBack()
  }
})

onShow(() => {
  session.value = getSession()
})

const transportIndex = computed(() => {
  const map = { 公共交通: 0, 自驾: 1, '步行 + 公交': 2, 智能混合: 3, 混合: 3 }
  return map[draft.transportation] ?? 0
})

const accommodationIndex = computed(() => accommodationOptions.indexOf(draft.accommodation) < 0 ? 0 : accommodationOptions.indexOf(draft.accommodation))
const arrivalIndex = computed(() => Math.max(0, arrivalOptions.indexOf(draft.arrival_transport)))
const departureIndex = computed(() => Math.max(0, departureOptions.indexOf(draft.departure_transport)))
const routeCities = computed(() => (
  Array.isArray(draft.destinations)
    ? draft.destinations.map(item => String(item || '').trim()).filter(Boolean)
    : []
))
const arrivalBoundaryCity = computed(() => (
  draft.trip_mode === 'single_city' || !routeCities.value.length
    ? draft.city
    : routeCities.value[0]
))
const departureBoundaryCity = computed(() => {
  if (draft.trip_mode === 'single_city' || !routeCities.value.length) return draft.city
  if (draft.return_to_start || draft.trip_mode === 'road_trip_loop') return routeCities.value[0]
  return routeCities.value[routeCities.value.length - 1]
})
const boundarySummary = computed(() => (
  arrivalBoundaryCity.value === departureBoundaryCity.value
    ? `本次路线从${arrivalBoundaryCity.value}进出`
    : `本次路线从${arrivalBoundaryCity.value}进入、从${departureBoundaryCity.value}离开`
))
const arrivalHubLabels = computed(() => [
  draft.arrival_transport === '飞机' ? '请选择具体机场' : '请选择具体车站',
  ...arrivalHubs.value.map(item => item.name)
])
const departureHubLabels = computed(() => [
  draft.departure_transport === '飞机' ? '请选择具体机场' : '请选择具体车站',
  ...departureHubs.value.map(item => item.name)
])
const arrivalHubIndex = computed(() => {
  const index = arrivalHubs.value.findIndex(item => (
    item.id === draft.arrival_location_poi_id || item.name === draft.arrival_location
  ))
  return index < 0 ? 0 : index + 1
})
const departureHubIndex = computed(() => {
  const index = departureHubs.value.findIndex(item => (
    item.id === draft.departure_location_poi_id || item.name === draft.departure_location
  ))
  return index < 0 ? 0 : index + 1
})
const arrivalHubDisplay = computed(() => {
  if (arrivalHubsLoading.value) return '正在加载…'
  if (!arrivalHubs.value.length) return '暂未找到可选站点'
  return draft.arrival_location || (draft.arrival_transport === '飞机' ? '请选择机场' : '请选择车站')
})
const departureHubDisplay = computed(() => {
  if (departureHubsLoading.value) return '正在加载…'
  if (!departureHubs.value.length) return '暂未找到可选站点'
  return draft.departure_location || (draft.departure_transport === '飞机' ? '请选择机场' : '请选择车站')
})
const primaryActionLabel = computed(() => {
  return session.value ? '开始生成行程' : '登录并开始生成'
})

const playableTip = computed(() => {
  const arrivalHour = draft.arrival_time ? Number(draft.arrival_time.slice(0, 2)) : null
  const departureHour = draft.departure_time ? Number(draft.departure_time.slice(0, 2)) : null
  const first = arrivalHour === null ? '首日时间待定' : `首日约 ${Math.max(0, 19 - arrivalHour)} 小时可游玩`
  const last = departureHour === null ? '末日时间待定' : `末日约 ${Math.max(0, departureHour - 9)} 小时可游玩`
  return `${first} · ${last}`
})

function onTransportChange(event) {
  const selected = transportOptions[event.detail.value]
  // 两种模式的路线决策不同，必须原样传给后端，不能都压成“混合”。
  draft.transportation = selected
}

function onAccommodationChange(event) {
  draft.accommodation = accommodationOptions[event.detail.value]
}

function onArrivalTransportChange(event) {
  draft.arrival_transport = arrivalOptions[event.detail.value]
  clearHubSelection('arrival')
  if (draft.arrival_transport === '已在此地') {
    draft.arrival_time = ''
  }
  loadTransportHubs('arrival')
}

function onDepartureTransportChange(event) {
  draft.departure_transport = departureOptions[event.detail.value]
  clearHubSelection('departure')
  loadTransportHubs('departure')
}

function clearHubSelection(kind) {
  draft[`${kind}_location`] = ''
  draft[`${kind}_location_poi_id`] = ''
  draft[`${kind}_location_longitude`] = null
  draft[`${kind}_location_latitude`] = null
}

function applyHubSelection(kind, hub) {
  draft[`${kind}_location`] = hub?.name || ''
  draft[`${kind}_location_poi_id`] = hub?.id || ''
  draft[`${kind}_location_longitude`] = hub?.longitude ?? null
  draft[`${kind}_location_latitude`] = hub?.latitude ?? null
}

async function loadTransportHubs(kind) {
  const transport = draft[`${kind}_transport`]
  const listRef = kind === 'arrival' ? arrivalHubs : departureHubs
  const loadingRef = kind === 'arrival' ? arrivalHubsLoading : departureHubsLoading
  const targetCity = kind === 'arrival' ? arrivalBoundaryCity.value : departureBoundaryCity.value
  if (!['高铁', '火车', '飞机'].includes(transport) || !targetCity) {
    listRef.value = []
    return
  }
  loadingRef.value = true
  const requestedTransport = transport
  const requestedCity = targetCity
  try {
    const response = await fetchTransportHubs(targetCity, transport)
    const currentCity = kind === 'arrival' ? arrivalBoundaryCity.value : departureBoundaryCity.value
    if (draft[`${kind}_transport`] !== requestedTransport || currentCity !== requestedCity) return
    listRef.value = Array.isArray(response?.data) ? response.data : []
    const selected = listRef.value.find(item => (
      item.id === draft[`${kind}_location_poi_id`] ||
      item.name === draft[`${kind}_location`]
    ))
    if (selected) applyHubSelection(kind, selected)
    else clearHubSelection(kind)
  } catch (error) {
    listRef.value = []
    clearHubSelection(kind)
    uni.showToast({ title: error.message || '站点加载失败', icon: 'none' })
  } finally {
    loadingRef.value = false
  }
}

function onArrivalHubChange(event) {
  const index = Number(event.detail.value) - 1
  applyHubSelection('arrival', index >= 0 ? arrivalHubs.value[index] : null)
}

function onDepartureHubChange(event) {
  const index = Number(event.detail.value) - 1
  applyHubSelection('departure', index >= 0 ? departureHubs.value[index] : null)
}

function togglePreference(item) {
  const index = draft.preferences.indexOf(item)
  if (index >= 0) draft.preferences.splice(index, 1)
  else draft.preferences.push(item)
}

function selectPace(pace) {
  selectedPace.value = pace
  const paceTags = Object.values(pacePreferenceMap)
  draft.preferences = draft.preferences.filter(item => !paceTags.includes(item))
  draft.preferences.push(pacePreferenceMap[pace])
}

function formatShortDate(value) {
  if (!value) return '日期待定'
  const [, month, day] = value.split('-')
  return `${Number(month)}月${Number(day)}日`
}

function buildRequest() {
  return {
    ...draft,
    arrival_transport: draft.arrival_transport || undefined,
    arrival_time: draft.arrival_time || undefined,
    arrival_location: draft.arrival_location?.trim() || undefined,
    departure_transport: draft.departure_transport || undefined,
    departure_time: draft.departure_time || undefined,
    departure_location: draft.departure_location?.trim() || undefined,
    start_location: draft.destinations?.[0] || undefined,
    end_location: draft.return_to_start
      ? draft.destinations?.[0]
      : (draft.destinations?.length ? draft.destinations[draft.destinations.length - 1] : undefined)
  }
}

function continuePlanning() {
  if (
    ['高铁', '火车', '飞机'].includes(draft.arrival_transport) &&
    !draft.arrival_location_poi_id
  ) {
    uni.showToast({ title: `请选择${arrivalBoundaryCity.value}到达站点`, icon: 'none' })
    return
  }
  if (
    ['高铁', '火车', '飞机'].includes(draft.departure_transport) &&
    !draft.departure_location_poi_id
  ) {
    uni.showToast({ title: `请选择${departureBoundaryCity.value}离开站点`, icon: 'none' })
    return
  }
  uni.setStorageSync('tripDraft', JSON.stringify(draft))
  uni.setStorageSync('tripDraftActive', '1')

  uni.setStorageSync('pendingTripRequest', JSON.stringify(buildRequest()))
  session.value = getSession()
  if (!session.value) {
    uni.setStorageSync('postLoginRoute', '/pages/generating/generating')
    uni.navigateTo({ url: '/pages/account/account' })
    return
  }
  uni.navigateTo({ url: '/pages/generating/generating' })
}

function goBack() {
  uni.navigateBack()
}
</script>

<style scoped>
.preferences-page {
  height: 100vh;
  padding-top: calc(var(--status-bar-height) + 12rpx);
  display: flex;
  flex-direction: column;
  background: #f8f7fc;
  overflow: hidden;
}

.page-header {
  flex-shrink: 0;
  height: 76rpx;
  padding: 0 32rpx;
  display: flex;
  align-items: center;
}

.back {
  width: 58rpx;
  color: #302a45;
  font-size: 52rpx;
  line-height: 1;
}

.header-title {
  font-size: 34rpx;
  font-weight: 700;
}

.step {
  margin-left: 18rpx;
  color: #7d768f;
  font-size: 23rpx;
}

.progress-track {
  flex-shrink: 0;
  height: 10rpx;
  margin: 0 40rpx;
  overflow: hidden;
  border-radius: 5rpx;
  background: #e8e4f2;
}

.progress-value {
  width: 66.666%;
  height: 100%;
  background: #6658e8;
}

.content {
  width: 100%;
  height: 0;
  min-height: 0;
  flex: 1;
  padding: 30rpx 32rpx 0;
}

.headline,
.subhead,
.section-title {
  display: block;
}

.headline {
  font-size: 38rpx;
  font-weight: 800;
}

.subhead {
  margin-top: 8rpx;
  color: #7d768f;
  font-size: 24rpx;
}

.section-title {
  margin: 40rpx 8rpx 18rpx;
  color: #4f4862;
  font-size: 27rpx;
  font-weight: 700;
}

.section-title.no-margin {
  margin: 0;
}

.config-card,
.timing-card {
  position: relative;
  padding: 28rpx;
  display: flex;
  border: 2rpx solid #e9e6f2;
  border-radius: 34rpx;
  background: #fff;
}

.config-card picker,
.config-cell,
.timing-column {
  flex: 1;
  min-width: 0;
}

.config-divider {
  width: 2rpx;
  min-height: 92rpx;
  margin: 0 28rpx;
  background: #ece9f3;
}

.cell-label,
.cell-value,
.timing-value,
.time-value {
  display: block;
}

.cell-label {
  color: #7d768f;
  font-size: 22rpx;
}

.cell-value,
.timing-value {
  margin-top: 16rpx;
  color: #302a45;
  font-size: 28rpx;
  font-weight: 600;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 18rpx;
}

.chip {
  padding: 14rpx 25rpx;
  border: 2rpx solid #dfdbea;
  border-radius: 34rpx;
  background: #fff;
  color: #5c556d;
  font-size: 24rpx;
}

.chip.active {
  border-color: #6658e8;
  background: #6658e8;
  color: #fff;
  font-weight: 600;
}

.section-row {
  margin: 42rpx 8rpx 18rpx;
  display: flex;
  align-items: center;
}

.section-tip {
  margin-left: auto;
  padding-left: 16rpx;
  color: #8a8497;
  font-size: 20rpx;
  text-align: right;
}

.timing-card {
  padding-bottom: 76rpx;
}

.time-value {
  margin-top: 10rpx;
  color: #8a8497;
  font-size: 22rpx;
}

.playable-tip {
  position: absolute;
  left: 28rpx;
  right: 28rpx;
  bottom: 24rpx;
  color: #6658e8;
  font-size: 21rpx;
  font-weight: 600;
}

.note-input {
  width: 100%;
  min-height: 132rpx;
  padding: 24rpx;
  border: 2rpx solid #dfdbea;
  border-radius: 30rpx;
  background: #fff;
  color: #302a45;
  font-size: 25rpx;
}

.bottom-space {
  height: 28rpx;
}

.footer-action {
  position: relative;
  flex-shrink: 0;
  z-index: 10;
  padding: 18rpx 32rpx calc(18rpx + env(safe-area-inset-bottom));
  background: rgba(248, 247, 252, 0.96);
}

/* 手绘偏好页 */
.preferences-page {
  background: var(--background);
}

.page-header {
  height: 92rpx;
  padding: 0 40rpx;
}

.back {
  color: var(--text);
}

.header-title {
  flex: 1;
  color: var(--text);
  font-size: 38rpx;
}

.step {
  margin-left: auto;
  color: var(--muted);
  font-size: 24rpx;
}

.progress-track {
  background: #e4e0d5;
}

.progress-value {
  background: var(--accent);
}

.content {
  padding: 40rpx 40rpx 0;
}

.timing-card,
.pace-card,
.preference-card,
.config-card.compact {
  border: 2rpx solid var(--border);
  border-radius: 40rpx;
  background: var(--surface);
}

.timing-card {
  padding: 34rpx;
  display: flex;
  flex-wrap: wrap;
  gap: 22rpx;
}

.timing-card .section-title,
.timing-intro,
.playable-tip {
  width: 100%;
}

.section-title {
  color: var(--text);
  font-size: 30rpx;
}

.timing-intro {
  display: block;
  color: var(--muted);
  font-size: 23rpx;
}

.timing-column {
  position: relative;
  flex: 0 0 calc(50% - 11rpx);
  min-height: 258rpx;
  padding: 24rpx;
  border-radius: 30rpx;
  background: var(--brand-soft);
}

.timing-column + .timing-column {
  background: var(--accent-soft);
}

.timing-label,
.timing-date {
  display: block;
}

.timing-label {
  color: var(--text);
  font-size: 25rpx;
  font-weight: 700;
}

.timing-label.arrival {
  color: var(--brand);
}

.timing-date {
  margin-top: 12rpx;
  color: var(--muted);
  font-size: 22rpx;
}

.timing-picker {
  display: block;
  margin-top: 14rpx;
}

.timing-select-row {
  min-height: 62rpx;
  padding: 0 18rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10rpx;
  border: 2rpx solid rgba(72, 62, 94, 0.12);
  border-radius: 22rpx;
  background: rgba(255, 255, 255, 0.72);
}

.timing-select-row.disabled {
  opacity: 0.52;
}

.hub-row {
  margin-top: 14rpx;
}

.hub-input {
  min-width: 0;
  flex: 1;
  color: var(--text);
  font-size: 21rpx;
  font-weight: 650;
  text-align: right;
}

.select-caption {
  flex-shrink: 0;
  color: var(--muted);
  font-size: 19rpx;
}

.select-value {
  min-width: 0;
  overflow: hidden;
  color: var(--text);
  font-size: 22rpx;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.playable-tip {
  position: static;
  color: var(--brand);
  font-size: 21rpx;
}

.pace-card,
.preference-card {
  margin-top: 32rpx;
  padding: 34rpx;
}

.pace-options {
  margin-top: 26rpx;
  display: flex;
  gap: 16rpx;
}

.pace-chip {
  min-width: 0;
  flex: 1;
  padding: 18rpx 10rpx;
  border: 2rpx solid var(--border);
  border-radius: 40rpx;
  color: var(--text);
  font-size: 25rpx;
  font-weight: 600;
  text-align: center;
}

.pace-chip.active {
  border-color: var(--brand);
  background: var(--brand);
  color: #fff;
}

.preference-card .section-row {
  margin: 0 0 26rpx;
}

.section-tip {
  color: var(--muted);
}

.chips {
  gap: 16rpx;
}

.chip {
  border-color: var(--border);
  background: var(--background);
  color: var(--muted);
}

.chip.active {
  border-color: transparent;
  background: var(--brand-soft);
  color: var(--brand);
}

.note-input {
  min-height: 92rpx;
  margin-top: 24rpx;
  padding: 20rpx 0 0;
  border: 0;
  border-top: 2rpx dashed var(--border);
  border-radius: 0;
  background: transparent;
  color: var(--text);
}

.config-card.compact {
  margin-top: 32rpx;
  padding: 28rpx;
}

.cell-label {
  color: var(--muted);
}

.cell-value {
  color: var(--text);
  font-size: 24rpx;
}

.config-divider {
  background: var(--border);
}

.account-state,
.ai-note {
  margin-top: 28rpx;
  padding: 28rpx;
  display: flex;
  justify-content: space-between;
  border-radius: 30rpx;
  font-size: 23rpx;
}

.account-state {
  background: var(--success-soft);
  color: var(--success);
}

.account-state.guest {
  background: var(--accent-soft);
  color: var(--warning);
}

.ai-note {
  display: block;
  background: var(--brand-soft);
  color: var(--text);
}

.footer-action {
  padding: 18rpx 40rpx calc(24rpx + env(safe-area-inset-bottom));
  background: rgba(251, 248, 239, 0.98);
}

.footer-action .primary-button {
  background: var(--accent);
}
</style>
