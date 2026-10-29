<template>
  <view class="route-page">
    <view class="page-header">
      <text class="back" @click="goBack">‹</text>
      <text class="header-title">先把路线画顺</text>
      <text class="step">第 1 / 3 步</text>
    </view>
    <view class="progress-track"><view class="progress-value"></view></view>

    <scroll-view class="content" scroll-y>
      <view class="suggestion-card">
        <text class="suggestion-label">AI 路线草案</text>
        <text class="suggestion-title">{{ draft.city }} · {{ draft.travel_days }} 日{{ isLoop ? '环线' : '慢游' }}</text>
        <text class="suggestion-meta">{{ nodes.length || '待推荐' }} 城 · {{ isLoop ? '自驾优先' : intercityTransportLabels[intercityTransportIndex] }} · 可修改</text>
        <text class="suggestion-copy">{{ suggestionCopy }}</text>
        <view class="route-stamp"><text>⌁</text></view>
      </view>

      <picker
        v-if="!isLoop"
        mode="selector"
        :range="intercityTransportLabels"
        :value="intercityTransportIndex"
        @change="onIntercityTransportChange"
      >
        <view class="transport-card">
          <view>
            <text class="transport-label">城际交通</text>
            <text class="transport-tip">用于估算城市之间的移动时间</text>
          </view>
          <text class="transport-value">{{ intercityTransportLabels[intercityTransportIndex] }} ›</text>
        </view>
      </picker>

      <view v-if="routeOptions.length > 1" class="option-tabs">
        <view
          v-for="(option, index) in routeOptions"
          :key="option.id"
          class="option-tab"
          :class="{ active: selectedIndex === index }"
          @click="selectOption(index)"
        >
          {{ option.name }}
        </view>
      </view>

      <view class="route-heading">
        <text class="route-title">AI 推荐的游玩顺序</text>
        <text class="route-tip">点箭头调整，点城市可替换</text>
      </view>

      <view v-if="loading" class="loading-card">
        <view class="loading-dot"></view>
        <text>{{ isLoop ? 'AI 正在分析区域、天数和每日驾驶强度……' : 'AI 正在根据天数推荐城市与跨城顺序……' }}</text>
      </view>

      <view v-else class="node-list">
        <view class="route-line"></view>
        <view v-for="(node, index) in nodes" :key="`${node}-${index}`" class="node-row">
          <view class="node-number">{{ index + 1 }}</view>
          <view class="node-card">
            <view class="node-main">
              <text class="node-name">{{ node }}</text>
              <text class="node-meta">{{ segmentText(index) }}</text>
            </view>
            <view class="node-actions">
              <text class="move-action" :class="{ disabled: index === 0 }" @click="moveNode(index, -1)">↑</text>
              <text class="move-action" :class="{ disabled: index === nodes.length - 1 }" @click="moveNode(index, 1)">↓</text>
              <text class="remove-action" @click="removeNode(index)">×</text>
            </view>
          </view>
        </view>
      </view>

      <view class="add-node">
        <input
          v-model="newNode"
          class="add-input"
          placeholder="添加途经点"
          placeholder-style="color:#aaa4b8"
          @confirm="addNode"
        />
        <text class="add-action" @click="addNode">＋ 添加</text>
      </view>

      <button class="validate-button" :loading="validating" @click="validateEdited">
        重新校验路线
      </button>

      <view v-if="warnings.length" class="warning-card">
        <text class="warning-title">出行提示</text>
        <text v-for="warning in warnings" :key="warning" class="warning-item">· {{ warning }}</text>
      </view>

      <view class="summary-card" v-if="selectedOption">
        <view class="summary-item">
          <text class="summary-value">{{ Math.round(selectedOption.total_distance_km || 0) }}</text>
          <text class="summary-label">参考公里</text>
        </view>
        <view class="summary-item">
          <text class="summary-value">{{ routeHours }}</text>
          <text class="summary-label">{{ isLoop ? '总驾驶小时' : '城际参考小时' }}</text>
        </view>
        <view class="summary-item">
          <text class="summary-value">{{ Math.round(selectedOption.fit_score || 0) }}</text>
          <text class="summary-label">适配分</text>
        </view>
      </view>

      <view class="bottom-space"></view>
    </scroll-view>

    <view class="footer-action">
      <button class="primary-button" :disabled="loading || nodes.length < 2" @click="confirmRoute">
        确认路线，继续选偏好
      </button>
    </view>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import {
  recommendRouteOptions,
  validateRouteDraft
} from '@/api/index.js'

const draft = ref({})
const routeOptions = ref([])
const selectedIndex = ref(0)
const nodes = ref([])
const warnings = ref([])
const loading = ref(true)
const validating = ref(false)
const newNode = ref('')

const intercityTransports = [
  { label: '智能推荐', value: 'auto' },
  { label: '高铁/动车优先', value: 'high_speed_rail' },
  { label: '普通火车/城际交通', value: 'train' },
  { label: '飞机优先', value: 'flight' },
  { label: '自驾', value: 'self_drive' }
]
const intercityTransportLabels = intercityTransports.map(item => item.label)

const selectedOption = computed(() => routeOptions.value[selectedIndex.value] || null)
const isLoop = computed(() => draft.value.trip_mode === 'road_trip_loop')
const intercityTransportIndex = computed(() => {
  const index = intercityTransports.findIndex(item => item.value === draft.value.intercity_transport)
  return index < 0 ? 0 : index
})
const routeHours = computed(() => {
  const minutes = selectedOption.value?.total_drive_minutes || 0
  return minutes ? (minutes / 60).toFixed(1) : '—'
})
const suggestionTitle = computed(() => {
  if (draft.value.travel_days <= 4) return 'AI 为你推荐了一条舒适短线'
  return selectedOption.value?.name || (isLoop.value ? 'AI 为你推荐了区域环线' : 'AI 为你推荐了跨城路线')
})
const suggestionCopy = computed(() => {
  if (loading.value) return '正在生成适合当前天数的路线骨架。'
  if (selectedOption.value?.summary) return selectedOption.value.summary
  if (draft.value.travel_days <= 4) return '时间不足以完成完整大环线，已优先保留经典节点。'
  return isLoop.value
    ? '路线已按每日驾驶上限完成初步校验，你仍可调整节点。'
    : '路线已结合天数与城际移动耗时完成初步校验，你仍可增删和排序。'
})

onLoad(async () => {
  try {
    draft.value = JSON.parse(uni.getStorageSync('tripDraft') || '{}')
    if (!draft.value.city) throw new Error('缺少行程草稿')
    await loadOptions()
  } catch (error) {
    uni.showToast({ title: error.message || '路线加载失败', icon: 'none' })
    loading.value = false
  }
})

async function loadOptions() {
  loading.value = true
  try {
    let options
    if (draft.value.route_source === 'manual' && draft.value.destinations?.length >= 2) {
      const validated = await validateRouteDraft({
        region: draft.value.city,
        nodes: draft.value.destinations,
        travel_days: draft.value.travel_days,
        max_drive_hours_per_day: draft.value.max_drive_hours_per_day || 6,
        trip_mode: draft.value.trip_mode,
        intercity_transport: draft.value.intercity_transport || 'auto'
      })
      options = [validated]
    } else {
      options = await recommendRouteOptions({
        region: draft.value.city,
        travel_days: draft.value.travel_days,
        start_location: undefined,
        preferences: draft.value.preferences || [],
        max_drive_hours_per_day: draft.value.max_drive_hours_per_day || 6,
        trip_mode: draft.value.trip_mode,
        intercity_transport: draft.value.intercity_transport || (isLoop.value ? 'self_drive' : 'auto')
      })
    }
    routeOptions.value = Array.isArray(options) ? options : []
    selectedIndex.value = 0
    applySelectedOption()
  } catch (error) {
    const fallback = createShortRouteFallback()
    if (!fallback) throw error
    routeOptions.value = [fallback]
    selectedIndex.value = 0
    applySelectedOption()
  } finally {
    loading.value = false
  }
}

function applySelectedOption() {
  const option = selectedOption.value
  const optionNodes = option?.nodes ? [...option.nodes] : [draft.value.city]
  if (isLoop.value && optionNodes.length > 1 && optionNodes[0] === optionNodes[optionNodes.length - 1]) {
    optionNodes.pop()
  }
  nodes.value = optionNodes
  warnings.value = option?.warnings ? [...option.warnings] : []
}

function createShortRouteFallback() {
  const region = draft.value.city || ''
  const fallbacks = [
    { keywords: ['西藏', '拉萨'], nodes: ['拉萨', '林芝', '巴松措'] },
    { keywords: ['新疆', '乌鲁木齐'], nodes: ['乌鲁木齐', '吐鲁番', '天山天池'] },
    { keywords: ['青海', '青甘'], nodes: ['西宁', '青海湖', '茶卡盐湖'] },
    { keywords: ['川西'], nodes: ['成都', '康定', '新都桥'] },
    { keywords: ['江西'], nodes: ['南昌', '景德镇', '婺源'] },
    { keywords: ['云南'], nodes: ['昆明', '大理', '丽江'] },
    { keywords: ['贵州'], nodes: ['贵阳', '安顺', '黔东南'] }
  ]
  const match = fallbacks.find(item => item.keywords.some(keyword => region.includes(keyword)))
  if (!match || draft.value.travel_days < 3) return null
  const selectedNodes = match.nodes.slice(0, Math.min(match.nodes.length, draft.value.travel_days))
  return {
    id: `short-${Date.now()}`,
    name: '经典精华短线',
    summary: isLoop.value
      ? '当前天数不足以完成完整大环线，已切换为常见精华短线；请确认节点后再生成详细日程。'
      : '暂时未取得在线推荐，已提供一条可编辑的经典跨城短线。',
    nodes: selectedNodes,
    segments: [],
    total_distance_km: 0,
    total_drive_minutes: 0,
    fit_score: 75,
    warnings: [isLoop.value
      ? '短线不是完整闭环，返程路段需要出发前再次确认。'
      : '当前城际耗时为参考值，真实车次与班次需要出发前确认。']
  }
}

function selectOption(index) {
  selectedIndex.value = index
  applySelectedOption()
}

function segmentText(index) {
  if (index === 0) return isLoop.value ? '环线起点 · 先适应当地节奏' : '跨城路线起点'
  const segment = selectedOption.value?.segments?.[index - 1]
  if (!segment) return `第 ${index + 1} 个节点`
  const hours = segment.duration_minutes ? `${(segment.duration_minutes / 60).toFixed(1)}小时` : '时间待确认'
  return `${segment.transport_mode || (isLoop.value ? '自驾' : '城际交通')} · ${hours}${segment.requires_confirmation ? ' · 需确认班次' : ''}`
}

async function onIntercityTransportChange(event) {
  const selected = intercityTransports[event.detail.value]
  draft.value.intercity_transport = selected.value
  if (nodes.value.length >= 2) await validateEdited()
}

function moveNode(index, delta) {
  const target = index + delta
  if (target < 0 || target >= nodes.value.length) return
  const copy = [...nodes.value]
  ;[copy[index], copy[target]] = [copy[target], copy[index]]
  nodes.value = copy
}

function removeNode(index) {
  if (nodes.value.length <= 2) {
    uni.showToast({ title: '路线至少保留 2 个节点', icon: 'none' })
    return
  }
  nodes.value.splice(index, 1)
}

function addNode() {
  const name = newNode.value.trim()
  if (!name) return
  if (nodes.value.includes(name)) {
    uni.showToast({ title: '该节点已在路线中', icon: 'none' })
    return
  }
  if (nodes.value.length >= 12) {
    uni.showToast({ title: '最多 12 个路线节点', icon: 'none' })
    return
  }
  nodes.value.push(name)
  newNode.value = ''
}

async function validateEdited() {
  if (nodes.value.length < 2) return
  validating.value = true
  try {
    const option = await validateRouteDraft({
      region: draft.value.city,
      nodes: nodes.value,
      travel_days: draft.value.travel_days,
      max_drive_hours_per_day: draft.value.max_drive_hours_per_day || 6,
      trip_mode: draft.value.trip_mode,
      intercity_transport: draft.value.intercity_transport || (isLoop.value ? 'self_drive' : 'auto')
    })
    routeOptions.value[selectedIndex.value] = option
    const validatedNodes = [...option.nodes]
    if (isLoop.value && validatedNodes.length > 1 && validatedNodes[0] === validatedNodes[validatedNodes.length - 1]) {
      validatedNodes.pop()
    }
    nodes.value = validatedNodes
    warnings.value = [...(option.warnings || [])]
    uni.showToast({ title: '路线校验完成', icon: 'success' })
  } catch (error) {
    uni.showModal({ title: '路线需要调整', content: error.message || '校验失败', showCancel: false })
  } finally {
    validating.value = false
  }
}

function confirmRoute() {
  if (nodes.value.length > draft.value.travel_days) {
    uni.showModal({
      title: '节点过多',
      content: `${draft.value.travel_days} 天最多建议保留 ${draft.value.travel_days} 个节点，请先删减。`,
      showCancel: false
    })
    return
  }

  const request = {
    ...draft.value,
    arrival_transport: draft.value.arrival_transport || undefined,
    arrival_time: draft.value.arrival_time || undefined,
    arrival_location: draft.value.arrival_location || undefined,
    departure_transport: draft.value.departure_transport || undefined,
    departure_time: draft.value.departure_time || undefined,
    departure_location: draft.value.departure_location || undefined,
    trip_mode: isLoop.value ? 'road_trip_loop' : 'multi_city',
    transportation: isLoop.value ? '自驾' : (draft.value.transportation || '公共交通'),
    destinations: [...nodes.value],
    start_location: nodes.value[0],
    end_location: isLoop.value ? nodes.value[0] : nodes.value[nodes.value.length - 1],
    return_to_start: isLoop.value,
    intercity_transport: isLoop.value ? 'self_drive' : (draft.value.intercity_transport || 'auto')
  }
  uni.setStorageSync('tripDraft', JSON.stringify(request))
  uni.setStorageSync('tripDraftActive', '1')
  uni.navigateTo({ url: '/pages/preferences/preferences' })
}

function goBack() {
  uni.navigateBack()
}
</script>

<style scoped>
.route-page {
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
  width: 100%;
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

.suggestion-card {
  padding: 28rpx;
  border-radius: 34rpx;
  background: #eeeafe;
}

.suggestion-title,
.suggestion-meta,
.suggestion-copy {
  display: block;
}

.suggestion-title {
  color: #5142be;
  font-size: 30rpx;
  font-weight: 700;
}

.suggestion-meta {
  margin-top: 12rpx;
  color: #6658e8;
  font-size: 23rpx;
  font-weight: 600;
}

.suggestion-copy {
  margin-top: 14rpx;
  color: #615a72;
  font-size: 23rpx;
  line-height: 1.55;
}

.transport-card {
  min-height: 104rpx;
  margin-top: 20rpx;
  padding: 20rpx 24rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border: 2rpx solid #e9e6f2;
  border-radius: 28rpx;
  background: #fff;
}

.transport-label,
.transport-tip {
  display: block;
}

.transport-label {
  color: #302a45;
  font-size: 26rpx;
  font-weight: 700;
}

.transport-tip {
  margin-top: 6rpx;
  color: #8a8497;
  font-size: 20rpx;
}

.transport-value {
  margin-left: 20rpx;
  color: #6658e8;
  font-size: 23rpx;
  font-weight: 600;
}

.option-tabs {
  margin-top: 28rpx;
  padding: 6rpx;
  display: flex;
  gap: 8rpx;
  border-radius: 24rpx;
  background: #edeaf4;
}

.option-tab {
  flex: 1;
  padding: 16rpx 12rpx;
  overflow: hidden;
  border-radius: 19rpx;
  color: #756e85;
  font-size: 23rpx;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.option-tab.active {
  background: #fff;
  color: #6658e8;
  font-weight: 700;
}

.route-heading {
  margin: 34rpx 8rpx 22rpx;
  display: flex;
  align-items: center;
}

.route-title {
  color: #4f4862;
  font-size: 28rpx;
  font-weight: 700;
}

.route-tip {
  margin-left: auto;
  color: #8a8497;
  font-size: 21rpx;
}

.loading-card {
  padding: 44rpx 30rpx;
  display: flex;
  align-items: center;
  gap: 18rpx;
  border: 2rpx solid #e9e6f2;
  border-radius: 30rpx;
  background: #fff;
  color: #6f6880;
  font-size: 24rpx;
}

.loading-dot {
  width: 20rpx;
  height: 20rpx;
  border-radius: 50%;
  background: #6658e8;
}

.node-list {
  position: relative;
}

.route-line {
  position: absolute;
  left: 35rpx;
  top: 42rpx;
  bottom: 42rpx;
  width: 4rpx;
  background: #c9c1f7;
}

.node-row {
  position: relative;
  z-index: 2;
  margin-bottom: 20rpx;
  display: flex;
  align-items: center;
  gap: 20rpx;
}

.node-number {
  width: 70rpx;
  height: 70rpx;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 6rpx solid #f8f7fc;
  border-radius: 50%;
  background: #6658e8;
  color: #fff;
  font-weight: 700;
}

.node-card {
  min-height: 120rpx;
  padding: 20rpx 18rpx 20rpx 24rpx;
  flex: 1;
  display: flex;
  align-items: center;
  border: 2rpx solid #e8e4f0;
  border-radius: 28rpx;
  background: #fff;
}

.node-main {
  min-width: 0;
  flex: 1;
}

.node-name,
.node-meta {
  display: block;
}

.node-name {
  color: #302a45;
  font-size: 27rpx;
  font-weight: 700;
}

.node-meta {
  margin-top: 10rpx;
  color: #7d768f;
  font-size: 20rpx;
}

.node-actions {
  display: flex;
  align-items: center;
  gap: 8rpx;
}

.move-action,
.remove-action {
  padding: 10rpx;
  color: #6658e8;
  font-size: 28rpx;
}

.move-action.disabled {
  color: #d4cfdd;
}

.remove-action {
  color: #d36b61;
}

.add-node {
  margin: 8rpx 0 20rpx 90rpx;
  padding: 0 20rpx;
  display: flex;
  align-items: center;
  border: 2rpx dashed #cfc8e5;
  border-radius: 26rpx;
}

.add-input {
  height: 82rpx;
  flex: 1;
  font-size: 24rpx;
}

.add-action {
  color: #6658e8;
  font-size: 23rpx;
  font-weight: 600;
}

.validate-button {
  height: 84rpx;
  line-height: 80rpx;
  margin: 22rpx 0 0;
  border: 2rpx solid #cfc8e5;
  border-radius: 42rpx;
  background: #fff;
  color: #6658e8;
  font-size: 25rpx;
}

.warning-card {
  margin-top: 24rpx;
  padding: 24rpx;
  border-radius: 26rpx;
  background: #fff4e8;
}

.warning-title,
.warning-item {
  display: block;
}

.warning-title {
  color: #c46b18;
  font-size: 24rpx;
  font-weight: 700;
}

.warning-item {
  margin-top: 10rpx;
  color: #805a32;
  font-size: 21rpx;
  line-height: 1.5;
}

.summary-card {
  margin-top: 24rpx;
  padding: 24rpx;
  display: flex;
  border: 2rpx solid #e9e6f2;
  border-radius: 28rpx;
  background: #fff;
}

.summary-item {
  flex: 1;
  text-align: center;
}

.summary-value,
.summary-label {
  display: block;
}

.summary-value {
  color: #5142be;
  font-size: 30rpx;
  font-weight: 700;
}

.summary-label {
  margin-top: 6rpx;
  color: #8a8497;
  font-size: 20rpx;
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

/* 手绘路线确认页 */
.route-page {
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
  margin: 0 40rpx;
  background: #e4e0d5;
}

.progress-value {
  width: 32%;
  background: var(--accent);
}

.content {
  padding: 38rpx 40rpx 0;
}

.suggestion-card {
  position: relative;
  min-height: 190rpx;
  padding: 34rpx;
  overflow: hidden;
  border-radius: 40rpx;
  background: var(--brand-soft);
}

.suggestion-label,
.suggestion-title,
.suggestion-meta,
.suggestion-copy {
  display: block;
}

.suggestion-label {
  color: var(--brand);
  font-size: 25rpx;
  font-weight: 700;
}

.suggestion-title {
  width: 480rpx;
  margin-top: 18rpx;
  color: var(--text);
  font-size: 40rpx;
}

.suggestion-meta {
  color: var(--muted);
  font-size: 23rpx;
}

.suggestion-copy {
  color: var(--muted);
}

.route-stamp {
  position: absolute;
  right: 36rpx;
  top: 42rpx;
  width: 92rpx;
  height: 92rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 5rpx dashed var(--accent);
  border-radius: 50%;
  color: var(--accent);
  font-size: 46rpx;
  transform: rotate(-8deg);
}

.transport-card,
.node-list,
.add-node,
.warning-card,
.summary-card {
  border-color: var(--border);
  background: var(--surface);
}

.transport-card {
  border-radius: 30rpx;
}

.transport-label,
.route-title,
.node-name,
.warning-title {
  color: var(--text);
}

.transport-tip,
.route-tip,
.node-meta,
.summary-label {
  color: var(--muted);
}

.transport-value,
.summary-value {
  color: var(--brand);
}

.option-tab.active {
  background: var(--brand);
}

.route-heading {
  margin-top: 36rpx;
  padding: 0 6rpx;
}

.route-title {
  font-size: 31rpx;
}

.node-list {
  position: relative;
  margin-top: 18rpx;
  padding: 28rpx 22rpx;
  border: 2rpx solid var(--border);
  border-radius: 40rpx;
}

.route-line {
  left: 47rpx;
  border-left: 5rpx dashed var(--brand);
  background: transparent;
}

.node-row {
  position: relative;
  z-index: 1;
}

.node-number {
  background: var(--accent);
  box-shadow: 0 0 0 8rpx var(--surface);
}

.node-row:nth-child(4n) .node-number {
  background: #f6be45;
}

.node-row:nth-child(5n) .node-number {
  background: var(--success);
}

.node-card {
  border-color: var(--border);
  border-radius: 28rpx;
  background: var(--background);
}

.move-action {
  color: var(--brand);
}

.remove-action {
  color: var(--accent);
}

.add-node {
  border-radius: 28rpx;
}

.add-action {
  color: var(--brand);
}

.validate-button {
  border-color: var(--border);
  color: var(--text);
  background: var(--accent-soft);
}

.warning-card {
  background: var(--accent-soft);
}

.summary-card {
  border-radius: 30rpx;
}

.footer-action {
  padding: 18rpx 40rpx calc(24rpx + env(safe-area-inset-bottom));
  background: rgba(251, 248, 239, 0.98);
}

.footer-action .primary-button {
  background: var(--accent);
}
</style>
