<template>
  <div class="result-container">
    <!-- 页面头部 -->
    <div class="page-header">
      <a-button class="back-button" size="large" @click="goBack">
        ← 返回首页
      </a-button>
      <a-space size="middle">
        <a-button v-if="!editMode" @click="toggleEditMode" type="default">
          ✏️ 编辑行程
        </a-button>
        <a-button v-else @click="saveChanges" type="primary">
          💾 保存修改
        </a-button>
        <a-button v-if="editMode" @click="cancelEdit" type="default">
          ❌ 取消编辑
        </a-button>

        <!-- 导出按钮 -->
        <a-dropdown v-if="!editMode">
          <template #overlay>
            <a-menu>
              <a-menu-item key="image" @click="exportAsImage">
                📷 导出为图片
              </a-menu-item>
              <a-menu-item key="pdf" @click="exportAsPDF">
                📄 导出为PDF
              </a-menu-item>
            </a-menu>
          </template>
          <a-button type="default">
            📥 导出行程 <DownOutlined />
          </a-button>
        </a-dropdown>
      </a-space>
    </div>

    <div v-if="tripPlan" class="content-wrapper">
      <!-- 侧边导航 -->
      <div class="side-nav">
        <a-affix :offset-top="80">
          <a-menu mode="inline" :selected-keys="[activeSection]" @click="scrollToSection">
            <a-menu-item key="overview">
              <span>📋 行程概览</span>
            </a-menu-item>
            <a-menu-item key="budget" v-if="tripPlan.budget">
              <span>💰 预算明细</span>
            </a-menu-item>
            <a-menu-item key="map">
              <span>📍 景点地图</span>
            </a-menu-item>
            <a-sub-menu key="days" title="📅 每日行程">
              <a-menu-item v-for="(day, index) in tripPlan.days" :key="`day-${index}`">
                第{{ day.day_index + 1 }}天
              </a-menu-item>
            </a-sub-menu>
            <a-menu-item key="weather" v-if="tripPlan.weather_info && tripPlan.weather_info.length > 0">
              <span>🌤️ 天气信息</span>
            </a-menu-item>
          </a-menu>
        </a-affix>
      </div>

      <!-- 主内容区 -->
      <div class="main-content">
        <!-- 顶部信息区:左侧概览+预算,右侧地图 -->
        <div class="top-info-section">
          <!-- 左侧:行程概览和预算明细 -->
          <div class="left-info">
            <!-- 行程概览 -->
            <a-card id="overview" :title="`${tripPlan.city}旅行计划`" :bordered="false" class="overview-card">
              <div class="overview-content">
                <div class="info-item">
                  <span class="info-label">📅 日期:</span>
                  <span class="info-value">{{ tripPlan.start_date }} 至 {{ tripPlan.end_date }}</span>
                </div>
                <div
                  v-if="tripPlan.trip_mode && tripPlan.trip_mode !== 'single_city' && tripPlan.route_nodes?.length"
                  class="info-item"
                >
                  <span class="info-label">🧭 路线:</span>
                  <span class="info-value route-nodes">{{ tripPlan.route_nodes.join(' → ') }}</span>
                </div>
                <div class="info-item">
                  <span class="info-label">💡 建议:</span>
                  <span class="info-value">{{ tripPlan.overall_suggestions }}</span>
                </div>
                <a-alert
                  v-if="tripPlan.quality_report"
                  class="quality-alert"
                  :type="qualityAlertType"
                  show-icon
                  :message="qualityAlertMessage"
                >
                  <template #description>
                    <ul v-if="qualityDisplayItems.length" class="quality-advice-list">
                      <li v-for="item in qualityDisplayItems" :key="item">{{ item }}</li>
                    </ul>
                    <span v-else>行程基础安排合理，出发前请确认预约、开放时间和当天预报。</span>
                  </template>
                </a-alert>
              </div>
            </a-card>

            <!-- 预算明细 -->
            <a-card id="budget" v-if="tripPlan.budget" title="💰 预算明细" :bordered="false" class="budget-card">
              <div class="budget-grid">
                <div class="budget-item">
                  <div class="budget-label">景点门票</div>
                  <div class="budget-value">¥{{ tripPlan.budget.total_attractions }}</div>
                </div>
                <div class="budget-item">
                  <div class="budget-label">酒店住宿</div>
                  <div class="budget-value">¥{{ tripPlan.budget.total_hotels }}</div>
                </div>
                <div class="budget-item">
                  <div class="budget-label">餐饮费用</div>
                  <div class="budget-value">¥{{ tripPlan.budget.total_meals }}</div>
                </div>
                <div class="budget-item">
                  <div class="budget-label">交通费用</div>
                  <div class="budget-value">¥{{ tripPlan.budget.total_transportation }}</div>
                </div>
              </div>
              <div class="budget-total">
                <span class="total-label">预估总费用</span>
                <span class="total-value">¥{{ tripPlan.budget.total }}</span>
              </div>
            </a-card>
          </div>

          <!-- 右侧:地图 -->
          <div class="right-map">
            <a-card id="map" title="📍 景点地图" :bordered="false" class="map-card">
              <div id="amap-container" style="width: 100%; height: 100%"></div>
            </a-card>
          </div>
        </div>

        <!-- 每日行程:可折叠 -->
        <a-card title="📅 每日行程" :bordered="false" class="days-card">
          <a-collapse v-model:activeKey="activeDays" accordion>
            <a-collapse-panel
              v-for="(day, index) in tripPlan.days"
              :key="index"
              :id="`day-${index}`"
            >
              <template #header>
                <div class="day-header">
                  <span class="day-title">第{{ day.day_index + 1 }}天</span>
                  <span class="day-date">{{ day.date }}</span>
                  <a-tag v-if="day.overnight_city" color="blue">住 {{ day.overnight_city }}</a-tag>
                </div>
              </template>

              <!-- 行程基本信息 -->
              <div v-if="day.route_segment" class="route-segment-card">
                <div class="route-segment-main">
                  <span class="route-city">{{ day.route_segment.origin }}</span>
                  <span class="route-arrow">→</span>
                  <span class="route-city">{{ day.route_segment.destination }}</span>
                </div>
                <div class="route-segment-meta">
                  <a-tag color="purple">{{ day.route_segment.transport_mode }}</a-tag>
                  <span v-if="day.route_segment.distance_km">
                    参考 {{ day.route_segment.distance_km.toFixed(0) }} km
                  </span>
                  <span v-else>距离待确认</span>
                  <span>门到门预计 {{ formatDuration(day.route_segment.duration_minutes) }}</span>
                  <a-tag v-if="day.is_transit_day" color="orange">跨城为主</a-tag>
                </div>
                <a-alert
                  v-if="day.route_segment.exceeds_daily_limit"
                  message="该路段超过你设置的每日驾驶上限，建议增加中途住宿点。"
                  type="warning"
                  show-icon
                />
                <a-alert
                  v-else-if="day.route_segment.requires_confirmation"
                  message="交通方式和耗时为规划建议，请在出发前确认实际车次或航班。"
                  type="info"
                  show-icon
                />
              </div>
              <div class="day-info">
                <div class="info-row">
                  <span class="label">📝 行程描述:</span>
                  <span class="value">{{ day.description }}</span>
                </div>
                <div class="info-row">
                  <span class="label">🚗 交通方式:</span>
                  <span class="value">{{ day.transportation }}</span>
                </div>
                <div class="info-row">
                  <span class="label">🏨 住宿:</span>
                  <span class="value">{{ day.accommodation }}</span>
                </div>
              </div>

              <!-- 景点安排 -->
              <a-divider orientation="left">🎯 景点安排</a-divider>
              <!-- 编辑模式：添加景点按钮 -->
              <div v-if="editMode" style="margin-bottom: 12px; text-align: right;">
                <a-button type="dashed" @click="openAddModal(day.day_index)">
                  ➕ 添加景点
                </a-button>
              </div>
              <a-list
                :data-source="day.attractions"
                :grid="{ gutter: 16, column: 2 }"
              >
                <template #renderItem="{ item, index }">
                  <a-list-item>
                    <a-card :title="item.name" size="small" class="attraction-card">
                      <!-- 编辑模式下的操作按钮 -->
                      <template #extra v-if="editMode">
                        <a-space>
                          <a-button
                            size="small"
                            @click="moveAttraction(day.day_index, index, 'up')"
                            :disabled="index === 0"
                          >
                            ↑
                          </a-button>
                          <a-button
                            size="small"
                            @click="moveAttraction(day.day_index, index, 'down')"
                            :disabled="index === day.attractions.length - 1"
                          >
                            ↓
                          </a-button>
                          <a-button
                            size="small"
                            danger
                            @click="deleteAttraction(day.day_index, index)"
                          >
                            🗑️
                          </a-button>
                        </a-space>
                      </template>

                      <!-- 景点序号和价格 -->
                      <div class="attraction-badge-inline">
                        <span class="badge-number">{{ index + 1 }}</span>
                        <span v-if="item.ticket_info && item.ticket_price === 0" class="price-tag-inline">免费</span>
                        <span v-else-if="item.ticket_price" class="price-tag-inline">¥{{ item.ticket_price }}</span>
                      </div>

                      <!-- 编辑模式下可编辑的字段 -->
                      <div v-if="editMode">
                        <p><strong>地址:</strong></p>
                        <a-input v-model:value="item.address" size="small" style="margin-bottom: 8px" />

                        <p><strong>游览时长(分钟):</strong></p>
                        <a-input-number v-model:value="item.visit_duration" :min="10" :max="480" size="small" style="width: 100%; margin-bottom: 8px" />

                        <p><strong>描述:</strong></p>
                        <a-textarea v-model:value="item.description" :rows="2" size="small" style="margin-bottom: 8px" />
                      </div>

                      <!-- 查看模式 -->
                      <div v-else>
                        <!-- 门票信息框 -->
                        <div v-if="item.ticket_info && (item.ticket_info.opening_hours || item.ticket_info.price > 0 || item.ticket_info.purchase_method)" class="ticket-info-box">
                          <div class="ticket-info-header">
                            <span class="ticket-icon">🎫</span>
                            <span v-if="item.ticket_info.price > 0" class="ticket-price-text">¥{{ item.ticket_info.price }}</span>
                            <span v-else-if="item.ticket_info.price === 0 && item.ticket_info.purchase_method && !item.ticket_info.purchase_method.includes('免费')" class="ticket-price-text">详见购票</span>
                            <span v-else class="ticket-price-text free">免费开放</span>
                          </div>
                          <div class="ticket-info-body">
                            <div v-if="item.ticket_info.opening_hours" class="ticket-info-row">
                              <span class="ticket-info-icon">🕐</span>
                              <span class="ticket-info-label">开放时间</span>
                              <span class="ticket-info-value">{{ item.ticket_info.opening_hours }}</span>
                            </div>
                            <div v-if="item.ticket_info.purchase_method && !item.ticket_info.purchase_method.includes('免费开放，无需购票')" class="ticket-info-row">
                              <span class="ticket-info-icon">📱</span>
                              <span class="ticket-info-label">购票方式</span>
                              <span class="ticket-info-value">{{ item.ticket_info.purchase_method }}</span>
                            </div>
                            <div v-if="item.ticket_info.source" class="ticket-info-source">
                              <a-tag :color="item.ticket_info.confidence === 'medium' ? 'blue' : 'orange'">
                                {{ formatConfidence(item.ticket_info.confidence) }}
                              </a-tag>
                              <span>数据来源: {{ formatSource(item.ticket_info.source) }}</span>
                              <a
                                v-if="item.ticket_info.source_url"
                                :href="item.ticket_info.source_url"
                                target="_blank"
                                rel="noopener noreferrer"
                              >查看来源 ↗</a>
                            </div>
                          </div>
                        </div>

                        <p><strong>地址:</strong> {{ item.address }}</p>
                        <p><strong>游览时长:</strong> {{ item.visit_duration }}分钟</p>
                        <p><strong>描述:</strong> {{ item.description }}</p>
                        <p v-if="item.rating"><strong>评分:</strong> {{ item.rating }}⭐</p>
                      </div>
                    </a-card>
                  </a-list-item>
                </template>
              </a-list>

              <!-- 酒店推荐 -->
              <a-divider v-if="day.hotel" orientation="left">🏨 住宿推荐</a-divider>
              <a-card v-if="day.hotel" size="small" class="hotel-card">
                <template #title>
                  <span v-if="!editMode" class="hotel-title">{{ day.hotel.name }}</span>
                  <a-space v-else :size="8" style="width: 100%;">
                    <a-input v-model:value="day.hotel.name" size="small" placeholder="酒店名称" style="width: 200px;" />
                    <a-button size="small" type="primary" :loading="hotelSearching[day.day_index]"
                              @click="handleHotelSearch(day.day_index)">🔍 查找</a-button>
                  </a-space>
                </template>

                <!-- 查看模式 -->
                <a-descriptions v-if="!editMode" :column="2" size="small">
                  <a-descriptions-item label="地址">{{ day.hotel.address }}</a-descriptions-item>
                  <a-descriptions-item label="类型">{{ day.hotel.type }}</a-descriptions-item>
                  <a-descriptions-item label="价格范围">{{ day.hotel.price_range }}</a-descriptions-item>
                  <a-descriptions-item label="评分">{{ day.hotel.rating }}⭐</a-descriptions-item>
                  <a-descriptions-item label="距离" :span="2">{{ day.hotel.distance }}</a-descriptions-item>
                </a-descriptions>

                <!-- 编辑模式 -->
                <div v-else class="edit-fields">
                  <a-row :gutter="12">
                    <a-col :span="12">
                      <p><strong>💰 房价(¥/晚):</strong></p>
                      <a-input-number v-model:value="day.hotel.estimated_cost" :min="0" :max="5000"
                                      size="small" style="width:100%" placeholder="估算房价" />
                    </a-col>
                  </a-row>
                  <a-checkbox v-if="tripPlan.days.length > 1"
                              v-model:checked="syncHotelAllDays[day.day_index]"
                              style="margin-top: 8px;">
                    同步到所有天
                  </a-checkbox>
                  <a-divider style="margin: 10px 0;" />
                  <p style="color: #888; font-size: 12px; margin-bottom: 4px;">以下信息由 API 自动补全：</p>
                  <p><strong>地址:</strong> <span>{{ day.hotel.address || '—' }}</span></p>
                  <a-row :gutter="12">
                    <a-col :span="8">
                      <p><strong>类型:</strong> <span>{{ day.hotel.type || '—' }}</span></p>
                    </a-col>
                    <a-col :span="8">
                      <p><strong>评分:</strong>
                        <span v-if="day.hotel.rating">{{ day.hotel.rating }}⭐</span>
                        <span v-else>—</span>
                      </p>
                    </a-col>
                  </a-row>
                  <p><strong>距离:</strong> <span>{{ day.hotel.distance || '—' }}</span></p>
                </div>
              </a-card>

              <!-- 餐饮安排 -->
              <a-divider orientation="left">🍽️ 餐饮安排</a-divider>

              <!-- 查看模式 -->
              <a-descriptions v-if="!editMode" :column="1" bordered size="small">
                <a-descriptions-item
                  v-for="meal in day.meals"
                  :key="meal.type"
                  :label="getMealLabel(meal.type)"
                >
                  <div class="meal-summary">
                    <span>{{ meal.name }}</span>
                    <span v-if="meal.estimated_cost">（人均约 ¥{{ meal.estimated_cost }}）</span>
                    <a-tag v-if="meal.rating" color="gold">{{ meal.rating }}⭐</a-tag>
                    <a-tag v-else-if="meal.data_source === 'amap'">评分暂无</a-tag>
                  </div>
                  <div v-if="meal.recommended_dish" class="meal-detail">
                    <strong>建议品尝：</strong>{{ meal.recommended_dish }}
                  </div>
                  <div v-if="meal.description" class="meal-detail">{{ meal.description }}</div>
                  <div v-if="meal.address" class="meal-detail"><strong>地址：</strong>{{ meal.address }}</div>
                  <div v-if="meal.data_source === 'amap'" class="meal-source">餐厅数据来源：高德地图</div>
                </a-descriptions-item>
              </a-descriptions>

              <!-- 编辑模式 -->
              <div v-else>
                <a-card
                  v-for="meal in day.meals"
                  :key="meal.type"
                  size="small"
                  :title="getMealLabel(meal.type)"
                  style="margin-bottom: 8px;"
                >
                  <a-space :size="8" style="width: 100%; margin-bottom: 8px;">
                    <a-input v-model:value="meal.name" size="small" placeholder="餐厅名称" style="width: 200px;" />
                    <a-button size="small" type="primary"
                              :loading="mealSearching[`${day.day_index}-${meal.type}`]"
                              @click="handleMealSearch(day.day_index, meal.type)">🔍 查找</a-button>
                  </a-space>
                  <a-divider style="margin: 8px 0;" />
                  <p style="color: #888; font-size: 12px; margin-bottom: 4px;">以下信息由 API 自动补全：</p>
                  <a-row :gutter="12">
                    <a-col :span="12">
                      <p><strong>人均(¥):</strong>
                        <span v-if="meal.estimated_cost">¥{{ meal.estimated_cost }}</span>
                        <span v-else>—</span>
                      </p>
                    </a-col>
                  </a-row>
                  <p><strong>地址:</strong> <span>{{ meal.address || '—' }}</span></p>
                  <p style="margin-top:8px;"><strong>补充描述:</strong></p>
                  <a-input v-model:value="meal.description" size="small" placeholder="如：本地老字号、排队王" />
                </a-card>
              </div>
            </a-collapse-panel>
          </a-collapse>
        </a-card>

        <a-card id="weather" v-if="tripPlan.weather_info && tripPlan.weather_info.length > 0" title="天气信息" style="margin-top: 20px" :bordered="false">
        <a-list
          :data-source="tripPlan.weather_info"
          :grid="{ gutter: 16, column: 3 }"
        >
          <template #renderItem="{ item }">
            <a-list-item>
              <a-card size="small" class="weather-card">
                <div class="weather-date">
                  {{ item.date }}
                  <a-tag v-if="item.location_name" color="cyan">{{ item.location_name }}</a-tag>
                </div>
                <div class="weather-info-row">
                  <span class="weather-icon">☀️</span>
                  <div>
                    <div class="weather-label">白天</div>
                    <div class="weather-value">{{ item.day_weather }} {{ item.day_temp }}°C</div>
                  </div>
                </div>
                <div class="weather-info-row">
                  <span class="weather-icon">🌙</span>
                  <div>
                    <div class="weather-label">夜间</div>
                    <div class="weather-value">{{ item.night_weather }} {{ item.night_temp }}°C</div>
                  </div>
                </div>
                <div class="weather-wind">
                  💨 {{ item.wind_direction }} {{ item.wind_power }}
                </div>
              </a-card>
            </a-list-item>
          </template>
        </a-list>
        </a-card>
      </div>
    </div>

    <a-empty v-else description="没有找到旅行计划数据">
      <template #image>
        <div style="font-size: 80px;">🗺️</div>
      </template>
      <template #description>
        <span style="color: #999;">暂无旅行计划数据,请先创建行程</span>
      </template>
      <a-button type="primary" @click="goBack">返回首页创建行程</a-button>
    </a-empty>

    <!-- 添加景点搜索弹窗 -->
    <a-modal
      v-model:open="showAddModal"
      :title="`添加景点 - 第${addTargetDayIndex + 1}天`"
      :footer="null"
      width="560px"
      @cancel="showAddModal = false"
    >
      <a-space direction="vertical" style="width: 100%;" size="middle">
        <a-input-search
          v-model:value="poiSearchKeyword"
          placeholder='输入景点名称，如「滕王阁」'
          enter-button="搜索"
          size="large"
          :loading="poiSearching"
          @search="handlePOISearch"
          @press-enter="handlePOISearch"
        />
        <div v-if="poiSearchResults.length > 0" class="poi-search-results">
          <div
            v-for="item in poiSearchResults"
            :key="item.id"
            class="poi-search-item"
            @click="addAttractionToDay(item)"
          >
            <div class="poi-name">{{ item.name }}</div>
            <div class="poi-addr">{{ item.address }}</div>
            <div class="poi-tag">{{ item.type || '景点' }}</div>
          </div>
        </div>
        <div v-else-if="!poiSearching" style="text-align: center; color: #999; padding: 40px 0;">
          🔍 输入关键词搜索景点，点击结果即可添加
        </div>
      </a-space>
    </a-modal>

    <!-- 回到顶部按钮 -->
    <a-back-top :visibility-height="300">
      <div class="back-top-button">
        ↑
      </div>
    </a-back-top>
  </div>
</template>

<script setup lang="ts">
import { computed, h, ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { DownOutlined } from '@ant-design/icons-vue'
import AMapLoader from '@amap/amap-jsapi-loader'
import html2canvas from 'html2canvas'
import jsPDF from 'jspdf'
import { searchPOI, searchPOIDetail, fetchTicketInfo, fetchDrivingRoute } from '@/services/api'
import type { TripPlan, POISearchItem } from '@/types'
import { saveTripPlanHistory } from '@/services/tripHistory'

const router = useRouter()
const tripPlan = ref<TripPlan | null>(null)
const historyPlanId = ref<string | null>(sessionStorage.getItem('tripPlanHistoryId'))
const editMode = ref(false)
const originalPlan = ref<TripPlan | null>(null)
const activeSection = ref('overview')
const activeDays = ref<number[]>([0]) // 默认展开第一天
let map: any = null

const formatDuration = (minutes: number) => {
  if (!minutes) return '待确认'
  const hours = Math.floor(minutes / 60)
  const remainder = minutes % 60
  if (!hours) return `${remainder}分钟`
  return remainder ? `${hours}小时${remainder}分钟` : `${hours}小时`
}

const persistCurrentPlan = () => {
  if (!tripPlan.value) return
  const historyEntry = saveTripPlanHistory(tripPlan.value, historyPlanId.value || undefined)
  historyPlanId.value = historyEntry.id
  sessionStorage.setItem('tripPlanHistoryId', historyEntry.id)
  sessionStorage.setItem('tripPlan', JSON.stringify(tripPlan.value))
}

const extractUserAdvice = (rawAdvice: string): string[] => {
  const cleanText = rawAdvice
    .replace(/^#{1,6}\s*.*$/gm, '')
    .replace(/\*\*/g, '')
    .replace(/`/g, '')
    .replace(/\r/g, '')
    .trim()
  if (!cleanText) return []

  const numberedItems = cleanText
    .split(/(?:^|\s)\d+[.、]\s*/)
    .map(item => item.trim())
    .filter(Boolean)
  const candidates = numberedItems.length > 1 ? numberedItems : [cleanText]

  return candidates
    .map((item) => {
      const sentences = item.match(/[^。！？]+[。！？]?/g) || [item]
      return sentences.slice(0, 2).join('').trim()
    })
    .filter(Boolean)
    .slice(0, 3)
}

const qualityDisplayItems = computed(() => {
  const report = tripPlan.value?.quality_report
  if (!report) return []
  const issues = report.issues.map(item => item.message)
  const advice = extractUserAdvice(report.critic_advice)
  return [...issues, ...advice.filter(item => !issues.includes(item))].slice(0, 3)
})

const qualityAlertType = computed<'success' | 'info' | 'warning' | 'error'>(() => {
  const report = tripPlan.value?.quality_report
  if (!report) return 'info'
  if (report.issues.some(item => item.severity === 'error')) return 'error'
  if (qualityDisplayItems.value.length) return 'warning'
  return 'success'
})

const qualityAlertMessage = computed(() => {
  const report = tripPlan.value?.quality_report
  if (!report) return ''
  if (report.issues.some(item => item.severity === 'error')) return '行程存在需要调整的问题'
  if (qualityDisplayItems.value.length) return '行程提醒'
  return '行程基础校验通过'
})

type EditValidationResult = {
  errors: string[]
  warnings: string[]
}

const roundMoney = (value: number) => Math.round(value * 100) / 100

const haversineDistance = (from: { longitude: number, latitude: number }, to: { longitude: number, latitude: number }) => {
  const earthRadiusKm = 6371
  const toRadians = (degree: number) => degree * Math.PI / 180
  const latitudeDelta = toRadians(to.latitude - from.latitude)
  const longitudeDelta = toRadians(to.longitude - from.longitude)
  const a = Math.sin(latitudeDelta / 2) ** 2
    + Math.cos(toRadians(from.latitude)) * Math.cos(toRadians(to.latitude)) * Math.sin(longitudeDelta / 2) ** 2
  return earthRadiusKm * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

const recalculateEditedBudget = (plan: TripPlan) => {
  const totalAttractions = roundMoney(plan.days.reduce(
    (sum, day) => sum + day.attractions.reduce((daySum, attraction) => daySum + Number(attraction.ticket_price || 0), 0), 0
  ))
  const totalHotels = roundMoney(plan.days.reduce((sum, day) => sum + Number(day.hotel?.estimated_cost || 0), 0))
  const totalMeals = roundMoney(plan.days.reduce(
    (sum, day) => sum + day.meals.reduce((daySum, meal) => daySum + Number(meal.estimated_cost || 0), 0), 0
  ))
  const totalTransportation = Number(plan.budget?.total_transportation || 0)
  plan.budget = {
    total_attractions: totalAttractions,
    total_hotels: totalHotels,
    total_meals: totalMeals,
    total_transportation: totalTransportation,
    total: roundMoney(totalAttractions + totalHotels + totalMeals + totalTransportation)
  }
}

const validateEditedPlan = (plan: TripPlan): EditValidationResult => {
  const errors: string[] = []
  const warnings: string[] = []
  const seenAttractions = new Set<string>()

  if (!plan.days.length) errors.push('行程至少需要保留一天。')

  plan.days.forEach((day, dayPosition) => {
    const dayLabel = `第${dayPosition + 1}天`
    if (!day.attractions.length) errors.push(`${dayLabel}没有景点，无法保存。`)
    if (day.attractions.length > 3) warnings.push(`${dayLabel}安排了${day.attractions.length}个景点，可能过于紧凑。`)

    day.attractions.forEach((attraction) => {
      const name = attraction.name.trim()
      if (!name) {
        errors.push(`${dayLabel}存在未命名景点。`)
        return
      }
      const normalizedName = name.toLocaleLowerCase()
      if (seenAttractions.has(normalizedName)) {
        errors.push(`景点“${name}”重复出现，请保留一次或替换。`)
      }
      seenAttractions.add(normalizedName)
      if (!Number.isFinite(attraction.location?.longitude) || !Number.isFinite(attraction.location?.latitude)) {
        errors.push(`景点“${name}”缺少有效坐标，请重新搜索后添加。`)
      }
      if (!Number.isFinite(attraction.visit_duration) || attraction.visit_duration < 10) {
        errors.push(`景点“${name}”的游览时长无效。`)
      }
    })

    for (let index = 1; index < day.attractions.length; index += 1) {
      const previous = day.attractions[index - 1]
      const current = day.attractions[index]
      if (previous.location && current.location) {
        const distance = haversineDistance(previous.location, current.location)
        if (distance > 20) warnings.push(`${dayLabel}“${previous.name}”到“${current.name}”直线约${distance.toFixed(1)}公里，建议确认交通时间。`)
      }
    }

    if (day.meals.some(meal => !meal.name.trim())) warnings.push(`${dayLabel}有未填写名称的餐饮安排。`)
    if (day.hotel && !day.hotel.name.trim()) warnings.push(`${dayLabel}未填写住宿名称。`)

    const weather = plan.weather_info?.find(item => item.date === day.date)
    const badWeather = weather && /雨|雪|雷|暴/.test(`${weather.day_weather}${weather.night_weather}`)
    const hasOutdoorAttraction = day.attractions.some(item => item.category === 'nature')
    if (badWeather && hasOutdoorAttraction) warnings.push(`${dayLabel}预报有${weather?.day_weather}，但包含户外景点，建议准备备选方案。`)
  })

  return { errors, warnings }
}

const updateEditQualityReport = (plan: TripPlan, warnings: string[]) => {
  plan.quality_report = {
    passed: warnings.length === 0,
    issues: warnings.map((message, index) => ({ code: `edit_warning_${index + 1}`, severity: 'warning', message })),
    critic_advice: warnings.length ? '以上是编辑后的提醒；你可以按自己的取舍保留当前安排。' : '编辑后的基础规则校验通过，预算已重新核算。'
  }
}

// ========== 添加景点相关状态 ==========
const showAddModal = ref(false)
const addTargetDayIndex = ref(0)
const poiSearchKeyword = ref('')
const poiSearchResults = ref<POISearchItem[]>([])
const poiSearching = ref(false)

// ========== 酒店/餐饮 POI 查找状态 ==========
const hotelSearching = ref<Record<number, boolean>>({})   // day_index → loading
const mealSearching = ref<Record<string, boolean>>({})    // "dayIndex-mealType" → loading
const syncHotelAllDays = ref<Record<number, boolean>>({})  // day_index → 是否同步到所有天

/** 打开添加景点弹窗 */
const openAddModal = (dayIndex: number) => {
  addTargetDayIndex.value = dayIndex
  poiSearchKeyword.value = ''
  poiSearchResults.value = []
  showAddModal.value = true
}

/** 执行 POI 搜索 */
const handlePOISearch = async () => {
  if (!poiSearchKeyword.value.trim()) {
    message.warning('请输入景点关键词')
    return
  }
  if (!tripPlan.value) return

  poiSearching.value = true
  poiSearchResults.value = []
  try {
    const res = await searchPOI(tripPlan.value.city, poiSearchKeyword.value.trim())
    if (res.success && res.data) {
      poiSearchResults.value = res.data
      if (res.data.length === 0) {
        message.info('未找到匹配的景点')
      }
    } else {
      message.error(res.message || '搜索失败')
    }
  } catch (e: any) {
    message.error(e.message || '搜索失败')
  } finally {
    poiSearching.value = false
  }
}

/** 将选中 POI 添加到指定天（异步拉取门票信息） */
const addAttractionToDay = async (item: POISearchItem) => {
  if (!tripPlan.value) return

  const day = tripPlan.value.days[addTargetDayIndex.value]
  if (!day) return

  // 判断是否为餐饮/住宿类 POI
  const tc = item.typecode || ''
  const isFoodOrHotel = tc.startsWith('05') || tc.startsWith('10')
  if (isFoodOrHotel) {
    message.warning(`"${item.name}" 是餐饮/住宿类POI，不适合作为景点添加`)
    return
  }

  // 判断景点类别（高德 typecode 前2位大类：11=科教文化 14=公共设施 08=体育休闲 06=购物）
  let category = 'sightseeing'
  const name = item.name
  const tc2 = tc.length >= 2 ? tc.substring(0, 2) : ''
  if (['11', '14'].includes(tc2)) {
    category = 'culture'
  } else if (tc2 === '08') {
    category = 'entertainment'
  } else if (tc2 === '06') {
    category = 'shopping'
  } else if (['博物馆', '遗址', '古迹', '陵', '故居', '纪念'].some(k => name.includes(k))) {
    category = 'culture'
  } else if (['公园', '山', '湖', '海', '森林', '植物', '动物园'].some(k => name.includes(k))) {
    category = 'nature'
  }

  // 先加入景点（门票信息异步获取中）
  day.attractions.push({
    name: item.name,
    address: item.address || '',
    location: {
      longitude: item.lng,
      latitude: item.lat
    },
    visit_duration: 90,
    description: '',
    category: category,
    ticket_price: 0,
    rating: undefined,
    image_url: undefined,
    ticket_info: undefined
  })

  const newIndex = day.attractions.length - 1
  message.success(`已添加 "${item.name}" 到第${addTargetDayIndex.value + 1}天`)
  showAddModal.value = false

  // 更新 sessionStorage
  persistCurrentPlan()

  // 异步拉取门票信息（LLM估价）
  if (tripPlan.value.city) {
    const ticketInfo = await fetchTicketInfo(item.name, tripPlan.value.city)
    if (ticketInfo && day.attractions[newIndex]) {
      day.attractions[newIndex].ticket_price = ticketInfo.price
      day.attractions[newIndex].ticket_info = ticketInfo
      persistCurrentPlan()
      const priceStr = ticketInfo.price > 0 ? `¥${ticketInfo.price}` : '免费'
      message.success(`"${item.name}" 门票: ${priceStr}`)
    }
  }
}

// ---- 酒店查找：自动补全地址/类型/评分 ----
const handleHotelSearch = async (dayIndex: number) => {
  if (!tripPlan.value) return
  const day = tripPlan.value.days[dayIndex]
  if (!day?.hotel) return

  const hotelName = day.hotel.name.trim()
  if (!hotelName) {
    message.warning('请先输入酒店名称')
    return
  }

  hotelSearching.value[dayIndex] = true
  try {
    const res = await searchPOIDetail(tripPlan.value.city, hotelName)
    if (res.success && res.data) {
      const d = res.data
      day.hotel.address = d.address || day.hotel.address
      day.hotel.type = d.type || day.hotel.type
      if (d.rating) day.hotel.rating = d.rating
      message.success(`已找到 "${d.name}"，信息已自动补全`)
      persistCurrentPlan()
    } else {
      message.info('未找到匹配的酒店，请检查名称')
    }
  } catch (e: any) {
    message.error(e.message || '查找失败')
  } finally {
    hotelSearching.value[dayIndex] = false
  }
}

// ---- 餐饮查找：自动补全地址/人均 ----
const handleMealSearch = async (dayIndex: number, mealType: string) => {
  if (!tripPlan.value) return
  const day = tripPlan.value.days[dayIndex]
  const meal = day?.meals?.find(m => m.type === mealType)
  if (!meal) return

  const mealName = meal.name.trim()
  if (!mealName) {
    message.warning('请先输入餐厅名称')
    return
  }

  const key = `${dayIndex}-${mealType}`
  mealSearching.value[key] = true
  try {
    const res = await searchPOIDetail(tripPlan.value.city, mealName)
    if (res.success && res.data) {
      const d = res.data
      meal.address = d.address || meal.address
      // 高德 biz_ext.cost 是人均消费字符串
      if (d.cost) {
        const costNum = parseFloat(d.cost)
        if (!isNaN(costNum) && costNum > 0) {
          meal.estimated_cost = costNum
        }
      }
      message.success(`已找到 "${d.name}"，信息已自动补全`)
      persistCurrentPlan()
    } else {
      message.info('未找到匹配的餐厅，请检查名称')
    }
  } catch (e: any) {
    message.error(e.message || '查找失败')
  } finally {
    mealSearching.value[key] = false
  }
}

onMounted(async () => {
  const data = sessionStorage.getItem('tripPlan')
  if (data) {
    tripPlan.value = JSON.parse(data)
    if (!historyPlanId.value) persistCurrentPlan()
    // 等待DOM渲染完成后初始化地图
    await nextTick()
    initMap()
  }
})

const goBack = () => {
  router.push('/')
}

// 滚动到指定区域
const scrollToSection = ({ key }: { key: string }) => {
  activeSection.value = key
  const element = document.getElementById(key)
  if (element) {
    element.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

// 切换编辑模式
const toggleEditMode = () => {
  editMode.value = true
  // 保存原始数据用于取消编辑
  originalPlan.value = JSON.parse(JSON.stringify(tripPlan.value))
  // 初始化酒店同步开关（默认全部勾选）
  if (tripPlan.value) {
    tripPlan.value.days.forEach(d => {
      syncHotelAllDays.value[d.day_index] = true
    })
  }
  message.info('进入编辑模式')
}

const syncSelectedHotel = (plan: TripPlan) => {
  for (const day of plan.days) {
    if (syncHotelAllDays.value[day.day_index] && day.hotel) {
      const sourceHotel = JSON.parse(JSON.stringify(day.hotel))
      plan.days.forEach((targetDay) => {
        targetDay.hotel = JSON.parse(JSON.stringify(sourceHotel))
      })
      break
    }
  }
}

const commitChanges = (warnings: string[]) => {
  if (!tripPlan.value) return
  updateEditQualityReport(tripPlan.value, warnings)
  persistCurrentPlan()
  editMode.value = false
  message.success(warnings.length ? '修改已保存，已更新预算与行程提醒' : '修改已保存，预算已重新核算')

  if (map) {
    map.destroy()
    routePolylines.length = 0
  }
  nextTick(() => {
    initMap()
  })
}

// 保存修改：无效数据阻止保存；可行性风险由用户确认后仍可保存。
const saveChanges = () => {
  if (!tripPlan.value) return
  syncSelectedHotel(tripPlan.value)
  recalculateEditedBudget(tripPlan.value)
  const { errors, warnings } = validateEditedPlan(tripPlan.value)

  if (errors.length) {
    Modal.error({
      title: '暂时不能保存',
      content: h('ul', { style: 'padding-left: 20px; margin: 12px 0 0;' }, errors.map(item => h('li', item)))
    })
    return
  }

  if (warnings.length) {
    Modal.confirm({
      title: `发现 ${warnings.length} 个行程提醒`,
      content: h('div', [
        h('p', '这些情况不阻止保存，请确认你愿意保留当前安排：'),
        h('ul', { style: 'padding-left: 20px; margin: 8px 0 0;' }, warnings.map(item => h('li', item)))
      ]),
      okText: '仍然保存',
      cancelText: '继续编辑',
      onOk: () => commitChanges(warnings)
    })
    return
  }

  commitChanges([])
}

// 取消编辑
const cancelEdit = () => {
  if (originalPlan.value) {
    tripPlan.value = JSON.parse(JSON.stringify(originalPlan.value))
  }
  editMode.value = false
  message.info('已取消编辑')
}

// 删除景点
const deleteAttraction = (dayIndex: number, attrIndex: number) => {
  if (!tripPlan.value) return

  const day = tripPlan.value.days[dayIndex]
  if (day.attractions.length <= 1) {
    message.warning('每天至少需要保留一个景点')
    return
  }

  day.attractions.splice(attrIndex, 1)
  message.success('景点已删除')
}

// 移动景点顺序
const moveAttraction = (dayIndex: number, attrIndex: number, direction: 'up' | 'down') => {
  if (!tripPlan.value) return

  const day = tripPlan.value.days[dayIndex]
  const attractions = day.attractions

  if (direction === 'up' && attrIndex > 0) {
    [attractions[attrIndex], attractions[attrIndex - 1]] = [attractions[attrIndex - 1], attractions[attrIndex]]
  } else if (direction === 'down' && attrIndex < attractions.length - 1) {
    [attractions[attrIndex], attractions[attrIndex + 1]] = [attractions[attrIndex + 1], attractions[attrIndex]]
  }
}

const getMealLabel = (type: string): string => {
  const labels: Record<string, string> = {
    breakfast: '早餐',
    lunch: '午餐',
    dinner: '晚餐',
    snack: '小吃'
  }
  return labels[type] || type
}

const formatSource = (source: string): string => {
  const map: Record<string, string> = {
    'amap': '高德地图',
    'tavily': 'AI搜索',
    'tavily+llm': 'AI搜索+LLM',
    'llm-estimate': 'LLM估算',
    'classified': '规则识别',
    'amap+tavily': '高德地图 + AI搜索',
    'amap+tavily+llm': '高德地图 + AI搜索',
    'amap+llm-estimate': '高德地图 + LLM估算'
  }
  return map[source] || source
}

const formatConfidence = (confidence?: string): string => {
  return confidence === 'medium' ? '网页检索' : 'LLM估算'
}




// 导出为图片
const exportAsImage = async () => {
  try {
    message.loading({ content: '正在生成图片...', key: 'export', duration: 0 })

    const element = document.querySelector('.main-content') as HTMLElement
    if (!element) {
      throw new Error('未找到内容元素')
    }

    // 创建一个独立的容器
    const exportContainer = document.createElement('div')
    exportContainer.style.width = element.offsetWidth + 'px'
    exportContainer.style.backgroundColor = '#f5f7fa'
    exportContainer.style.padding = '20px'

    // 复制所有内容
    exportContainer.innerHTML = element.innerHTML

    // 处理地图截图
    const mapContainer = document.getElementById('amap-container')
    if (mapContainer && map) {
      const mapCanvas = mapContainer.querySelector('canvas')
      if (mapCanvas) {
        const mapSnapshot = mapCanvas.toDataURL('image/png')
        const exportMapContainer = exportContainer.querySelector('#amap-container')
        if (exportMapContainer) {
          exportMapContainer.innerHTML = `<img src="${mapSnapshot}" style="width:100%;height:100%;object-fit:cover;" />`
        }
      }
    }

    // 移除所有ant-card类,替换为纯div
    const cards = exportContainer.querySelectorAll('.ant-card')
    cards.forEach((card) => {
      const cardEl = card as HTMLElement
      try {
        cardEl.className = '' // 移除所有类
        cardEl.style.setProperty('background-color', '#ffffff')
        cardEl.style.setProperty('border-radius', '12px')
        cardEl.style.setProperty('box-shadow', '0 4px 12px rgba(0, 0, 0, 0.1)')
        cardEl.style.setProperty('margin-bottom', '20px')
        cardEl.style.setProperty('overflow', 'hidden')
      } catch (err) {
        console.error('设置卡片样式失败:', err)
      }
    })

    // 处理卡片头部
    const cardHeads = exportContainer.querySelectorAll('.ant-card-head')
    cardHeads.forEach((head) => {
      const headEl = head as HTMLElement
      try {
        headEl.style.setProperty('background-color', '#667eea')
        headEl.style.setProperty('color', '#ffffff')
        headEl.style.setProperty('padding', '16px 24px')
        headEl.style.setProperty('font-size', '18px')
        headEl.style.setProperty('font-weight', '600')
      } catch (err) {
        console.error('设置卡片头部样式失败:', err)
      }
    })

    // 处理卡片内容
    const cardBodies = exportContainer.querySelectorAll('.ant-card-body')
    cardBodies.forEach((body) => {
      const bodyEl = body as HTMLElement
      bodyEl.style.setProperty('background-color', '#ffffff')
      bodyEl.style.setProperty('padding', '24px')
    })

    // 处理酒店卡片头部
    const hotelCards = exportContainer.querySelectorAll('.hotel-card')
    hotelCards.forEach((card) => {
      const head = card.querySelector('.ant-card-head') as HTMLElement
      if (head) {
        head.style.setProperty('background-color', '#1976d2')
      }
      (card as HTMLElement).style.setProperty('background-color', '#e3f2fd')
    })

    // 处理天气卡片
    const weatherCards = exportContainer.querySelectorAll('.weather-card')
    weatherCards.forEach((card) => {
      (card as HTMLElement).style.setProperty('background-color', '#e0f7fa')
    })

    // 处理预算总计
    const budgetTotal = exportContainer.querySelector('.budget-total')
    if (budgetTotal) {
      const el = budgetTotal as HTMLElement
      el.style.setProperty('background-color', '#667eea')
      el.style.setProperty('color', '#ffffff')
      el.style.setProperty('padding', '20px')
      el.style.setProperty('border-radius', '12px')
      el.style.setProperty('margin-bottom', '20px')
    }

    // 处理预算项
    const budgetItems = exportContainer.querySelectorAll('.budget-item')
    budgetItems.forEach((item) => {
      const el = item as HTMLElement
      el.style.setProperty('background-color', '#f5f7fa')
      el.style.setProperty('padding', '16px')
      el.style.setProperty('border-radius', '8px')
      el.style.setProperty('margin-bottom', '12px')
    })

    // 添加到body(隐藏)
    exportContainer.style.position = 'absolute'
    exportContainer.style.left = '-9999px'
    document.body.appendChild(exportContainer)

    const canvas = await html2canvas(exportContainer, {
      backgroundColor: '#f5f7fa',
      scale: 2,
      logging: false,
      useCORS: true,
      allowTaint: true
    })

    // 移除容器
    document.body.removeChild(exportContainer)

    // 转换为图片并下载
    const link = document.createElement('a')
    link.download = `旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.png`
    link.href = canvas.toDataURL('image/png')
    link.click()

    message.success({ content: '图片导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出图片失败:', error)
    message.error({ content: `导出图片失败: ${error.message}`, key: 'export' })
  }
}

// 导出为PDF
const exportAsPDF = async () => {
  try {
    message.loading({ content: '正在生成PDF...', key: 'export', duration: 0 })

    const element = document.querySelector('.main-content') as HTMLElement
    if (!element) {
      throw new Error('未找到内容元素')
    }

    // 创建一个独立的容器
    const exportContainer = document.createElement('div')
    exportContainer.style.width = element.offsetWidth + 'px'
    exportContainer.style.backgroundColor = '#f5f7fa'
    exportContainer.style.padding = '20px'

    // 复制所有内容
    exportContainer.innerHTML = element.innerHTML

    // 处理地图截图
    const mapContainer = document.getElementById('amap-container')
    if (mapContainer && map) {
      const mapCanvas = mapContainer.querySelector('canvas')
      if (mapCanvas) {
        const mapSnapshot = mapCanvas.toDataURL('image/png')
        const exportMapContainer = exportContainer.querySelector('#amap-container')
        if (exportMapContainer) {
          exportMapContainer.innerHTML = `<img src="${mapSnapshot}" style="width:100%;height:100%;object-fit:cover;" />`
        }
      }
    }

    // 移除所有ant-card类,替换为纯div
    const cards = exportContainer.querySelectorAll('.ant-card')
    cards.forEach((card) => {
      const cardEl = card as HTMLElement
      try {
        cardEl.className = ''
        cardEl.style.setProperty('background-color', '#ffffff')
        cardEl.style.setProperty('border-radius', '12px')
        cardEl.style.setProperty('box-shadow', '0 4px 12px rgba(0, 0, 0, 0.1)')
        cardEl.style.setProperty('margin-bottom', '20px')
        cardEl.style.setProperty('overflow', 'hidden')
      } catch (err) {
        console.error('设置卡片样式失败:', err)
      }
    })

    // 处理卡片头部
    const cardHeads = exportContainer.querySelectorAll('.ant-card-head')
    cardHeads.forEach((head) => {
      const headEl = head as HTMLElement
      try {
        headEl.style.setProperty('background-color', '#667eea')
        headEl.style.setProperty('color', '#ffffff')
        headEl.style.setProperty('padding', '16px 24px')
        headEl.style.setProperty('font-size', '18px')
        headEl.style.setProperty('font-weight', '600')
      } catch (err) {
        console.error('设置卡片头部样式失败:', err)
      }
    })

    // 处理卡片内容
    const cardBodies = exportContainer.querySelectorAll('.ant-card-body')
    cardBodies.forEach((body) => {
      const bodyEl = body as HTMLElement
      bodyEl.style.setProperty('background-color', '#ffffff')
      bodyEl.style.setProperty('padding', '24px')
    })

    // 处理酒店卡片头部
    const hotelCards = exportContainer.querySelectorAll('.hotel-card')
    hotelCards.forEach((card) => {
      const head = card.querySelector('.ant-card-head') as HTMLElement
      if (head) {
        head.style.setProperty('background-color', '#1976d2')
      }
      (card as HTMLElement).style.setProperty('background-color', '#e3f2fd')
    })

    // 处理天气卡片
    const weatherCards = exportContainer.querySelectorAll('.weather-card')
    weatherCards.forEach((card) => {
      (card as HTMLElement).style.setProperty('background-color', '#e0f7fa')
    })

    // 处理预算总计
    const budgetTotal = exportContainer.querySelector('.budget-total')
    if (budgetTotal) {
      const el = budgetTotal as HTMLElement
      el.style.setProperty('background-color', '#667eea')
      el.style.setProperty('color', '#ffffff')
      el.style.setProperty('padding', '20px')
      el.style.setProperty('border-radius', '12px')
      el.style.setProperty('margin-bottom', '20px')
    }

    // 处理预算项
    const budgetItems = exportContainer.querySelectorAll('.budget-item')
    budgetItems.forEach((item) => {
      const el = item as HTMLElement
      el.style.setProperty('background-color', '#f5f7fa')
      el.style.setProperty('padding', '16px')
      el.style.setProperty('border-radius', '8px')
      el.style.setProperty('margin-bottom', '12px')
    })

    // 添加到body(隐藏)
    exportContainer.style.position = 'absolute'
    exportContainer.style.left = '-9999px'
    document.body.appendChild(exportContainer)

    const canvas = await html2canvas(exportContainer, {
      backgroundColor: '#f5f7fa',
      scale: 2,
      logging: false,
      useCORS: true,
      allowTaint: true
    })

    // 移除容器
    document.body.removeChild(exportContainer)

    const imgData = canvas.toDataURL('image/png')
    const pdf = new jsPDF({
      orientation: 'portrait',
      unit: 'mm',
      format: 'a4'
    })

    const imgWidth = 210 // A4宽度(mm)
    const imgHeight = (canvas.height * imgWidth) / canvas.width

    // 如果内容高度超过一页,分页处理
    let heightLeft = imgHeight
    let position = 0

    pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight)
    heightLeft -= 297 // A4高度

    while (heightLeft > 0) {
      position = heightLeft - imgHeight
      pdf.addPage()
      pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight)
      heightLeft -= 297
    }

    pdf.save(`旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.pdf`)

    message.success({ content: 'PDF导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出PDF失败:', error)
    message.error({ content: `导出PDF失败: ${error.message}`, key: 'export' })
  }
}


// 初始化地图
const initMap = async () => {
  try {
    const AMap = await AMapLoader.load({
      key: import.meta.env.VITE_AMAP_WEB_JS_KEY,  // 高德地图Web端(JS API) Key
      version: '2.0',
      plugins: ['AMap.Marker', 'AMap.Polyline', 'AMap.InfoWindow']
    })

    // 创建地图实例
    map = new AMap.Map('amap-container', {
      zoom: 12,
      center: [116.397128, 39.916527], // 默认中心点(北京)
      viewMode: '3D'
    })

    // 添加景点标记
    await addAttractionMarkers(AMap)

    message.success('地图加载成功')
  } catch (error) {
    console.error('地图加载失败:', error)
    message.error('地图加载失败')
  }
}

// 添加景点标记
const addAttractionMarkers = async (AMap: any) => {
  if (!tripPlan.value) return

  const markers: any[] = []
  const allAttractions: any[] = []
  const allHotels: any[] = []
  const seenHotels = new Set<string>()

  // 天数颜色调色板
  const DAY_COLORS = ['#4CAF50', '#2196F3', '#FF9800', '#9C27B0', '#E91E63', '#00BCD4', '#FF5722', '#795548']
  const totalDays = tripPlan.value.days.length
  const isSingleDay = totalDays === 1

  // 收集所有景点和酒店
  tripPlan.value.days.forEach((day, dayIndex) => {
    day.attractions.forEach((attraction, attrIndex) => {
      if (attraction.location && attraction.location.longitude && attraction.location.latitude) {
        allAttractions.push({
          ...attraction,
          dayIndex,
          attrIndex
        })
      }
    })
    // 收集酒店（同位置去重）
    if (day.hotel && day.hotel.location && day.hotel.location.longitude && day.hotel.location.latitude) {
      const key = `${day.hotel.location.longitude},${day.hotel.location.latitude}`
      if (!seenHotels.has(key)) {
        seenHotels.add(key)
        allHotels.push({ ...day.hotel, dayIndex })
      }
    }
  })

  // 全局景点序号（多天时用数字，单天时不用）
  let globalIndex = 0

  // 创建景点标记
  allAttractions.forEach((attraction) => {
    const dayColor = DAY_COLORS[attraction.dayIndex % DAY_COLORS.length]
    globalIndex++

    let labelHtml: string
    if (isSingleDay) {
      // 单天：简洁圆点
      labelHtml = `<div style="background: ${dayColor}; width: 14px; height: 14px; border-radius: 50%; border: 2px solid white; box-shadow: 0 2px 6px rgba(0,0,0,0.35);"></div>`
    } else {
      // 多天：不同颜色 + 数字
      labelHtml = `<div style="background: ${dayColor}; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px; white-space: nowrap;">${globalIndex}</div>`
    }

    const marker = new AMap.Marker({
      position: [attraction.location.longitude, attraction.location.latitude],
      title: attraction.name,
      label: {
        content: labelHtml,
        offset: new AMap.Pixel(0, -30)
      }
    })

    const infoWindow = new AMap.InfoWindow({
      content: `
        <div style="padding: 10px;">
          <h4 style="margin: 0 0 8px 0;">📍 ${attraction.name}</h4>
          <p style="margin: 4px 0;"><strong>地址:</strong> ${attraction.address}</p>
          <p style="margin: 4px 0;"><strong>游览时长:</strong> ${attraction.visit_duration}分钟</p>
          <p style="margin: 4px 0;"><strong>描述:</strong> ${attraction.description}</p>
          <p style="margin: 4px 0; color: ${dayColor};"><strong>第${attraction.dayIndex + 1}天 景点${attraction.attrIndex + 1}</strong></p>
        </div>
      `,
      offset: new AMap.Pixel(0, -30)
    })

    marker.on('click', () => {
      infoWindow.open(map, marker.getPosition())
    })

    markers.push(marker)
  })

  // 创建酒店标记（🏨 蓝色特殊样式）
  allHotels.forEach((hotel) => {
    const marker = new AMap.Marker({
      position: [hotel.location.longitude, hotel.location.latitude],
      title: hotel.name,
      label: {
        content: `<div style="background: #E67E22; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px; white-space: nowrap;">🏨 ${hotel.name.length > 8 ? hotel.name.slice(0, 8) + '...' : hotel.name}</div>`,
        offset: new AMap.Pixel(0, -30)
      }
    })

    const hotelInfo = new AMap.InfoWindow({
      content: `
        <div style="padding: 10px;">
          <h4 style="margin: 0 0 8px 0;">🏨 ${hotel.name}</h4>
          <p style="margin: 4px 0;"><strong>地址:</strong> ${hotel.address}</p>
          <p style="margin: 4px 0;"><strong>价格:</strong> ${hotel.price_range || '暂无'}</p>
          <p style="margin: 4px 0;"><strong>类型:</strong> ${hotel.type || '未知'}</p>
          <p style="margin: 4px 0; color: #E67E22;"><strong>住宿 | 距景点密集区 ${hotel.distance || '未知'}</strong></p>
        </div>
      `,
      offset: new AMap.Pixel(0, -30)
    })

    marker.on('click', () => {
      hotelInfo.open(map, marker.getPosition())
    })

    markers.push(marker)
  })

  // 添加标记到地图
  map.add(markers)

  // 自动调整视野以包含所有标记
  if (markers.length > 0) {
    map.setFitView(markers)
  }

  // 绘制路线（异步获取驾车路径）
  await drawRoutes(AMap, allAttractions)
}

// 路线颜色（与天颜色一致）
const ROUTE_COLORS = ['#4CAF50', '#2196F3', '#FF9800', '#9C27B0', '#E91E63', '#00BCD4', '#FF5722', '#795548']
const routePolylines: any[] = []  // 存储所有路线 polyline，便于清理

// 清理旧路线
const clearRoutes = () => {
  if (routePolylines.length > 0) {
    map?.remove(routePolylines)
    routePolylines.length = 0
  }
}

// 绘制路线（通过后端 REST API 调高德驾车导航，获取真实道路路径）
const drawRoutes = async (_AMap: any, attractions: any[]) => {
  if (attractions.length < 2) return

  // 先清理旧路线
  clearRoutes()

  // 按天分组
  const dayGroups: any = {}
  attractions.forEach(attr => {
    if (!dayGroups[attr.dayIndex]) dayGroups[attr.dayIndex] = []
    dayGroups[attr.dayIndex].push(attr)
  })

  // 串行处理每天
  for (const [dayIdx, dayAttractions] of Object.entries(dayGroups)) {
    const attrs = dayAttractions as any[]
    if (attrs.length < 2) continue

    const routeColor = ROUTE_COLORS[parseInt(dayIdx) % ROUTE_COLORS.length]

    for (let i = 0; i < attrs.length - 1; i++) {
      const start = attrs[i]
      const end = attrs[i + 1]

      const origin = `${start.location.longitude},${start.location.latitude}`
      const destination = `${end.location.longitude},${end.location.latitude}`

      // 调后端 REST API 获取驾车导航路径
      const drivingPath = await fetchDrivingRoute(origin, destination)

      // 短暂延迟避免频率限制
      await new Promise(r => setTimeout(r, 300))

      // 绘制路线 polyline
      if (drivingPath && drivingPath.length > 0) {
        const polyline = new _AMap.Polyline({
          path: drivingPath,
          strokeColor: routeColor,
          strokeWeight: 5,
          strokeOpacity: 0.75,
          strokeStyle: 'solid',
          showDir: true,
          dirColor: routeColor,
        })
        map.add(polyline)
        routePolylines.push(polyline)
      } else {
        // 兜底：直线连接
        const fallbackPath: [number, number][] = [
          [start.location.longitude, start.location.latitude],
          [end.location.longitude, end.location.latitude],
        ]
        const polyline = new _AMap.Polyline({
          path: fallbackPath,
          strokeColor: routeColor,
          strokeWeight: 4,
          strokeOpacity: 0.5,
          strokeStyle: 'dashed',
          showDir: true,
        })
        map.add(polyline)
        routePolylines.push(polyline)
      }
    }
  }
}
</script>

<style scoped>
.result-container {
  min-height: 100vh;
  background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
  padding: 40px 20px;
}

.page-header {
  max-width: 1200px;
  margin: 0 auto 30px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  animation: fadeInDown 0.6s ease-out;
}

.back-button {
  border-radius: 8px;
  font-weight: 500;
}

/* 内容布局 */
.content-wrapper {
  max-width: 1400px;
  margin: 0 auto;
  display: flex;
  gap: 24px;
}

.side-nav {
  width: 240px;
  flex-shrink: 0;
}

.side-nav :deep(.ant-menu) {
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  background: white;
}

.side-nav :deep(.ant-menu-item) {
  margin: 4px 8px;
  border-radius: 8px;
  transition: all 0.3s ease;
}

.side-nav :deep(.ant-menu-item-selected) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

.side-nav :deep(.ant-menu-item:hover) {
  background: rgba(102, 126, 234, 0.1);
}

.main-content {
  flex: 1;
  min-width: 0;
}

/* 景点序号和价格（无图片） */
.attraction-badge-inline {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.attraction-badge-inline .badge-number {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  font-size: 16px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  flex-shrink: 0;
}

.price-tag-inline {
  background: rgba(255, 77, 79, 0.9);
  color: white;
  padding: 4px 12px;
  border-radius: 12px;
  font-weight: bold;
  font-size: 14px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}

/* 门票信息框 */
.ticket-info-box {
  margin-bottom: 16px;
  border: 1px solid #e8d5b7;
  border-radius: 10px;
  overflow: hidden;
  background: linear-gradient(135deg, #fffbf0 0%, #fff8e7 100%);
}

.ticket-info-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  background: linear-gradient(135deg, #f5a623 0%, #f7b84e 100%);
  color: white;
}

.ticket-icon {
  font-size: 18px;
}

.ticket-price-text {
  font-size: 18px;
  font-weight: 700;
  color: white;
}

.ticket-price-text.free {
  color: #e8ffd0;
}

.ticket-info-body {
  padding: 10px 16px;
}

.ticket-info-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 13px;
}

.ticket-info-icon {
  flex-shrink: 0;
  font-size: 14px;
}

.ticket-info-label {
  flex-shrink: 0;
  color: #8b6914;
  font-weight: 600;
  min-width: 60px;
}

.ticket-info-value {
  color: #333;
  line-height: 1.5;
  word-break: break-all;
}

.ticket-info-source {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed #e8d5b7;
  font-size: 11px;
  color: #bbb;
  text-align: right;
}

.ticket-info-source a {
  margin-left: 8px;
}

/* 天气卡片样式 */
.weather-card {
  background: linear-gradient(135deg, #e0f7fa 0%, #b2ebf2 100%);
  border: none !important;
  transition: all 0.3s ease;
}

.weather-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 8px 16px rgba(0, 0, 0, 0.15);
}

.weather-date {
  font-size: 16px;
  font-weight: bold;
  color: #00796b;
  margin-bottom: 12px;
  text-align: center;
}

.weather-info-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.weather-icon {
  font-size: 24px;
}

.weather-label {
  font-size: 12px;
  color: #666;
}

.weather-value {
  font-size: 16px;
  font-weight: 600;
  color: #00796b;
}

.weather-wind {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(0, 121, 107, 0.2);
  text-align: center;
  color: #00796b;
  font-size: 14px;
}

/* 回到顶部按钮 */
.back-top-button {
  width: 50px;
  height: 50px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  font-weight: bold;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  cursor: pointer;
  transition: all 0.3s ease;
}

.back-top-button:hover {
  transform: scale(1.1);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
}

/* 酒店卡片样式 */
.hotel-card {
  background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
  border: none !important;
}

.hotel-card :deep(.ant-card-head) {
  background: linear-gradient(135deg, #1976d2 0%, #1565c0 100%);
}

.hotel-title {
  color: white !important;
  font-weight: 600;
}

/* 顶部信息区布局 */
.top-info-section {
  display: flex;
  gap: 20px;
  margin-bottom: 20px;
}

.left-info {
  flex: 0 0 400px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.right-map {
  flex: 1;
}

/* 行程概览卡片 */
.overview-card {
  height: fit-content;
}

.overview-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.info-label {
  font-size: 14px;
  font-weight: 600;
  color: #666;
}

.info-value {
  font-size: 15px;
  color: #333;
  line-height: 1.6;
}

/* 预算卡片 */
.budget-card {
  height: fit-content;
}

.budget-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}

.budget-item {
  text-align: center;
  padding: 12px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}

.budget-label {
  font-size: 13px;
  color: #666;
  margin-bottom: 8px;
}

.budget-value {
  font-size: 20px;
  font-weight: 700;
  color: #1890ff;
}

.budget-total {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 8px;
  color: white;
}

.total-label {
  font-size: 16px;
  font-weight: 600;
}

.total-value {
  font-size: 28px;
  font-weight: 700;
}

/* 地图卡片 */
.map-card {
  height: 100%;
  min-height: 500px;
}

.map-card :deep(.ant-card-body) {
  height: calc(100% - 57px);
  padding: 0;
}

/* 每日行程卡片 */
.days-card {
  margin-top: 20px;
}

.day-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.day-title {
  font-size: 18px;
  font-weight: 600;
  color: #333;
}

.day-date {
  font-size: 14px;
  color: #999;
}

.day-info {
  margin-bottom: 20px;
  padding: 16px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}

.info-row {
  display: flex;
  gap: 12px;
  margin-bottom: 8px;
}

.info-row:last-child {
  margin-bottom: 0;
}

.info-row .label {
  font-weight: 600;
  color: #666;
  min-width: 100px;
}

.info-row .value {
  color: #333;
  flex: 1;
}

/* 卡片样式优化 */
:deep(.ant-card) {
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  margin-bottom: 20px;
  transition: all 0.3s ease;
  animation: fadeInUp 0.6s ease-out;
}

:deep(.ant-card:hover) {
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
}

:deep(.ant-card-head) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white !important;
  border-radius: 12px 12px 0 0;
  font-weight: 600;
}

:deep(.ant-card-head-title) {
  color: white !important;
  font-size: 18px;
}

:deep(.ant-card-head-title span) {
  color: white !important;
}

/* Collapse样式 */
:deep(.ant-collapse) {
  border: none;
  background: transparent;
}

:deep(.ant-collapse-item) {
  margin-bottom: 16px;
  border: 1px solid #e8e8e8;
  border-radius: 12px;
  overflow: hidden;
}

:deep(.ant-collapse-header) {
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  padding: 16px 20px !important;
  font-weight: 600;
}

:deep(.ant-collapse-content) {
  border-top: 1px solid #e8e8e8;
}

:deep(.ant-collapse-content-box) {
  padding: 20px;
}

/* 统计卡片样式 */
:deep(.ant-statistic-title) {
  font-size: 14px;
  color: #666;
  margin-bottom: 8px;
}

:deep(.ant-statistic-content) {
  font-size: 24px;
  font-weight: 600;
  color: #1890ff;
}

/* 景点卡片样式 */
:deep(.ant-list-item) {
  transition: all 0.3s ease;
}

:deep(.ant-list-item:hover) {
  transform: scale(1.02);
}

/* 动画 */
@keyframes fadeInDown {
  from {
    opacity: 0;
    transform: translateY(-20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 响应式设计 */
@media (max-width: 768px) {
  .result-container {
    padding: 20px 10px;
  }

  .page-header {
    flex-direction: column;
    gap: 16px;
  }
}

/* 添加景点搜索弹窗 */
.poi-search-results {
  max-height: 360px;
  overflow-y: auto;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
}

.poi-search-item {
  padding: 12px 16px;
  border-bottom: 1px solid #f5f5f5;
  cursor: pointer;
  transition: background 0.15s;
}
.poi-search-item:last-child {
  border-bottom: none;
}
.poi-search-item:hover {
  background: #e6f7ff;
}

.poi-name {
  font-size: 15px;
  font-weight: 600;
  color: #333;
  margin-bottom: 4px;
}

.poi-addr {
  font-size: 12px;
  color: #999;
  margin-bottom: 4px;
}

.poi-tag {
  font-size: 11px;
  color: #667eea;
  background: #f0f2ff;
  display: inline-block;
  padding: 1px 8px;
  border-radius: 4px;
}

.route-nodes {
  line-height: 1.8;
}

.route-segment-card {
  margin-bottom: 16px;
  padding: 16px;
  border: 1px solid #d9dfff;
  border-radius: 10px;
  background: linear-gradient(135deg, #f6f8ff 0%, #ffffff 100%);
}

.route-segment-main {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 10px;
}

.route-city {
  font-size: 17px;
  font-weight: 700;
  color: #3448a4;
}

.route-arrow {
  color: #7b61c9;
  font-size: 20px;
}

.route-segment-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 10px;
  color: #5d6472;
}

.route-segment-card :deep(.ant-alert) {
  margin-top: 10px;
}

.meal-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  font-weight: 600;
}

.meal-detail {
  margin-top: 4px;
  color: #595959;
  line-height: 1.6;
}

.meal-source {
  margin-top: 4px;
  color: #999;
  font-size: 12px;
}
</style>

