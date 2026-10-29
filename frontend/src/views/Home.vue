<template>
  <div class="home-container">
    <!-- 背景装饰 -->
    <div class="bg-decoration">
      <div class="circle circle-1"></div>
      <div class="circle circle-2"></div>
      <div class="circle circle-3"></div>
    </div>

    <!-- 页面标题 -->
    <div class="page-header">
      <div class="icon-wrapper">
        <span class="icon">✈️</span>
      </div>
      <h1 class="page-title">智能旅行助手</h1>
      <p class="page-subtitle">基于AI的个性化旅行规划,让每一次出行都完美无忧</p>
    </div>

    <a-card class="form-card" :bordered="false">
      <a-form
        :model="formData"
        layout="vertical"
        @finish="handleSubmit"
      >
        <!-- 第一步:目的地和日期 -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon">📍</span>
            <span class="section-title">目的地与日期</span>
          </div>

          <a-form-item name="trip_mode">
            <template #label>
              <span class="form-label">行程类型</span>
            </template>
            <a-radio-group v-model:value="formData.trip_mode" button-style="solid" size="large">
              <a-radio-button value="single_city">🏙️ 单城市</a-radio-button>
              <a-radio-button value="multi_city">🚄 多城市</a-radio-button>
              <a-radio-button value="road_trip_loop">🚗 自驾环线</a-radio-button>
            </a-radio-group>
          </a-form-item>

          <a-row :gutter="24">
            <a-col :span="8">
              <a-form-item name="city" :rules="[{ required: true, message: '请输入目的地城市' }]">
                <template #label>
                  <span class="form-label">
                    {{ formData.trip_mode === 'single_city' ? '目的地城市' : '行程名称 / 区域' }}
                  </span>
                </template>
                <a-input
                  v-model:value="formData.city"
                  :placeholder="formData.trip_mode === 'single_city' ? '例如：北京' : '例如：青甘大环线'"
                  size="large"
                  class="custom-input"
                >
                  <template #prefix>
                    <span style="color: #1890ff;">🏙️</span>
                  </template>
                </a-input>
              </a-form-item>
            </a-col>
            <a-col :span="6">
              <a-form-item name="start_date" :rules="[{ required: true, message: '请选择开始日期' }]">
                <template #label>
                  <span class="form-label">开始日期</span>
                </template>
                <a-date-picker
                  v-model:value="formData.start_date"
                  style="width: 100%"
                  size="large"
                  class="custom-input"
                  placeholder="选择日期"
                />
              </a-form-item>
            </a-col>
            <a-col :span="6">
              <a-form-item name="end_date" :rules="[{ required: true, message: '请选择结束日期' }]">
                <template #label>
                  <span class="form-label">结束日期</span>
                </template>
                <a-date-picker
                  v-model:value="formData.end_date"
                  style="width: 100%"
                  size="large"
                  class="custom-input"
                  placeholder="选择日期"
                />
              </a-form-item>
            </a-col>
            <a-col :span="4">
              <a-form-item>
                <template #label>
                  <span class="form-label">旅行天数</span>
                </template>
                <div class="days-display-compact">
                  <span class="days-value">{{ formData.travel_days }}</span>
                  <span class="days-unit">天</span>
                </div>
              </a-form-item>
            </a-col>
          </a-row>

          <div v-if="formData.trip_mode !== 'single_city'" class="route-config">
            <a-alert
              :message="formData.trip_mode === 'road_trip_loop'
                ? '可以让 AI 推荐完整环线，也可以自行填写路线节点。'
                : '按旅行顺序填写城市，系统会安排跨城交通和每晚住宿地。'"
              type="info"
              show-icon
              class="route-tip"
            />

            <a-form-item v-if="formData.trip_mode === 'road_trip_loop'">
              <template #label>
                <span class="form-label">路线生成方式</span>
              </template>
              <a-radio-group v-model:value="routeInputMode" button-style="solid">
                <a-radio-button value="ai">✨ AI 帮我规划</a-radio-button>
                <a-radio-button value="manual">✍️ 我自己指定</a-radio-button>
              </a-radio-group>
            </a-form-item>

            <template v-if="formData.trip_mode === 'road_trip_loop' && routeInputMode === 'ai'">
              <a-row :gutter="24">
                <a-col :span="12">
                  <a-form-item>
                    <template #label>
                      <span class="form-label">期望起点（可选）</span>
                    </template>
                    <a-input
                      v-model:value="routeStartLocation"
                      size="large"
                      placeholder="不填则由系统选择常用门户城市"
                    />
                  </a-form-item>
                </a-col>
                <a-col :span="12">
                  <a-form-item name="max_drive_hours_per_day">
                    <template #label>
                      <span class="form-label">每日最多驾驶</span>
                    </template>
                    <a-input-number
                      v-model:value="formData.max_drive_hours_per_day"
                      :min="1"
                      :max="12"
                      :step="0.5"
                      size="large"
                      style="width: 100%"
                      addon-after="小时"
                    />
                  </a-form-item>
                </a-col>
              </a-row>
              <div class="route-ai-action">
                <a-button
                  type="primary"
                  size="large"
                  :loading="routeRecommending"
                  @click="generateRouteOptions"
                >
                  🧭 生成候选环线
                </a-button>
                <span>首批支持：西藏、新疆、青甘、青海、甘肃、川西</span>
              </div>

              <div v-if="routeOptions.length" class="route-options">
                <div
                  v-for="option in routeOptions"
                  :key="option.id"
                  class="route-option-card"
                  :class="{ selected: selectedRouteId === option.id }"
                >
                  <div class="route-option-header">
                    <div>
                      <div class="route-option-title">{{ option.name }}</div>
                      <div class="route-option-summary">{{ option.summary }}</div>
                    </div>
                    <div class="fit-score">{{ option.fit_score.toFixed(0) }}<small>匹配</small></div>
                  </div>
                  <div class="route-option-nodes">{{ option.nodes.join(' → ') }}</div>
                  <div class="route-option-stats">
                    <span>🛣️ 约 {{ option.total_distance_km.toFixed(0) }} km</span>
                    <span>⏱️ 总驾驶 {{ formatRouteDuration(option.total_drive_minutes) }}</span>
                    <span>🚗 最长单段 {{ formatRouteDuration(option.max_segment_minutes) }}</span>
                  </div>
                  <div class="route-option-tags">
                    <a-tag v-for="tag in option.tags" :key="tag" color="blue">{{ tag }}</a-tag>
                  </div>
                  <a-alert
                    v-if="option.warnings.length"
                    :message="option.warnings.slice(0, 2).join('；')"
                    type="warning"
                    show-icon
                    class="route-option-warning"
                  />
                  <a-button
                    :type="selectedRouteId === option.id ? 'primary' : 'default'"
                    block
                    @click="selectRouteOption(option)"
                  >
                    {{ selectedRouteId === option.id ? '✓ 已采用这条路线' : '采用这条路线' }}
                  </a-button>
                </div>
              </div>

              <div v-if="selectedBaseRoute" class="route-editor">
                <div class="route-editor-header">
                  <div>
                    <div class="route-editor-title">调整路线节点</div>
                    <div class="route-editor-subtitle">
                      节点代表当天行程结束后的住宿区域，最后会自动返回第一个节点
                    </div>
                  </div>
                  <a-tag :color="routeDraftDirty ? 'orange' : 'green'">
                    {{ routeDraftDirty ? '待重新校验' : '路线已校验' }}
                  </a-tag>
                </div>

                <div class="route-node-list">
                  <div
                    v-for="(node, index) in routeDraftNodes"
                    :key="`${index}-${node}`"
                    class="route-node-row"
                  >
                    <div class="route-node-index">{{ index + 1 }}</div>
                    <a-input
                      v-model:value="routeDraftNodes[index]"
                      size="large"
                      @input="markRouteDraftDirty"
                    >
                      <template #addonBefore>
                        {{ index === 0 ? '起点' : '住宿点' }}
                      </template>
                    </a-input>
                    <div class="route-node-actions">
                      <a-button
                        size="small"
                        :disabled="index === 0"
                        :aria-label="`上移${node}`"
                        @click="moveRouteNode(index, -1)"
                      >↑</a-button>
                      <a-button
                        size="small"
                        :disabled="index === routeDraftNodes.length - 1"
                        :aria-label="`下移${node}`"
                        @click="moveRouteNode(index, 1)"
                      >↓</a-button>
                      <a-button
                        size="small"
                        danger
                        :disabled="routeDraftNodes.length <= 2"
                        :aria-label="`删除${node}`"
                        @click="removeRouteNode(index)"
                      >删除</a-button>
                    </div>
                  </div>
                  <div class="route-return-row">
                    <div class="route-node-index">↩</div>
                    <div>返回起点：{{ routeDraftNodes[0] || '待填写' }}</div>
                  </div>
                </div>

                <div class="route-add-node">
                  <a-input
                    v-model:value="newRouteNode"
                    size="large"
                    placeholder="输入要增加的城市、景区或住宿区域"
                    @press-enter="addRouteNode"
                  />
                  <a-button size="large" @click="addRouteNode">＋ 添加节点</a-button>
                </div>

                <a-alert
                  v-if="routeDraftDirty"
                  message="路线已被修改，生成详细行程前需要重新计算距离和驾驶时间。"
                  type="warning"
                  show-icon
                  class="route-editor-alert"
                />
                <a-alert
                  v-else-if="routeDraftValidation?.warnings.length"
                  :message="routeDraftValidation.warnings.join('；')"
                  type="warning"
                  show-icon
                  class="route-editor-alert"
                />
                <div v-if="routeDraftValidation && !routeDraftDirty" class="route-editor-stats">
                  <span>🛣️ {{ routeDraftValidation.total_distance_km.toFixed(0) }} km</span>
                  <span>⏱️ 总驾驶 {{ formatRouteDuration(routeDraftValidation.total_drive_minutes) }}</span>
                  <span>🚗 最长单段 {{ formatRouteDuration(routeDraftValidation.max_segment_minutes) }}</span>
                </div>

                <div class="route-editor-actions">
                  <a-button @click="restoreRecommendedRoute">恢复AI推荐</a-button>
                  <a-button
                    type="primary"
                    :loading="routeValidating"
                    @click="validateEditedRoute"
                  >
                    重新校验路线
                  </a-button>
                </div>
              </div>
            </template>

            <a-row v-else :gutter="24">
              <a-col :span="24">
                <a-form-item name="destinations">
                  <template #label>
                    <span class="form-label">路线节点（按顺序）</span>
                  </template>
                  <a-select
                    v-model:value="formData.destinations"
                    mode="tags"
                    size="large"
                    class="custom-select"
                    placeholder="逐个输入并回车，例如：西宁、青海湖、敦煌"
                    :token-separators="[',', '，']"
                    :max-tag-count="8"
                  />
                </a-form-item>
              </a-col>
              <a-col :span="12">
                <a-form-item name="intercity_transport">
                  <template #label>
                    <span class="form-label">跨城交通</span>
                  </template>
                  <a-select
                    v-model:value="formData.intercity_transport"
                    size="large"
                    class="custom-select"
                    :disabled="formData.trip_mode === 'road_trip_loop'"
                  >
                    <a-select-option value="auto">✨ 智能建议</a-select-option>
                    <a-select-option value="high_speed_rail">🚄 优先高铁 / 动车</a-select-option>
                    <a-select-option value="train">🚆 优先普通火车 / 城际</a-select-option>
                    <a-select-option value="flight">✈️ 优先飞机</a-select-option>
                    <a-select-option value="self_drive">🚗 自驾</a-select-option>
                  </a-select>
                </a-form-item>
              </a-col>
              <a-col
                v-if="formData.trip_mode === 'road_trip_loop' || formData.intercity_transport === 'self_drive'"
                :span="12"
              >
                <a-form-item name="max_drive_hours_per_day">
                  <template #label>
                    <span class="form-label">每日最多驾驶</span>
                  </template>
                  <a-input-number
                    v-model:value="formData.max_drive_hours_per_day"
                    :min="1"
                    :max="12"
                    :step="0.5"
                    size="large"
                    style="width: 100%"
                    addon-after="小时"
                  />
                </a-form-item>
              </a-col>
              <a-col v-else :span="12">
                <a-form-item>
                  <template #label>
                    <span class="form-label">班次说明</span>
                  </template>
                  <div class="schedule-note">生成建议交通和时间缓冲；具体车次、航班需出发前确认</div>
                </a-form-item>
              </a-col>
            </a-row>
          </div>
        </div>

        <!-- 第二步:偏好设置 -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon">⚙️</span>
            <span class="section-title">偏好设置</span>
          </div>

          <a-row :gutter="24">
            <a-col :span="8">
              <a-form-item name="transportation">
                <template #label>
                  <span class="form-label">交通方式</span>
                </template>
                <a-select v-model:value="formData.transportation" size="large" class="custom-select">
                  <a-select-option value="公共交通">🚇 公共交通</a-select-option>
                  <a-select-option value="自驾">🚗 自驾</a-select-option>
                  <a-select-option value="步行">🚶 步行</a-select-option>
                  <a-select-option value="混合">🔀 混合</a-select-option>
                </a-select>
              </a-form-item>
            </a-col>
            <a-col :span="8">
              <a-form-item name="accommodation">
                <template #label>
                  <span class="form-label">住宿偏好</span>
                </template>
                <a-select v-model:value="formData.accommodation" size="large" class="custom-select">
                  <a-select-option value="经济型酒店">💰 经济型酒店</a-select-option>
                  <a-select-option value="舒适型酒店">🏨 舒适型酒店</a-select-option>
                  <a-select-option value="豪华酒店">⭐ 豪华酒店</a-select-option>
                  <a-select-option value="民宿">🏡 民宿</a-select-option>
                </a-select>
              </a-form-item>
            </a-col>
            <a-col :span="8">
              <a-form-item name="preferences">
                <template #label>
                  <span class="form-label">旅行偏好</span>
                </template>
                <div class="preference-tags">
                  <a-checkbox-group v-model:value="formData.preferences" class="custom-checkbox-group">
                    <a-checkbox value="历史文化" class="preference-tag">🏛️ 历史文化</a-checkbox>
                    <a-checkbox value="自然风光" class="preference-tag">🏞️ 自然风光</a-checkbox>
                    <a-checkbox value="美食" class="preference-tag">🍜 美食</a-checkbox>
                    <a-checkbox value="购物" class="preference-tag">🛍️ 购物</a-checkbox>
                    <a-checkbox value="艺术" class="preference-tag">🎨 艺术</a-checkbox>
                    <a-checkbox value="休闲" class="preference-tag">☕ 休闲</a-checkbox>
                  </a-checkbox-group>
                </div>
              </a-form-item>
            </a-col>
          </a-row>
        </div>

        <!-- 第三步:额外要求 -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon">💬</span>
            <span class="section-title">额外要求</span>
          </div>

          <a-form-item name="free_text_input">
            <a-textarea
              v-model:value="formData.free_text_input"
              placeholder="请输入您的额外要求,例如:想去看升旗、需要无障碍设施、对海鲜过敏等..."
              :rows="3"
              size="large"
              class="custom-textarea"
            />
          </a-form-item>
        </div>

        <!-- 抵达与离开 -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon">🕐</span>
            <span class="section-title">抵达与离开</span>
          </div>

          <a-row :gutter="24">
            <!-- 抵达方式 -->
            <a-col :span="8">
              <a-form-item name="arrival_transport">
                <template #label>
                  <span class="form-label">抵达方式</span>
                </template>
                <a-select
                  v-model:value="formData.arrival_transport"
                  size="large"
                  class="custom-select"
                  placeholder="选择抵达方式"
                  :allowClear="true"
                >
                  <a-select-option value="飞机">✈️ 飞机</a-select-option>
                  <a-select-option value="火车">🚄 火车</a-select-option>
                  <a-select-option value="自驾">🚗 自驾</a-select-option>
                  <a-select-option value="已在此地">🏠 已在此地</a-select-option>
                </a-select>
              </a-form-item>
            </a-col>

            <!-- 抵达时间 -->
            <a-col :span="8">
              <a-form-item name="arrival_time">
                <template #label>
                  <span class="form-label">抵达时间</span>
                </template>
                <a-time-picker
                  v-model:value="formData.arrival_time"
                  style="width: 100%"
                  size="large"
                  class="custom-input"
                  format="HH:mm"
                  placeholder="选择抵达时间"
                  :allowClear="true"
                  :disabled="!formData.arrival_transport || formData.arrival_transport === '已在此地'"
                />
              </a-form-item>
            </a-col>

            <!-- 离开时间 -->
            <a-col :span="8">
              <a-form-item name="departure_time">
                <template #label>
                  <span class="form-label">离开时间</span>
                </template>
                <a-time-picker
                  v-model:value="formData.departure_time"
                  style="width: 100%"
                  size="large"
                  class="custom-input"
                  format="HH:mm"
                  placeholder="选择离开时间"
                  :allowClear="true"
                />
              </a-form-item>
            </a-col>
          </a-row>
        </div>

        <!-- 提交按钮 -->
        <a-form-item>
          <a-button
            type="primary"
            html-type="submit"
            :loading="loading"
            size="large"
            block
            class="submit-button"
          >
            <template v-if="!loading">
              <span class="button-icon">🚀</span>
              <span>开始规划我的旅行</span>
            </template>
            <template v-else>
              <span>正在生成中...</span>
            </template>
          </a-button>
        </a-form-item>

        <!-- 加载进度条 -->
        <a-form-item v-if="loading">
          <div class="loading-container">
            <a-progress
              :percent="loadingProgress"
              status="active"
              :stroke-color="{
                '0%': '#667eea',
                '100%': '#764ba2',
              }"
              :stroke-width="10"
            />
            <p class="loading-status">
              {{ loadingStatus }}
            </p>
          </div>
        </a-form-item>
      </a-form>
    </a-card>

    <a-card v-if="tripHistory.length" class="history-card" title="🕘 我的历史行程" :bordered="false">
      <a-list :data-source="tripHistory" item-layout="horizontal">
        <template #renderItem="{ item }">
          <a-list-item>
            <a-list-item-meta
              :title="`${item.plan.city} · ${item.plan.start_date} 至 ${item.plan.end_date}`"
              :description="`更新于 ${formatHistoryTime(item.updatedAt)} · ${item.plan.days.length} 天`"
            />
            <template #actions>
              <a-button type="link" @click="openHistory(item)">查看</a-button>
              <a-button type="link" danger @click="deleteHistory(item.id)">删除</a-button>
            </template>
          </a-list-item>
        </template>
      </a-list>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { createTripPlanTask, fetchAccountHistory, getTripPlanTask, recommendRouteOptions, removeAccountHistory, validateRouteDraft } from '@/services/api'
import { getSession } from '@/services/auth'
import type { RouteOption, TripFormData, TripTaskStatusResponse } from '@/types'
import { getTripHistory, removeTripPlanHistory, saveTripPlanHistory, type TripHistoryEntry } from '@/services/tripHistory'
import {
  clearPendingTripSubmission,
  getPendingTripSubmission,
  savePendingTripSubmission
} from '@/services/pendingTripSubmission'
import {
  clearPendingTripPlanTask,
  getPendingTripPlanTask,
  savePendingTripPlanTask
} from '@/services/tripTaskRecovery'
import dayjs, { type Dayjs } from 'dayjs'

const router = useRouter()
const loading = ref(false)
const loadingProgress = ref(0)
const loadingStatus = ref('')
const tripHistory = ref<TripHistoryEntry[]>([])
const routeInputMode = ref<'ai' | 'manual'>('ai')
const routeStartLocation = ref('')
const routeOptions = ref<RouteOption[]>([])
const routeRecommending = ref(false)
const selectedRouteId = ref('')
const selectedBaseRoute = ref<RouteOption | null>(null)
const routeDraftNodes = ref<string[]>([])
const routeDraftValidation = ref<RouteOption | null>(null)
const routeDraftDirty = ref(false)
const routeValidating = ref(false)
const newRouteNode = ref('')
const activeTripTaskId = ref<string | null>(null)
let homePageActive = true

const refreshHistory = async () => {
  if (!getSession()) {
    tripHistory.value = []
    return
  }

  try {
    const remote = await fetchAccountHistory()
    tripHistory.value = remote
  } catch {
    tripHistory.value = getSession() ? getTripHistory() : []
  }
}

const formatHistoryTime = (value: string) => new Date(value).toLocaleString('zh-CN', {
  month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
})

const openHistory = (item: TripHistoryEntry) => {
  sessionStorage.setItem('tripPlan', JSON.stringify(item.plan))
  sessionStorage.setItem('tripPlanHistoryId', item.id)
  router.push('/result')
}

const deleteHistory = async (id: string) => {
  removeTripPlanHistory(id)
  try { await removeAccountHistory(id) } catch { /* local deletion remains available */ }
  await refreshHistory()
  message.success('历史行程已删除')
}

const formData = reactive<Omit<TripFormData, 'start_date' | 'end_date' | 'arrival_time' | 'departure_time'> & {
  start_date: Dayjs | null
  end_date: Dayjs | null
  arrival_time: Dayjs | undefined
  departure_time: Dayjs | undefined
}>({
  city: '',
  start_date: null,
  end_date: null,
  travel_days: 1,
  transportation: '公共交通',
  accommodation: '经济型酒店',
  preferences: [],
  free_text_input: '',
  arrival_transport: undefined as string | undefined,
  arrival_time: undefined as Dayjs | undefined,
  departure_time: undefined as Dayjs | undefined,
  trip_mode: 'single_city',
  destinations: [],
  start_location: undefined,
  end_location: undefined,
  return_to_start: false,
  intercity_transport: 'auto',
  max_drive_hours_per_day: 6
})

const wait = (milliseconds: number) => new Promise(resolve => setTimeout(resolve, milliseconds))

const monitorTripTask = async (
  taskId: string,
  initialStatus?: TripTaskStatusResponse
): Promise<TripTaskStatusResponse | null> => {
  let taskStatus = initialStatus
  let consecutiveConnectionErrors = 0

  while (homePageActive) {
    if (!taskStatus) {
      try {
        taskStatus = await getTripPlanTask(taskId)
        consecutiveConnectionErrors = 0
      } catch (error: any) {
        if (error.status === 404) {
          clearPendingTripPlanTask(taskId)
          throw new Error('上次生成任务不存在或已过期，请重新生成旅行计划')
        }

        consecutiveConnectionErrors += 1
        loadingStatus.value = '连接暂时中断，正在自动恢复生成任务...'
        await wait(Math.min(5000, 1000 * consecutiveConnectionErrors))
        continue
      }
    }

    loadingProgress.value = taskStatus.progress
    loadingStatus.value = taskStatus.message

    if (taskStatus.status !== 'queued' && taskStatus.status !== 'running') {
      return taskStatus
    }

    taskStatus = undefined
    await wait(800)
  }

  return null
}

const finishTripTask = async (taskStatus: TripTaskStatusResponse) => {
  if (!getSession()) {
    await router.push({
      name: 'Auth',
      query: { redirect: '/', continue: 'trip' }
    })
    return
  }

  clearPendingTripPlanTask(taskStatus.task_id)

  if ((taskStatus.status === 'succeeded' || taskStatus.status === 'degraded') && taskStatus.data) {
    const historyEntry = saveTripPlanHistory(taskStatus.data)
    sessionStorage.setItem('tripPlan', JSON.stringify(taskStatus.data))
    sessionStorage.setItem('tripPlanHistoryId', historyEntry.id)
    void refreshHistory()

    if (taskStatus.status === 'degraded') {
      message.warning('行程已部分生成，请查看页面中的待确认提醒')
    } else {
      message.success('旅行计划生成成功!')
    }

    await wait(500)
    if (homePageActive) await router.push('/result')
    return
  }

  throw new Error(taskStatus.error || taskStatus.message || '生成失败')
}

const trackTripTask = async (
  taskId: string,
  initialStatus?: TripTaskStatusResponse,
  isRecovery = false
) => {
  if (activeTripTaskId.value) return

  activeTripTaskId.value = taskId
  loading.value = true
  loadingProgress.value = initialStatus?.progress || 0
  loadingStatus.value = isRecovery ? '正在恢复上次未完成的旅行计划...' : '正在初始化...'

  try {
    const taskStatus = await monitorTripTask(taskId, initialStatus)
    if (taskStatus) await finishTripTask(taskStatus)
  } catch (error: any) {
    message.error(error.message || '生成旅行计划失败，请稍后刷新页面查看任务状态')
  } finally {
    if (activeTripTaskId.value === taskId) {
      activeTripTaskId.value = null
      loading.value = false
      loadingProgress.value = 0
      loadingStatus.value = ''
    }
  }
}

const resumePendingTripTask = (): boolean => {
  const pendingTask = getPendingTripPlanTask()
  if (!pendingTask) return false

  if (!getSession()) {
    void router.replace({
      name: 'Auth',
      query: { redirect: '/', continue: 'trip' }
    })
    return true
  }

  message.info('检测到未完成的旅行计划，正在继续生成')
  void trackTripTask(pendingTask.taskId, undefined, true)
  return true
}

const restoreTripForm = async (request: TripFormData) => {
  formData.trip_mode = request.trip_mode
  await nextTick()

  formData.city = request.city
  formData.start_date = dayjs(request.start_date)
  formData.end_date = dayjs(request.end_date)
  formData.travel_days = request.travel_days
  formData.transportation = request.transportation
  formData.accommodation = request.accommodation
  formData.preferences = [...request.preferences]
  formData.free_text_input = request.free_text_input
  formData.arrival_transport = request.arrival_transport
  formData.arrival_time = request.arrival_time
    ? dayjs(`2000-01-01T${request.arrival_time}:00`)
    : undefined
  formData.departure_time = request.departure_time
    ? dayjs(`2000-01-01T${request.departure_time}:00`)
    : undefined
  formData.destinations = [...request.destinations]
  formData.start_location = request.start_location
  formData.end_location = request.end_location
  formData.return_to_start = request.return_to_start
  formData.intercity_transport = request.intercity_transport
  formData.max_drive_hours_per_day = request.max_drive_hours_per_day
  if (request.trip_mode === 'road_trip_loop') routeInputMode.value = 'manual'
}

const startTripGeneration = async (requestData: TripFormData) => {
  loading.value = true
  loadingProgress.value = 0
  loadingStatus.value = '正在创建旅行规划任务...'

  try {
    const task = await createTripPlanTask(requestData)
    clearPendingTripSubmission()
    savePendingTripPlanTask(task.task_id)
    await trackTripTask(task.task_id, task)
  } catch (error: any) {
    if (error.status === 401) {
      savePendingTripSubmission(requestData)
      message.warning('登录已失效，请重新登录后继续生成')
      await router.push({
        name: 'Auth',
        query: { redirect: '/', continue: 'trip' }
      })
      return
    }

    message.error(error.message || '创建旅行规划任务失败，请稍后重试')
    loading.value = false
    loadingProgress.value = 0
    loadingStatus.value = ''
  }
}

const continuePendingTripSubmission = async (): Promise<boolean> => {
  if (!getSession()) return false

  const pendingSubmission = getPendingTripSubmission()
  if (!pendingSubmission) return false

  await restoreTripForm(pendingSubmission)
  message.info('已恢复刚才填写的旅行信息，正在继续生成')
  void startTripGeneration(pendingSubmission)
  return true
}

onMounted(async () => {
  homePageActive = true
  void refreshHistory()
  if (resumePendingTripTask()) return
  await continuePendingTripSubmission()
})

onUnmounted(() => {
  homePageActive = false
})

const formatRouteDuration = (minutes: number) => {
  if (!minutes) return '待确认'
  const hours = Math.floor(minutes / 60)
  const remainder = minutes % 60
  return remainder ? `${hours}小时${remainder}分` : `${hours}小时`
}

const clearRouteOptions = (clearNodes = false) => {
  routeOptions.value = []
  selectedRouteId.value = ''
  selectedBaseRoute.value = null
  routeDraftNodes.value = []
  routeDraftValidation.value = null
  routeDraftDirty.value = false
  newRouteNode.value = ''
  if (clearNodes) {
    formData.destinations = []
    formData.start_location = undefined
    formData.end_location = undefined
  }
}

const generateRouteOptions = async () => {
  if (formData.city.trim().length < 2) {
    message.error('请先填写要去的区域，例如“西藏”或“新疆”')
    return
  }
  if (formData.travel_days < 3) {
    message.error('智能环线至少需要 3 天')
    return
  }
  routeRecommending.value = true
  clearRouteOptions(true)
  try {
    routeOptions.value = await recommendRouteOptions({
      region: formData.city.trim(),
      travel_days: formData.travel_days,
      start_location: routeStartLocation.value.trim() || undefined,
      preferences: formData.preferences,
      max_drive_hours_per_day: formData.max_drive_hours_per_day
    })
    if (!routeOptions.value.length) {
      message.warning('暂时没有找到合适路线，可以切换为手动指定')
    }
  } catch (error: any) {
    message.error(error.message || '候选路线生成失败')
  } finally {
    routeRecommending.value = false
  }
}

const selectRouteOption = (option: RouteOption) => {
  const nodes = [...option.nodes]
  if (nodes.length > 1 && nodes[0] === nodes[nodes.length - 1]) {
    nodes.pop()
  }
  formData.destinations = nodes
  formData.start_location = nodes[0]
  formData.end_location = nodes[0]
  selectedRouteId.value = option.id
  selectedBaseRoute.value = option
  routeDraftNodes.value = [...nodes]
  routeDraftValidation.value = option
  routeDraftDirty.value = false
  message.success(`已采用“${option.name}”，现在可以调整路线节点`)
}

const markRouteDraftDirty = () => {
  routeDraftDirty.value = true
  routeDraftValidation.value = null
  formData.destinations = [...routeDraftNodes.value]
  formData.start_location = routeDraftNodes.value[0]
  formData.end_location = routeDraftNodes.value[0]
}

const moveRouteNode = (index: number, direction: -1 | 1) => {
  const target = index + direction
  if (target < 0 || target >= routeDraftNodes.value.length) return
  const nodes = [...routeDraftNodes.value]
  ;[nodes[index], nodes[target]] = [nodes[target], nodes[index]]
  routeDraftNodes.value = nodes
  markRouteDraftDirty()
}

const removeRouteNode = (index: number) => {
  if (routeDraftNodes.value.length <= 2) {
    message.warning('环线至少需要保留 2 个节点')
    return
  }
  routeDraftNodes.value.splice(index, 1)
  markRouteDraftDirty()
}

const addRouteNode = () => {
  const node = newRouteNode.value.trim()
  if (!node) return
  if (routeDraftNodes.value.length >= 12) {
    message.warning('路线节点最多 12 个')
    return
  }
  routeDraftNodes.value.push(node)
  newRouteNode.value = ''
  markRouteDraftDirty()
}

const validateEditedRoute = async () => {
  const nodes = routeDraftNodes.value.map(node => node.trim()).filter(Boolean)
  if (nodes.length < 2) {
    message.error('环线至少需要 2 个有效节点')
    return
  }
  routeValidating.value = true
  try {
    const result = await validateRouteDraft({
      region: formData.city.trim(),
      nodes,
      travel_days: formData.travel_days,
      max_drive_hours_per_day: formData.max_drive_hours_per_day
    })
    routeDraftValidation.value = result
    routeDraftNodes.value = result.nodes.slice(0, -1)
    formData.destinations = [...routeDraftNodes.value]
    formData.start_location = routeDraftNodes.value[0]
    formData.end_location = routeDraftNodes.value[0]
    routeDraftDirty.value = false
    message.success(
      result.warnings.length
        ? '路线已重新计算，请查看驾驶强度提醒'
        : '路线校验通过，可以生成详细行程'
    )
  } catch (error: any) {
    routeDraftValidation.value = null
    message.error(error.message || '路线重新校验失败')
  } finally {
    routeValidating.value = false
  }
}

const restoreRecommendedRoute = () => {
  if (selectedBaseRoute.value) {
    selectRouteOption(selectedBaseRoute.value)
  }
}

// 监听日期变化,自动计算旅行天数
watch([() => formData.start_date, () => formData.end_date], ([start, end]) => {
  if (start && end) {
    const days = end.diff(start, 'day') + 1
    if (days > 0 && days <= 30) {
      formData.travel_days = days
    } else if (days > 30) {
      message.warning('旅行天数不能超过30天')
      formData.end_date = null
    } else {
      message.warning('结束日期不能早于开始日期')
      formData.end_date = null
    }
  }
})

watch(() => formData.trip_mode, (mode) => {
  if (mode === 'single_city') {
    formData.return_to_start = false
    formData.intercity_transport = 'auto'
    clearRouteOptions(true)
    return
  }
  if (mode === 'road_trip_loop') {
    routeInputMode.value = 'ai'
    formData.return_to_start = true
    formData.intercity_transport = 'self_drive'
    formData.transportation = '自驾'
    clearRouteOptions(true)
  } else {
    routeInputMode.value = 'manual'
    formData.return_to_start = false
    clearRouteOptions(true)
    if (formData.intercity_transport === 'self_drive') {
      formData.intercity_transport = 'auto'
    }
  }
})

watch(routeInputMode, (mode, previous) => {
  clearRouteOptions(mode === 'ai' && previous === 'manual')
})

watch(
  [
    () => formData.city,
    () => formData.travel_days,
    () => formData.max_drive_hours_per_day,
    routeStartLocation
  ],
  () => {
    if (
      formData.trip_mode === 'road_trip_loop'
      && routeInputMode.value === 'ai'
      && routeOptions.value.length
    ) {
      clearRouteOptions(true)
    }
  }
)

const handleSubmit = async () => {
  if (loading.value || activeTripTaskId.value) return

  if (!formData.start_date || !formData.end_date) {
    message.error('请选择日期')
    return
  }
  if (
    formData.trip_mode === 'road_trip_loop'
    && routeInputMode.value === 'ai'
    && routeDraftDirty.value
  ) {
    message.error('路线节点已经修改，请先重新校验路线')
    return
  }
  if (formData.trip_mode !== 'single_city' && formData.destinations.length < 2) {
    message.error(
      formData.trip_mode === 'road_trip_loop' && routeInputMode.value === 'ai'
        ? '请先生成并采用一条候选环线'
        : '跨城行程至少需要填写 2 个路线节点'
    )
    return
  }
  if (
    formData.trip_mode !== 'single_city'
    && formData.travel_days < formData.destinations.length
  ) {
    message.error('旅行天数不能少于路线节点数量')
    return
  }

  const requestData: TripFormData = {
    city: formData.city,
    start_date: formData.start_date.format('YYYY-MM-DD'),
    end_date: formData.end_date.format('YYYY-MM-DD'),
    travel_days: formData.travel_days,
    transportation: formData.transportation,
    accommodation: formData.accommodation,
    preferences: formData.preferences,
    free_text_input: formData.free_text_input,
    arrival_transport: formData.arrival_transport,
    arrival_time: formData.arrival_time ? formData.arrival_time.format('HH:mm') : undefined,
    departure_time: formData.departure_time ? formData.departure_time.format('HH:mm') : undefined,
    trip_mode: formData.trip_mode,
    destinations: formData.destinations,
    start_location: formData.trip_mode === 'single_city' ? undefined : formData.destinations[0],
    end_location: formData.trip_mode === 'single_city'
      ? undefined
      : formData.destinations[formData.destinations.length - 1],
    return_to_start: formData.trip_mode === 'road_trip_loop',
    intercity_transport: formData.intercity_transport,
    max_drive_hours_per_day: formData.max_drive_hours_per_day
  }

  if (!getSession()) {
    savePendingTripSubmission(requestData)
    message.info('登录后将自动继续生成，不会丢失刚才填写的内容')
    await router.push({
      name: 'Auth',
      query: { redirect: '/', continue: 'trip' }
    })
    return
  }

  await startTripGeneration(requestData)
}
</script>

<style scoped>
.home-container {
  min-height: 100vh;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 60px 20px;
  position: relative;
  overflow: hidden;
}

/* 背景装饰 */
.bg-decoration {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  overflow: hidden;
}

.circle {
  position: absolute;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.1);
  animation: float 20s infinite ease-in-out;
}

.circle-1 {
  width: 300px;
  height: 300px;
  top: -100px;
  left: -100px;
  animation-delay: 0s;
}

.circle-2 {
  width: 200px;
  height: 200px;
  top: 50%;
  right: -50px;
  animation-delay: 5s;
}

.circle-3 {
  width: 150px;
  height: 150px;
  bottom: -50px;
  left: 30%;
  animation-delay: 10s;
}

@keyframes float {
  0%, 100% {
    transform: translateY(0) rotate(0deg);
  }
  50% {
    transform: translateY(-30px) rotate(180deg);
  }
}

/* 页面标题 */
.page-header {
  text-align: center;
  margin-bottom: 50px;
  animation: fadeInDown 0.8s ease-out;
  position: relative;
  z-index: 1;
}

.icon-wrapper {
  margin-bottom: 20px;
}

.icon {
  font-size: 80px;
  display: inline-block;
  animation: bounce 2s infinite;
}

@keyframes bounce {
  0%, 100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-20px);
  }
}

.page-title {
  font-size: 56px;
  font-weight: 800;
  color: #ffffff;
  margin-bottom: 16px;
  text-shadow: 3px 3px 6px rgba(0, 0, 0, 0.3);
  letter-spacing: 2px;
}

.page-subtitle {
  font-size: 20px;
  color: rgba(255, 255, 255, 0.95);
  margin: 0;
  font-weight: 300;
}

/* 表单卡片 */
.form-card {
  max-width: 1400px;
  margin: 0 auto;
  border-radius: 24px;
  box-shadow: 0 30px 80px rgba(0, 0, 0, 0.4);
  animation: fadeInUp 0.8s ease-out;
  position: relative;
  z-index: 1;
  backdrop-filter: blur(10px);
  background: rgba(255, 255, 255, 0.98) !important;
}

.history-card {
  margin-top: 24px;
  border-radius: 16px;
  box-shadow: 0 12px 30px rgba(31, 45, 61, 0.1);
}

/* 表单分区 */
.form-section {
  margin-bottom: 32px;
  padding: 24px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 16px;
  border: 1px solid #e8e8e8;
  transition: all 0.3s ease;
}

.form-section:hover {
  box-shadow: 0 8px 24px rgba(102, 126, 234, 0.15);
  transform: translateY(-2px);
}

.section-header {
  display: flex;
  align-items: center;
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 2px solid #667eea;
}

.section-icon {
  font-size: 24px;
  margin-right: 12px;
}

.section-title {
  font-size: 18px;
  font-weight: 600;
  color: #333;
}

/* 表单标签 */
.form-label {
  font-size: 15px;
  font-weight: 500;
  color: #555;
}

/* 自定义输入框 */
.custom-input :deep(.ant-input),
.custom-input :deep(.ant-picker) {
  border-radius: 12px;
  border: 2px solid #e8e8e8;
  transition: all 0.3s ease;
}

.custom-input :deep(.ant-input:hover),
.custom-input :deep(.ant-picker:hover) {
  border-color: #667eea;
}

.custom-input :deep(.ant-input:focus),
.custom-input :deep(.ant-picker-focused) {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

/* 自定义选择框 */
.custom-select :deep(.ant-select-selector) {
  border-radius: 12px !important;
  border: 2px solid #e8e8e8 !important;
  transition: all 0.3s ease;
}

.custom-select:hover :deep(.ant-select-selector) {
  border-color: #667eea !important;
}

.custom-select :deep(.ant-select-focused .ant-select-selector) {
  border-color: #667eea !important;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1) !important;
}

/* 天数显示 - 紧凑版 */
.days-display-compact {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 40px;
  padding: 8px 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 12px;
  color: white;
}

.days-display-compact .days-value {
  font-size: 24px;
  font-weight: 700;
  margin-right: 4px;
}

.days-display-compact .days-unit {
  font-size: 14px;
}

/* 偏好标签 */
.preference-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.custom-checkbox-group {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  width: 100%;
}

.preference-tag :deep(.ant-checkbox-wrapper) {
  margin: 0 !important;
  padding: 8px 16px;
  border: 2px solid #e8e8e8;
  border-radius: 20px;
  transition: all 0.3s ease;
  background: white;
  font-size: 14px;
}

.preference-tag :deep(.ant-checkbox-wrapper:hover) {
  border-color: #667eea;
  background: #f5f7ff;
}

.preference-tag :deep(.ant-checkbox-wrapper-checked) {
  border-color: #667eea;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

/* 自定义文本域 */
.custom-textarea :deep(.ant-input) {
  border-radius: 12px;
  border: 2px solid #e8e8e8;
  transition: all 0.3s ease;
}

.custom-textarea :deep(.ant-input:hover) {
  border-color: #667eea;
}

.custom-textarea :deep(.ant-input:focus) {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

.route-config {
  margin-top: 8px;
  padding: 20px;
  border-radius: 14px;
  background: #f6f8ff;
  border: 1px solid #dfe4ff;
}

.route-tip {
  margin-bottom: 18px;
}

.route-ai-action {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 20px;
  color: #667085;
  font-size: 13px;
}

.route-options {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.route-option-card {
  min-width: 0;
  padding: 18px;
  border: 2px solid #e2e6f3;
  border-radius: 14px;
  background: #fff;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
}

.route-option-card:hover {
  transform: translateY(-2px);
  border-color: #aab4ee;
  box-shadow: 0 8px 24px rgba(76, 88, 170, 0.12);
}

.route-option-card.selected {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.12);
}

.route-option-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.route-option-title {
  color: #27315f;
  font-size: 17px;
  font-weight: 700;
}

.route-option-summary {
  margin-top: 6px;
  min-height: 42px;
  color: #667085;
  font-size: 13px;
  line-height: 1.55;
}

.fit-score {
  flex: none;
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 48px;
  padding: 7px 8px;
  border-radius: 10px;
  background: #eef1ff;
  color: #5668d8;
  font-size: 19px;
  font-weight: 800;
}

.fit-score small {
  font-size: 10px;
  font-weight: 500;
}

.route-option-nodes {
  margin: 14px 0;
  padding: 10px;
  border-radius: 8px;
  background: #f7f8fc;
  color: #39405a;
  font-size: 13px;
  line-height: 1.7;
}

.route-option-stats {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-bottom: 12px;
  color: #5f6678;
  font-size: 12px;
}

.route-option-tags {
  min-height: 30px;
  margin-bottom: 10px;
}

.route-option-warning {
  margin-bottom: 12px;
}

.route-option-warning :deep(.ant-alert-message) {
  font-size: 12px;
  line-height: 1.45;
}

.route-editor {
  margin-top: 22px;
  padding: 20px;
  border: 1px solid #ccd4ff;
  border-radius: 14px;
  background: #fff;
}

.route-editor-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.route-editor-title {
  color: #27315f;
  font-size: 18px;
  font-weight: 700;
}

.route-editor-subtitle {
  margin-top: 4px;
  color: #7a8192;
  font-size: 13px;
}

.route-node-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.route-node-row {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
}

.route-node-index {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: #667eea;
  color: #fff;
  font-size: 13px;
  font-weight: 700;
}

.route-node-actions {
  display: flex;
  gap: 6px;
}

.route-return-row {
  display: grid;
  grid-template-columns: 34px 1fr;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
  color: #5361b5;
  font-weight: 600;
}

.route-return-row .route-node-index {
  background: #eef1ff;
  color: #667eea;
}

.route-add-node {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  margin-top: 14px;
}

.route-editor-alert {
  margin-top: 16px;
}

.route-editor-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  margin-top: 16px;
  padding: 12px 14px;
  border-radius: 8px;
  background: #f6f8ff;
  color: #4f5770;
  font-size: 13px;
}

.route-editor-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}

.schedule-note {
  min-height: 40px;
  display: flex;
  align-items: center;
  color: #667085;
  line-height: 1.5;
}

@media (max-width: 900px) {
  .route-options {
    grid-template-columns: 1fr;
  }

  .route-ai-action {
    align-items: flex-start;
    flex-direction: column;
  }

  .route-node-row {
    grid-template-columns: 30px minmax(0, 1fr);
  }

  .route-node-actions {
    grid-column: 2;
  }

  .route-add-node {
    grid-template-columns: 1fr;
  }
}

/* 提交按钮 */
.submit-button {
  height: 56px;
  border-radius: 28px;
  font-size: 18px;
  font-weight: 600;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
  box-shadow: 0 8px 24px rgba(102, 126, 234, 0.4);
  transition: all 0.3s ease;
}

.submit-button:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 32px rgba(102, 126, 234, 0.5);
}

.submit-button:active {
  transform: translateY(0);
}

.button-icon {
  margin-right: 8px;
  font-size: 20px;
}

/* 加载容器 */
.loading-container {
  text-align: center;
  padding: 24px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 16px;
  border: 2px dashed #667eea;
}

.loading-status {
  margin-top: 16px;
  color: #667eea;
  font-size: 18px;
  font-weight: 500;
}

/* 动画 */
@keyframes fadeInDown {
  from {
    opacity: 0;
    transform: translateY(-30px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(30px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>

