<template>
  <view class="generating-page">
    <view class="top-copy">
      <text class="title">{{ pageTitle }}</text>
      <text class="subtitle">{{ pageSubtitle }}</text>
    </view>

    <view class="progress-card">
      <view class="generation-route" aria-hidden="true">
        <view class="route-dash"></view>
        <view class="route-node node-one"></view>
        <view class="route-node node-two"></view>
        <view class="route-node node-three"></view>
        <view class="route-arrow">›</view>
      </view>
      <text class="progress-number">{{ progressLabel }}</text>
      <text class="current-message">{{ displayMessage }}</text>
    </view>

    <view class="steps-card">
      <view v-for="(step, index) in steps" :key="step.label" class="step-row">
        <view class="step-dot" :class="stepState(index)">
          <text>{{ stepMark(index) }}</text>
        </view>
        <view class="step-body">
          <text class="step-label" :class="{ current: stepState(index) === 'current' }">{{ step.label }}</text>
        </view>
        <text class="step-status" :class="stepState(index)">{{ stepStatusLabel(index) }}</text>
      </view>
    </view>

    <view class="recovery-card" :class="{ recovering }">
      <text class="recovery-title">{{ recoveryTitle }}</text>
      <text class="recovery-copy">{{ recoveryCopy }}</text>
      <text class="task-id">任务编号：{{ taskId || (failed ? '未创建' : '正在创建') }}</text>
    </view>

    <view v-if="failed" class="failed-card">
      <text class="failed-title">本次任务未能完成</text>
      <text class="failed-copy">{{ errorMessage }}</text>
    </view>

    <view class="action-row">
      <button class="secondary-button" @click="goHome">返回首页</button>
      <button class="primary-button" :disabled="!completed" @click="viewResult">查看行程</button>
    </view>
    <text v-if="!failed && !completed" class="estimate">预计还需 {{ estimatedText }}</text>
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import { onLoad, onUnload } from '@dcloudio/uni-app'
import {
  createTripPlanTask,
  getSession,
  getTripPlanTask,
  saveHistoryEntry
} from '@/api/index.js'

const taskId = ref('')
const status = ref('queued')
const progress = ref(0)
const message = ref('正在创建生成任务…')
const recovering = ref(false)
const failed = ref(false)
const failureStep = ref(0)
const completed = ref(false)
const errorMessage = ref('')
const requestData = ref(null)
let active = true

const steps = [
  { label: '理解需求与抵离时间', threshold: 10 },
  { label: '生成路线与每日节奏', threshold: 30 },
  { label: '匹配景点、餐厅和酒店', threshold: 55 },
  { label: '校验时间与风险提示', threshold: 80 },
  { label: '保存到你的账户', threshold: 96 }
]

const city = computed(() => requestData.value?.city || '目的地')
const pageTitle = computed(() => failed.value ? '这次行程未能生成' : completed.value ? `${city.value}行程已生成` : `正在规划你的${city.value}之旅`)
const pageSubtitle = computed(() => failed.value ? '请保留任务编号，方便反馈并定位问题' : completed.value ? '可以查看详细行程安排' : '可以离开此页，完成后会自动保存在历史行程')
const progressLabel = computed(() => failed.value ? '未完成' : `${progress.value}%`)
const currentStep = computed(() => {
  if (failed.value) return failureStep.value
  if (completed.value) return 4
  if (progress.value >= 80) return 3
  if (progress.value >= 55) return 2
  if (progress.value >= 30) return 1
  return 0
})
const displayMessage = computed(() => message.value || steps[currentStep.value].label)
const estimatedText = computed(() => progress.value < 30 ? '2–3 分钟' : progress.value < 75 ? '1–2 分钟' : '不到 1 分钟')
const progressBackground = computed(() => `conic-gradient(#6658e8 ${progress.value}%, #dcd6fa 0)`)
const recoveryTitle = computed(() => {
  if (failed.value) return taskId.value ? '本次任务已结束' : '生成任务尚未创建'
  if (completed.value) return '行程已经生成完成'
  if (recovering.value) return '正在恢复任务连接'
  if (taskId.value) return '生成任务已安全保存'
  return '正在创建生成任务'
})
const recoveryCopy = computed(() => {
  if (failed.value) return taskId.value
    ? '该任务已结束，不会自动继续。可返回首页重新生成；反馈问题时请保留任务编号。'
    : '任务尚未创建，请核对提示后返回首页重试。'
  if (completed.value) return '行程已保存到历史记录，点击“查看行程”进入详细日程。'
  if (recovering.value) return '网络暂时不可用，系统会自动重新连接，无需重新提交。'
  if (taskId.value) return '若网络中断或关闭小程序，下次进入会从当前任务继续。'
  return '正在校验行程信息并提交到后台。'
})

function normalizeTripRequest(value) {
  const normalized = { ...value }
  if (!normalized.travel_pace) normalized.travel_pace = 'relaxed'
  for (const field of [
    'arrival_transport', 'arrival_time', 'arrival_location',
    'departure_transport', 'departure_time', 'departure_location'
  ]) {
    if (!normalized[field]) delete normalized[field]
  }
  delete normalized.route_source
  return normalized
}

onLoad(async () => {
  active = true
  if (!getSession()) {
    uni.setStorageSync('postLoginRoute', '/pages/generating/generating')
    uni.redirectTo({ url: '/pages/account/account' })
    return
  }

  try {
    const pendingRequest = JSON.parse(uni.getStorageSync('pendingTripRequest') || 'null')
    requestData.value = pendingRequest ? normalizeTripRequest(pendingRequest) : null
    if (!requestData.value) throw new Error('没有待生成的行程')
    uni.setStorageSync('pendingTripRequest', JSON.stringify(requestData.value))

    const savedTask = JSON.parse(uni.getStorageSync('tripGenerationTask') || 'null')
    const requestHash = JSON.stringify(requestData.value)

    if (
      savedTask?.task_id &&
      savedTask.request_hash === requestHash &&
      ['queued', 'running'].includes(savedTask.status)
    ) {
      taskId.value = savedTask.task_id
      status.value = savedTask.status
      progress.value = savedTask.progress || 0
      message.value = savedTask.message || '正在恢复生成任务…'
      recovering.value = true
      await pollTask()
      return
    }

    await createTask(requestHash)
  } catch (error) {
    markFailed(error.message || '生成任务初始化失败')
  }
})

onUnload(() => {
  active = false
})

async function createTask(requestHash) {
  const task = await createTripPlanTask(requestData.value)
  taskId.value = task.task_id
  status.value = task.status
  progress.value = task.progress || 0
  message.value = task.message || '任务已创建'
  saveTask(requestHash)
  await pollTask()
}

async function pollTask() {
  let networkFailures = 0
  const requestHash = JSON.stringify(requestData.value)

  while (active && ['queued', 'running'].includes(status.value)) {
    try {
      const response = await getTripPlanTask(taskId.value)
      networkFailures = 0
      recovering.value = false
      status.value = response.status
      progress.value = response.progress || 0
      message.value = response.message || message.value
      saveTask(requestHash)

      if (['succeeded', 'degraded'].includes(response.status) && response.data) {
        await finishTask(response.data, response.status)
        return
      }
      if (response.status === 'failed') {
        markFailed(response.error || response.message || '生成失败')
        return
      }
    } catch (error) {
      networkFailures += 1
      recovering.value = true
      message.value = '网络中断，正在自动恢复任务连接…'
      if (error.status === 404) {
        markFailed('生成任务已过期，请返回首页重新创建行程。')
        return
      }
    }

    const wait = recovering.value ? Math.min(6000, 1500 * networkFailures) : 900
    await new Promise(resolve => setTimeout(resolve, wait))
  }
}

function saveTask(requestHash, extra = {}) {
  const currentSession = getSession()
  uni.setStorageSync('tripGenerationTask', JSON.stringify({
    task_id: taskId.value,
    user_id: currentSession?.user?.id || '',
    city: city.value,
    status: status.value,
    progress: progress.value,
    message: message.value,
    request_hash: requestHash,
    updated_at: new Date().toISOString(),
    ...extra
  }))
}

async function finishTask(plan, taskStatus = 'succeeded') {
  completed.value = true
  status.value = taskStatus
  progress.value = 100
  message.value = taskStatus === 'degraded'
    ? '行程已生成，部分安排需要确认'
    : '行程生成完成'
  uni.setStorageSync('tripPlan', JSON.stringify(plan))
  saveTask(JSON.stringify(requestData.value))

  const id = taskId.value || `trip-${Date.now()}`
  uni.setStorageSync('tripPlanEntryId', id)
  try {
    await saveHistoryEntry(id, plan)
    saveTask(JSON.stringify(requestData.value), { history_saved: true })
  } catch (error) {
    console.warn('账户历史同步失败，将在历史页重新加载:', error)
  }

  uni.removeStorageSync('tripDraft')
  uni.removeStorageSync('tripDraftActive')
  uni.removeStorageSync('pendingTripRequest')

  // 完成后停留在当前页，由用户主动点击“查看行程”。这样完成状态
  // 和保存结果可见，也避免按钮刚启用就被自动跳转打断。
}

function markFailed(reason) {
  failureStep.value = Math.min(currentStep.value, 3)
  failed.value = true
  completed.value = false
  recovering.value = false
  status.value = 'failed'
  errorMessage.value = reason
  message.value = '任务未完成'
  if (requestData.value) saveTask(JSON.stringify(requestData.value))
}

function stepState(index) {
  if (failed.value) return index < failureStep.value ? 'done' : index === failureStep.value ? 'error' : 'pending'
  if (completed.value || index < currentStep.value) return 'done'
  if (index === currentStep.value && !failed.value) return 'current'
  return 'pending'
}

function stepMark(index) {
  const state = stepState(index)
  if (state === 'done') return '✓'
  if (state === 'error') return '!'
  if (state === 'current') return '•'
  return index + 1
}

function stepStatusLabel(index) {
  const state = stepState(index)
  if (state === 'done') return '完成'
  if (state === 'error') return '未通过'
  if (failed.value && state === 'pending') return '未执行'
  if (state === 'current') return '进行中'
  return '等待'
}

function goHome() {
  uni.reLaunch({ url: '/pages/index/index' })
}

function viewResult() {
  if (completed.value) uni.redirectTo({ url: '/pages/result/result' })
}
</script>

<style scoped>
.generating-page {
  min-height: 100vh;
  padding: calc(var(--status-bar-height) + 38rpx) 32rpx calc(36rpx + env(safe-area-inset-bottom));
  background: #f8f7fc;
}

.title,
.subtitle,
.current-message,
.recovery-title,
.recovery-copy,
.task-id,
.failed-title,
.failed-copy,
.estimate {
  display: block;
}

.title {
  color: #252038;
  font-size: 36rpx;
  font-weight: 800;
}

.subtitle {
  margin-top: 10rpx;
  color: #7d768f;
  font-size: 23rpx;
}

.progress-card {
  margin-top: 40rpx;
  padding: 42rpx 30rpx 32rpx;
  text-align: center;
  border: 2rpx solid #e9e6f2;
  border-radius: 40rpx;
  background: #fff;
}

.progress-ring {
  width: 200rpx;
  height: 200rpx;
  margin: 0 auto;
  padding: 18rpx;
  border-radius: 50%;
}

.progress-ring__inner {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: #fff;
}

.progress-number {
  color: #5142be;
  font-size: 42rpx;
  font-weight: 800;
}

.current-message {
  margin-top: 28rpx;
  color: #5f5871;
  font-size: 25rpx;
  font-weight: 600;
}

.steps-card {
  margin-top: 32rpx;
  padding: 26rpx 28rpx;
  border: 2rpx solid #e9e6f2;
  border-radius: 36rpx;
  background: #fff;
}

.step-row {
  min-height: 82rpx;
  display: flex;
  align-items: flex-start;
  gap: 22rpx;
}

.step-dot {
  width: 56rpx;
  height: 56rpx;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  color: #9c95aa;
  background: #f2f0f5;
  font-size: 23rpx;
  font-weight: 700;
}

.step-dot.done {
  color: #39a575;
  background: #edf9f3;
}

.step-dot.current {
  color: #6658e8;
  background: #eeeafe;
}

.step-dot.error {
  color: #b43d38;
  background: #fff0ed;
}

.step-status.error {
  color: #b43d38;
}

.step-body {
  flex: 1;
  padding-top: 9rpx;
}

.step-label {
  color: #9a94a6;
  font-size: 25rpx;
  font-weight: 600;
}

.step-label.current {
  color: #302a45;
  font-weight: 700;
}

.mini-track {
  height: 8rpx;
  margin-top: 18rpx;
  overflow: hidden;
  border-radius: 4rpx;
  background: #e6e2ef;
}

.mini-value {
  width: 58%;
  height: 100%;
  background: #6658e8;
}

.recovery-card {
  margin-top: 32rpx;
  padding: 28rpx;
  border: 2rpx solid #cfe3fb;
  border-radius: 34rpx;
  background: #f0f7ff;
}

.recovery-card.recovering {
  border-color: #f0d0aa;
  background: #fff7ed;
}

.recovery-title {
  color: #2866a3;
  font-size: 27rpx;
  font-weight: 700;
}

.recovering .recovery-title {
  color: #a4621d;
}

.recovery-copy {
  margin-top: 12rpx;
  color: #52708d;
  font-size: 22rpx;
  line-height: 1.55;
}

.task-id {
  margin-top: 14rpx;
  color: #7790a8;
  font-size: 20rpx;
}

.failed-card {
  margin-top: 24rpx;
  padding: 26rpx;
  border-radius: 30rpx;
  background: #fff0ef;
}

.failed-title {
  color: #b54b43;
  font-size: 26rpx;
  font-weight: 700;
}

.failed-copy {
  margin-top: 10rpx;
  color: #7f5a57;
  font-size: 22rpx;
}

.action-row {
  margin-top: 32rpx;
  display: flex;
  gap: 24rpx;
}

.action-row button {
  flex: 1;
}

.action-row .primary-button[disabled] {
  opacity: 0.45;
}

.estimate {
  margin-top: 18rpx;
  color: #9690a4;
  font-size: 21rpx;
  text-align: center;
}

/* 手绘生成进度页 */
.generating-page {
  padding: calc(var(--status-bar-height) + 46rpx) 40rpx calc(34rpx + env(safe-area-inset-bottom));
  background: var(--background);
}

.title {
  color: var(--text);
  font-size: 40rpx;
}

.subtitle {
  color: var(--muted);
  font-size: 24rpx;
}

.progress-card,
.steps-card {
  border-color: var(--border);
  background: var(--surface);
}

.progress-card {
  padding: 34rpx 30rpx 28rpx;
}

.generation-route {
  position: relative;
  width: 360rpx;
  height: 190rpx;
  margin: 0 auto;
}

.route-dash {
  position: absolute;
  left: 24rpx;
  top: 98rpx;
  width: 300rpx;
  height: 85rpx;
  border-top: 7rpx dashed var(--brand);
  border-radius: 55%;
  transform: rotate(-17deg);
}

.route-node {
  position: absolute;
  width: 30rpx;
  height: 30rpx;
  border: 10rpx solid var(--background);
  border-radius: 50%;
  background: var(--accent);
}

.node-one {
  left: 22rpx;
  top: 120rpx;
}

.node-two {
  left: 168rpx;
  top: 70rpx;
  background: #f6be45;
}

.node-three {
  right: 34rpx;
  top: 32rpx;
  background: var(--success);
}

.route-arrow {
  position: absolute;
  right: 0;
  top: -8rpx;
  color: var(--accent);
  font-size: 86rpx;
  font-weight: 500;
  transform: rotate(-35deg);
}

.progress-number {
  display: block;
  color: var(--brand);
  font-size: 58rpx;
}

.current-message {
  margin-top: 6rpx;
  color: var(--muted);
}

.steps-card {
  padding: 30rpx;
}

.step-row {
  min-height: 82rpx;
  align-items: center;
}

.step-dot {
  width: 52rpx;
  height: 52rpx;
  color: var(--muted);
  background: #ebe9e1;
}

.step-dot.done {
  color: #fff;
  background: var(--success);
}

.step-dot.current {
  color: #fff;
  background: var(--accent);
}

.step-body {
  padding-top: 0;
}

.step-label {
  color: var(--muted);
}

.step-label.current {
  color: var(--text);
}

.step-status {
  color: var(--muted);
  font-size: 22rpx;
}

.step-status.done {
  color: var(--success);
}

.step-status.current {
  color: var(--accent);
}

.recovery-card {
  border: 0;
  background: var(--brand-soft);
}

.recovery-card.recovering {
  background: var(--accent-soft);
}

.recovery-title {
  color: var(--brand);
}

.recovery-copy,
.task-id {
  color: var(--text);
}

.estimate {
  margin: 28rpx 6rpx 0;
  color: var(--muted);
  text-align: left;
}

.action-row {
  margin-top: 32rpx;
}

.action-row .primary-button {
  background: var(--brand);
}
</style>
