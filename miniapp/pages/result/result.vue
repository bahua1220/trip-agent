<template>
  <view class="result-page" :style="{ height: pageHeight }">
    <!-- ====== 空数据 ====== -->
    <view v-if="!tripPlan" class="empty-state">
      <text class="empty-icon">🗺️</text>
      <text class="empty-text">暂无行程数据</text>
      <button class="btn-primary" @click="goHome">返回首页</button>
    </view>

    <template v-else>
      <!-- ====== 顶栏 ====== -->
      <view class="top-bar" :style="{ paddingTop: statusBarHeight + 'px' }">
        <view
          class="top-heading"
          :style="{ paddingRight: capsuleSafeRight + 'px', minHeight: capsuleRowHeight + 'px' }"
        >
          <view class="home-entry" @click="goHome">
            <text class="home-entry__arrow">⌂</text>
          </view>
          <view class="top-left">
            <text class="top-city">{{ tripPlan.city }} {{ tripPlan.days.length }} 日行程</text>
            <text class="top-date">{{ tripPlan.start_date }} — {{ tripPlan.end_date }} · {{ tripPlan.days.length }} 天</text>
          </view>
        </view>
      </view>

      <view class="ai-disclosure">
        <text class="ai-disclosure__tag">AI 生成内容</text>
        <text class="ai-disclosure__copy">行程仅供参考，出发前请核验开放时间、票价及交通信息</text>
      </view>
      <view class="quality-banner" v-if="tripPlan.quality_report && !tripPlan.quality_report.passed">
        <text class="quality-banner__title">检测到行程异常</text>
        <text class="quality-banner__copy">{{ tripPlan.quality_report.critic_advice || '系统未能自动消除全部时间或路线冲突，建议重新生成，也可以按需要编辑。' }}</text>
      </view>

      <!-- ====== 地图面板 ====== -->
      <view class="map-panel" v-if="showMap">
        <map
          :key="`day-map-${currentDay}`"
          class="map-view"
          :latitude="dayMapCenter.latitude"
          :longitude="dayMapCenter.longitude"
          :markers="dayMarkers"
          :include-points="dayMapPoints"
          :polyline="dayPolylines"
          :scale="13"
          show-location
          @markertap="onMarkerTap"
        />
        <view class="map-legend">
          <text class="legend-item">🏠 酒店</text>
          <text class="legend-item">📍 景点</text>
          <text class="legend-item" v-if="dayPolylines.length">🔵 行车路线</text>
          <text class="legend-loading" v-if="routesLoading">加载路线中...</text>
        </view>
      </view>

      <!-- ====== 天标签栏 ====== -->
      <scroll-view
        class="day-tabs"
        scroll-x
        scroll-with-animation
        :show-scrollbar="false"
        :scroll-into-view="`day-tab-${currentDay}`"
      >
        <view class="day-tabs-inner">
          <view
            v-for="(d, i) in tripPlan.days"
            :id="`day-tab-${i}`"
            :key="i"
            class="day-tab"
            :class="{ active: currentDay === i }"
            @click="switchDay(i)"
          >
            <text class="tab-label">第{{ i + 1 }}天</text>
            <text class="tab-date">{{ d.date.slice(5) }}</text>
          </view>
        </view>
      </scroll-view>

      <!-- ====== 每日 swiper ====== -->
      <swiper
        class="day-swiper"
        :current="currentDay"
        @change="onSwiperChange"
      >
        <swiper-item v-for="(day, di) in tripPlan.days" :key="di">
          <scroll-view class="day-scroll" scroll-y>
            <view class="day-desc-box" v-if="day.description">
              <text class="day-desc">{{ day.description }}</text>
            </view>
            <view class="day-warning-box" v-if="dayIssues(di).length">
              <text class="day-warning-title">系统未能自动消除以下冲突</text>
              <text class="day-warning-text" v-for="issue in dayIssues(di)" :key="issue.code + issue.message">
                {{ issue.message }}
              </text>
            </view>

            <!-- ====== 酒店 ====== -->
            <view class="card hotel-card" v-if="day.hotel">
              <view class="card-head">
                <text class="hotel-emoji">🏠</text>
                <view class="hotel-body">
                  <text class="hotel-name">{{ day.hotel.name }}</text>
                  <text class="sub-text" v-if="day.hotel.address">📍 {{ day.hotel.address }}</text>
                  <view class="tag-row" v-if="day.hotel.price_range || day.hotel.rating">
                    <text class="tag" v-if="day.hotel.price_range">{{ day.hotel.price_range }}</text>
                    <text class="tag" v-if="day.hotel.rating">⭐ {{ day.hotel.rating }}</text>
                    <text class="tag price" v-if="day.hotel.estimated_cost">¥{{ day.hotel.estimated_cost }}/晚</text>
                  </view>
                </view>
                <text class="del-btn" v-if="editMode" @click="removeHotel(day, di)">✕</text>
              </view>
            </view>
            <!-- 编辑模式：添加酒店 -->
            <view class="add-card" v-if="editMode && !day.hotel" @click="startSearch('hotel', di)">
              <text>+ 添加酒店</text>
            </view>

            <view class="schedule-heading">
              <text>今天的安排</text>
              <text class="schedule-edit" @click="toggleEditMode">{{ editMode ? '完成编辑' : '编辑日程' }}</text>
            </view>

            <!-- ====== 酒店 / 餐饮 / 景点 / 交通时间轴 ====== -->
            <view class="timeline-list">
              <template v-for="(item, ti) in displayTimeline(day)" :key="`${item.kind}-${ti}`">
                <view class="timeline-row transport-row" v-if="item.kind === 'transport'" @click="openTransportPicker(di, item._timeline_index ?? ti, item)">
                  <view class="timeline-time muted">{{ item.start_time || '' }}</view>
                  <view class="timeline-axis">
                    <view class="timeline-line"></view>
                    <view class="transport-dot">{{ transportIcon(item.transport_mode) }}</view>
                    <view class="timeline-line"></view>
                  </view>
                  <view class="transport-info">
                    <view class="transport-title-row">
                      <text class="transport-main">{{ item.transport_mode || '交通' }}约{{ item.duration_minutes }}分钟</text>
                      <text class="transport-change" v-if="item.alternatives?.length">更改 ›</text>
                    </view>
                    <text class="transport-sub" v-if="item.distance_km">约{{ item.distance_km }}公里 · {{ item.from_name }} → {{ item.to_name }}</text>
                    <text class="transport-sub" v-else>{{ item.from_name }} → {{ item.to_name }}</text>
                    <text class="transport-customized" v-if="item.customized">已按你的选择调整</text>
                    <text class="transport-warning" v-else-if="transportAdvice(item)">{{ transportAdvice(item) }}</text>
                  </view>
                </view>

                <view class="timeline-row" v-else>
                  <view class="timeline-time">
                    <text>{{ item.start_time || (item.kind === 'hub' && item.source === 'user_selected_untimed' ? '待定' : '--:--') }}</text>
                    <text class="timeline-end" v-if="item.end_time">{{ item.end_time }}</text>
                  </view>
                  <view class="timeline-axis">
                    <view class="timeline-line"></view>
                    <view class="event-dot" :class="`event-dot--${item.kind}`"></view>
                    <view class="timeline-line"></view>
                  </view>

                  <view class="card timeline-card hotel-stop" v-if="item.kind === 'hotel'">
                    <view class="card-head">
                      <text class="hotel-emoji">🏠</text>
                      <view class="hotel-body">
                        <text class="hotel-name">{{ item.name }}</text>
                        <text class="sub-text">{{ item.end_time ? '办理入住或整理行李' : (ti === 0 ? '从这里开始今天的行程' : '返回酒店取行李或休息') }}</text>
                      </view>
                    </view>
                  </view>

                  <view class="card timeline-card hub-stop" v-else-if="item.kind === 'hub'">
                    <view class="card-head">
                      <text class="hotel-emoji">🚉</text>
                      <view class="hotel-body">
                        <text class="hotel-name">{{ item.name }}</text>
                        <text class="sub-text">{{ hubSubtitle(item, ti) }}</text>
                      </view>
                    </view>
                  </view>

                  <view class="card timeline-card" v-else-if="item.kind === 'attraction'">
                    <view class="card-head">
                      <view class="attr-num">{{ (item.reference_index ?? 0) + 1 }}</view>
                      <view class="attr-body">
                        <text class="attr-name">{{ timelineEntity(day, item)?.name || item.name }}</text>
                        <text class="sub-text" v-if="item.duration_minutes">⏱ 游览约{{ item.duration_minutes }}分钟</text>
                        <text class="sub-text" v-if="timelineEntity(day, item)?.address">📍 {{ timelineEntity(day, item).address }}</text>
                        <text class="desc-text" v-if="timelineEntity(day, item)?.description">{{ timelineEntity(day, item).description }}</text>
                        <view class="tag-row" v-if="timelineEntity(day, item)?.ticket_info || timelineEntity(day, item)?.ticket_price > 0 || timelineEntity(day, item)?.rating">
                          <text class="tag price" v-if="verifiedFreeTicket(timelineEntity(day, item))">🎫 免费</text>
                          <text class="tag pending" v-else-if="ticketNeedsConfirmation(timelineEntity(day, item))">🎫 票价待核实</text>
                          <text class="tag price" v-else-if="timelineEntity(day, item)?.ticket_price > 0">🎫 ¥{{ timelineEntity(day, item).ticket_price }}</text>
                          <text class="tag" v-if="timelineEntity(day, item)?.rating">⭐ {{ timelineEntity(day, item).rating }}</text>
                        </view>
                      </view>
                      <text class="del-btn" v-if="editMode" @click="removeTimelineItem(day, item, di)">✕</text>
                    </view>
                  </view>

                  <view class="card timeline-card meal-card" v-else-if="item.kind === 'meal'">
                    <view class="meal-row">
                      <text class="meal-icon">{{ mealIcon(item.meal_type) }}</text>
                      <view class="meal-info">
                        <text class="meal-period">{{ mealTypeName(item.meal_type) }}</text>
                        <text class="meal-name">{{ timelineEntity(day, item)?.name || item.name }}</text>
                        <text class="sub-text" v-if="timelineEntity(day, item)?.address">📍 {{ timelineEntity(day, item).address }}</text>
                        <text class="sub-text dish" v-if="timelineEntity(day, item)?.recommended_dish">建议品尝：{{ timelineEntity(day, item).recommended_dish }}</text>
                        <text class="sub-text" v-if="timelineEntity(day, item)?.opening_hours">营业时间：{{ timelineEntity(day, item).opening_hours }}</text>
                      </view>
                      <view class="meal-right">
                        <text class="meal-cost" v-if="timelineEntity(day, item)?.estimated_cost">¥{{ timelineEntity(day, item).estimated_cost }}/人</text>
                        <text class="del-btn" v-if="editMode" @click="removeTimelineItem(day, item, di)">✕</text>
                      </view>
                    </view>
                  </view>

                  <view class="card timeline-card free-time-card" v-else-if="item.kind === 'note'">
                    <view class="card-head">
                      <text class="free-time-icon">☕</text>
                      <view class="attr-body">
                        <text class="attr-name">{{ item.name }}</text>
                        <text class="sub-text">约{{ item.duration_minutes }}分钟，可休息或在附近自由活动</text>
                      </view>
                    </view>
                  </view>
                </view>
              </template>
            </view>

            <view class="add-card" v-if="editMode" @click="startSearch('attr', di)">
              <text>+ 添加景点</text>
            </view>
            <view class="add-card" v-if="editMode" @click="startSearch('meal', di)">
              <text>+ 添加餐饮</text>
            </view>

            <!-- 交通住宿说明 -->
            <view class="meta-bar" v-if="day.transportation || day.accommodation">
              <text v-if="day.transportation">🚗 {{ day.transportation }}</text>
              <text v-if="day.accommodation">🛏 {{ day.accommodation }}</text>
            </view>
            <view style="height: 80rpx;"></view>
          </scroll-view>
        </swiper-item>
      </swiper>

      <view class="detail-bottom-nav">
        <view class="detail-nav-item active" @click="showMap = false">
          <text class="detail-nav-dot"></text>
          <text>行程</text>
        </view>
        <view class="detail-nav-item" :class="{ active: showMap }" @click="showMap = !showMap">
          <text>地图</text>
        </view>
        <view class="detail-nav-item" @click="showBudget = true">
          <text>预算</text>
        </view>
      </view>
    </template>

    <!-- ====== POI 搜索弹窗 ====== -->
    <view class="modal-mask" v-if="searchModal.show" @click="searchModal.show = false">
      <view class="modal-box" @click.stop>
        <text class="modal-title">
          {{ searchModal.type === 'hotel' ? '🏨 搜索酒店' : searchModal.type === 'meal' ? '🍽️ 搜索餐厅' : '🎯 搜索景点' }}
        </text>
        <!-- 搜索栏 -->
        <view class="search-row">
          <input
            class="search-input"
            v-model="searchModal.keyword"
            :placeholder="searchModal.type === 'hotel' ? '输入酒店名称...' : searchModal.type === 'meal' ? '输入餐厅名称...' : '输入景点名称...'"
            @confirm="doSearch"
          />
          <button class="search-btn" @click="doSearch" :loading="searchModal.loading">搜索</button>
        </view>
        <view v-if="searchModal.type === 'attr' && searchModal.removedResults.length" class="recommend-section">
          <text class="recommend-title">调整出的备选景点</text>
          <text class="recommend-tip">系统为保证时间可执行而移出，仅供备选，不需要你重新安排</text>
          <scroll-view class="search-results restore-results" scroll-y>
            <view
              v-for="(r, ri) in searchModal.removedResults"
              :key="'restore-' + ri"
              class="search-item restore-item"
              :class="{ picked: attractionKey(searchModal.pickedResult) === attractionKey(r) }"
              @click="pickResult(r)"
            >
              <text class="sr-name">{{ r.name }}</text>
              <text class="sr-reason">调整原因：{{ r.restoreReason }}</text>
              <text class="sr-addr" v-if="r.address">📍 {{ r.address }}</text>
            </view>
          </scroll-view>
        </view>
        <view v-if="searchModal.type === 'attr'" class="recommend-heading">
          <text class="recommend-title">{{ searchModal.searched ? '搜索结果' : '城市景点推荐' }}</text>
          <text class="recommend-tip">仅展示高德中的景点类 POI，可继续搜索其他地点</text>
        </view>
        <!-- 结果列表 -->
        <scroll-view class="search-results" scroll-y v-if="searchModal.results.length > 0">
          <view
            v-for="(r, ri) in searchModal.results"
            :key="ri"
            class="search-item"
            :class="{ picked: attractionKey(searchModal.pickedResult) === attractionKey(r) }"
            @click="pickResult(r)"
          >
            <text class="sr-name">{{ r.name }}</text>
            <text class="sr-addr" v-if="r.address">📍 {{ r.address }}</text>
            <view class="sr-meta">
              <text v-if="r.rating">⭐ {{ r.rating }}</text>
              <text v-if="r.cost">💰 ¥{{ r.cost }}/人</text>
              <text v-if="r.opentime">🕐 {{ r.opentime }}</text>
            </view>
          </view>
        </scroll-view>
        <text class="no-result" v-if="searchModal.loading && searchModal.type === 'attr'">
          正在加载景点候选…
        </text>
        <text class="no-result" v-if="!searchModal.loading && searchModal.searched && !searchModal.results.length">
          没有匹配的景点，可以换个名称搜索或手动输入
        </text>
        <!-- 手动输入 -->
        <view class="manual-add">
          <text class="manual-label">或手动输入：</text>
          <input class="modal-input" v-model="searchModal.manualName" placeholder="名称" />
          <input class="modal-input" v-model="searchModal.manualDesc" placeholder="描述/地址（可选）" />
          <view class="manual-row">
            <input class="modal-input half" v-model="searchModal.manualCost" placeholder="费用（可选）" type="number" />
            <input class="modal-input half" v-if="searchModal.type === 'attr'" v-model="searchModal.manualDuration" placeholder="时长/分钟（可选）" type="number" />
            <picker
              v-if="searchModal.type === 'meal'"
              mode="selector"
              :range="mealTypeLabels"
              :value="searchModal.mealTypeIdx"
              @change="e => searchModal.mealTypeIdx = e.detail.value"
            >
              <view class="modal-input half picker-like">{{ mealTypeLabels[searchModal.mealTypeIdx] }}</view>
            </picker>
          </view>
        </view>
        <view class="modal-btns">
          <button class="btn-cancel" @click="searchModal.show = false">取消</button>
          <button class="btn-confirm" @click="confirmAdd">确定</button>
        </view>
      </view>
    </view>

    <!-- ====== 单段交通方式选择 ====== -->
    <view class="modal-mask" v-if="transportModal.show" @click="transportModal.show = false">
      <view class="modal-box transport-modal" @click.stop>
        <text class="modal-title">更改这一段交通</text>
        <text class="transport-route-name">{{ transportModal.item?.from_name }} → {{ transportModal.item?.to_name }}</text>
        <text class="transport-default-tip">只修改这一段，不会改变全程交通偏好</text>
        <view
          class="transport-option"
          :class="{ selected: option.mode === transportModal.item?.transport_mode && option.duration_minutes === transportModal.item?.duration_minutes }"
          v-for="(option, oi) in transportModal.item?.alternatives || []"
          :key="`${option.route_mode}-${oi}`"
          @click="applyTransportOption(option)"
        >
          <text class="transport-option-icon">{{ transportIcon(option.mode) }}</text>
          <view class="transport-option-body">
            <view class="transport-option-title">
              <text>{{ option.mode }}</text>
              <text class="fastest-tag" v-if="isFastestOption(option)">较快</text>
            </view>
            <text class="transport-option-distance" v-if="option.distance_km">约{{ option.distance_km }}公里</text>
          </view>
          <text class="transport-option-time">约{{ option.duration_minutes }}分钟</text>
        </view>
        <button class="btn-cancel transport-close" @click="transportModal.show = false">取消</button>
      </view>
    </view>

    <!-- ====== 预算弹窗 ====== -->
    <view class="modal-mask" v-if="showBudget" @click="showBudget = false">
      <view class="modal-box" @click.stop>
        <text class="modal-title">💰 预算明细</text>
        <view class="budget-grid">
          <view class="b-item"><text class="b-label">景点门票</text><text class="b-val">¥{{ budgetData.attrs }}</text></view>
          <view class="b-item"><text class="b-label">酒店住宿</text><text class="b-val">¥{{ budgetData.hotels }}</text></view>
          <view class="b-item"><text class="b-label">餐饮费用</text><text class="b-val">¥{{ budgetData.meals }}</text></view>
          <view class="b-item"><text class="b-label">交通费用</text><text class="b-val">¥{{ budgetData.transport }}</text></view>
        </view>
        <view class="budget-total">
          <text>预估总费用</text>
          <text class="total-num">¥{{ budgetData.total }}</text>
        </view>
        <button class="btn-confirm" @click="showBudget = false">关闭</button>
      </view>
    </view>

    <!-- ====== 天气弹窗 ====== -->
    <view class="modal-mask" v-if="showWeather" @click="showWeather = false">
      <view class="modal-box" @click.stop>
        <text class="modal-title">🌤️ 天气预报</text>
        <view class="weather-item" v-for="w in (tripPlan?.weather_info || [])" :key="w.date">
          <text class="w-date">{{ w.date }}</text>
          <text>☀️ 白天 {{ w.day_weather }} {{ w.day_temp }}°C</text>
          <text>🌙 夜间 {{ w.night_weather }} {{ w.night_temp }}°C</text>
        </view>
        <text class="sub-text" v-if="!tripPlan?.weather_info?.length">暂无天气数据</text>
        <button class="btn-confirm" @click="showWeather = false">关闭</button>
      </view>
    </view>
  </view>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { searchPOI, fetchAttractionSuggestions, searchPOIDetail, fetchDrivingRoute, saveHistoryEntry, reflowTripPlan } from '@/api/index.js'

// ====== 基础状态 ======
const tripPlan = ref(null)
const currentDay = ref(0)
const pageHeight = ref('100vh')
const statusBarHeight = ref(0)
const capsuleSafeRight = ref(0)
const capsuleRowHeight = ref(40)
const showMap = ref(true)
const showBudget = ref(false)
const showWeather = ref(false)
const editMode = ref(false)
const routesLoading = ref(false)
const dayRoutes = ref([])  // 当前天的路线 [ { points: [...] }, ... ]
const editedDays = new Set()
const reflowing = ref(false)
const transportModal = reactive({ show: false, dayIndex: -1, itemIndex: -1, item: null })

// 餐饮类型
const mealTypes = [
  { value: 'breakfast', label: '早餐', icon: '🥐' },
  { value: 'lunch',     label: '午餐', icon: '🍱' },
  { value: 'dinner',    label: '晚餐', icon: '🍲' },
  { value: 'snack',     label: '小吃', icon: '🍰' }
]
const mealTypeLabels = mealTypes.map(t => t.icon + ' ' + t.label)

// ====== POI 搜索弹窗 ======
const searchModal = reactive({
  show: false,
  type: 'attr',       // 'attr' | 'meal' | 'hotel'
  dayIndex: 0,
  keyword: '',
  loading: false,
  searched: false,
  results: [],        // 搜索结果
  removedResults: [], // 系统或用户调整掉、可以恢复的景点
  pickedResult: null, // 选中的结果
  // 手动输入
  manualName: '',
  manualDesc: '',
  manualCost: '',
  manualDuration: '',
  mealTypeIdx: 2      // 默认午餐
})

// ====== 预算（实时计算）======
const budgetData = computed(() => {
  let attrs = 0, hotels = 0, meals = 0, transport = 0
  if (!tripPlan.value) return { attrs, hotels, meals, transport, total: 0 }
  tripPlan.value.days.forEach(day => {
    (day.attractions || []).forEach(a => { attrs += a.ticket_price || 0 })
    if (day.hotel) hotels += day.hotel.estimated_cost || 0
    // 如果没有 estimate_cost，用 price_range 估算
    if (day.hotel && !day.hotel.estimated_cost && day.hotel.price_range) {
      const nums = day.hotel.price_range.match(/\d+/g)
      if (nums && nums.length >= 2) hotels += Math.round((parseInt(nums[0]) + parseInt(nums[1])) / 2)
    }
    (day.meals || []).forEach(m => { meals += m.estimated_cost || 0 })
    transport += 0
  })
  return { attrs, hotels, meals, transport, total: attrs + hotels + meals + transport }
})

// ====== 地图数据（仅当天）======
const dayMarkers = computed(() => {
  const markers = []
  if (!tripPlan.value) return markers
  const day = tripPlan.value.days[currentDay.value]
  if (!day) return markers
  let id = 0
  // 酒店
  if (isValidMapPoint(day.hotel?.location)) {
    markers.push({
      id: id++,
      latitude: Number(day.hotel.location.latitude),
      longitude: Number(day.hotel.location.longitude),
      title: '酒店: ' + day.hotel.name,
      width: 32,
      height: 32,
      label: { content: '🏠', fontSize: 20, width: 32, height: 32, anchorY: -3 },
      callout: { content: day.hotel.name, display: 'BYCLICK', fontSize: 12, borderRadius: 8, padding: 8, bgColor: '#fff8e1' }
    })
  }
  // 景点
  (day.attractions || []).forEach((a, ai) => {
    if (isValidMapPoint(a.location)) {
      markers.push({
        id: id++,
        latitude: Number(a.location.latitude),
        longitude: Number(a.location.longitude),
        title: a.name,
        width: 28,
        height: 28,
        label: { content: String(ai + 1), fontSize: 14, color: '#fff', bgColor: '#667eea', borderRadius: 14, width: 28, height: 28, padding: 4, anchorY: -3 },
        callout: { content: a.name, display: 'BYCLICK', fontSize: 12, borderRadius: 8, padding: 8 }
      })
    }
  })
  return markers
})

const isValidMapPoint = (location) => {
  const latitude = Number(location?.latitude)
  const longitude = Number(location?.longitude)
  return Number.isFinite(latitude) && Number.isFinite(longitude) &&
    latitude >= -90 && latitude <= 90 && longitude >= -180 && longitude <= 180 &&
    !(latitude === 0 && longitude === 0)
}

// 自动缩放时纳入当天全部景点和酒店，避免地图一直停在固定酒店上。
const dayMapPoints = computed(() => dayMarkers.value.map(marker => ({
  latitude: Number(marker.latitude),
  longitude: Number(marker.longitude)
})))

const dayMapCenter = computed(() => {
  const day = tripPlan.value?.days?.[currentDay.value]
  const attractionPoints = (day?.attractions || [])
    .map(attraction => attraction.location)
    .filter(isValidMapPoint)

  // 景点才是当天地图的主体。多景点时取中心点，只有酒店时才退回酒店。
  const points = attractionPoints.length > 0
    ? attractionPoints
    : (isValidMapPoint(day?.hotel?.location) ? [day.hotel.location] : [])

  if (points.length > 0) {
    return {
      latitude: points.reduce((sum, point) => sum + Number(point.latitude), 0) / points.length,
      longitude: points.reduce((sum, point) => sum + Number(point.longitude), 0) / points.length
    }
  }
  return { latitude: 39.9042, longitude: 116.4074 }
})

const dayPolylines = computed(() => {
  const polylines = []
  dayRoutes.value.forEach((route, ri) => {
    if (route.points && route.points.length > 0) {
      polylines.push({
        points: route.points,
        color: '#667eeaCC',
        width: 6,
        dottedLine: false,
        arrowLine: true
      })
    }
  })
  return polylines
})

// ====== 加载路线 ======
async function loadDayRoutes() {
  const day = tripPlan.value?.days[currentDay.value]
  // 当前地图接口只返回驾车轨迹。公共交通/智能混合不画伪驾车线，
  // 逐段方式和耗时以时间轴中后端核算的结果为准。
  if (!day || !String(day.transportation || '').includes('自驾') || !day.attractions || day.attractions.length < 2) {
    dayRoutes.value = []
    return
  }
  routesLoading.value = true
  const routes = []
  for (let i = 0; i < day.attractions.length - 1; i++) {
    const from = day.attractions[i]
    const to = day.attractions[i + 1]
    if (from.address && to.address) {
      try {
        const res = await fetchDrivingRoute(from.address, to.address)
        if (res?.data?.path) {
          routes.push({
            points: res.data.path.map(p => ({ longitude: p[0], latitude: p[1] }))
          })
        }
      } catch (e) { console.warn('路线加载失败:', from.name, '→', to.name) }
    }
  }
  dayRoutes.value = routes
  routesLoading.value = false
}

// 切换到某天时重新加载路线
function switchDay(i) {
  currentDay.value = i
  dayRoutes.value = []
  if (showMap.value) loadDayRoutes()
}

function onSwiperChange(e) {
  switchDay(e.detail.current)
}

// 展开地图时加载路线
watch(showMap, (v) => {
  if (v) loadDayRoutes()
})

// ====== POI 搜索 ======
async function startSearch(type, dayIndex) {
  searchModal.show = true
  searchModal.type = type
  searchModal.dayIndex = dayIndex
  searchModal.keyword = ''
  searchModal.results = []
  searchModal.removedResults = []
  searchModal.searched = false
  searchModal.pickedResult = null
  searchModal.loading = false
  searchModal.manualName = ''
  searchModal.manualDesc = ''
  searchModal.manualCost = ''
  searchModal.manualDuration = ''
  searchModal.mealTypeIdx = 2
  if (type === 'attr') await loadAttractionSuggestions()
}

function attractionKey(item) {
  return item?.poi_id || item?.id || String(item?.name || '').replace(/\s/g, '')
}

function markDayEdited(dayIndex) {
  if (Number.isInteger(dayIndex) && dayIndex >= 0) editedDays.add(dayIndex)
}

function currentAttractionKeys() {
  return new Set((tripPlan.value?.days || []).flatMap(day => day.attractions || []).map(attractionKey))
}

async function loadAttractionSuggestions() {
  const used = currentAttractionKeys()
  searchModal.removedResults = (tripPlan.value?.removed_attractions || [])
    .filter(item => item?.attraction && !used.has(attractionKey(item.attraction)))
    .map(item => ({
      ...item.attraction,
      lng: item.attraction.location?.longitude,
      lat: item.attraction.location?.latitude,
      restoreReason: item.reason,
      originalDayIndex: item.original_day_index
    }))
  searchModal.loading = true
  try {
    const res = await fetchAttractionSuggestions(tripPlan.value?.city || '')
    searchModal.results = (res?.data || []).filter(item => !used.has(attractionKey(item)))
  } catch (e) {
    searchModal.results = []
  }
  searchModal.loading = false
}

async function doSearch() {
  if (!searchModal.keyword.trim()) {
    uni.showToast({ title: '请输入关键词', icon: 'none' })
    return
  }
  searchModal.loading = true
  searchModal.searched = true
  try {
    const city = tripPlan.value?.city || ''
    const res = searchModal.type === 'attr'
      ? await fetchAttractionSuggestions(city, searchModal.keyword.trim())
      : await searchPOI(city, searchModal.keyword.trim())
    if (res?.success && res.data?.length) {
      const used = currentAttractionKeys()
      searchModal.results = res.data.filter(item => !used.has(attractionKey(item)))
    } else {
      searchModal.results = []
    }
  } catch (e) {
    searchModal.results = []
  }
  searchModal.loading = false
}

async function pickResult(r) {
  searchModal.pickedResult = r
  searchModal.manualName = r.name || ''
  searchModal.manualDesc = r.address || ''
  searchModal.manualDuration = r.visit_duration ? String(r.visit_duration) : ''
  // 基础数据填充
  searchModal.manualCost = ''
  searchModal.rating = ''
  searchModal.opentime = ''
  if (r.restoreReason) return
  // 异步查详情（评分/人均/营业时间）
  try {
    const city = tripPlan.value?.city || ''
    const detail = await searchPOIDetail(city, r.name)
    if (detail?.success && detail.data) {
      const d = detail.data
      searchModal.pickedResult = { ...r, ...d }
      searchModal.manualCost = d.cost ? String(d.cost) : ''
      searchModal.rating = d.rating || ''
      searchModal.opentime = d.opentime || ''
    }
  } catch (e) { /* 详情失败不影响 */ }
}

function distanceKm(left, right) {
  if (!left || !right) return null
  const lat1 = Number(left.latitude), lon1 = Number(left.longitude)
  const lat2 = Number(right.latitude), lon2 = Number(right.longitude)
  if (![lat1, lon1, lat2, lon2].every(Number.isFinite)) return null
  const rad = value => value * Math.PI / 180
  const dLat = rad(lat2 - lat1), dLon = rad(lon2 - lon1)
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dLon / 2) ** 2
  return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

async function confirmAdd() {
  const name = searchModal.manualName.trim()
  if (!name) {
    uni.showToast({ title: '请输入名称', icon: 'none' })
    return
  }
  const day = tripPlan.value.days[searchModal.dayIndex]
  const r = searchModal.pickedResult

  if (searchModal.type === 'attr') {
    if ((day.attractions || []).some(item => attractionKey(item) === attractionKey(r || { name }))) {
      uni.showToast({ title: '这个景点已经在行程里', icon: 'none' })
      return
    }
    const longitude = Number(r?.location?.longitude ?? r?.lng)
    const latitude = Number(r?.location?.latitude ?? r?.lat)
    const location = Number.isFinite(longitude) && Number.isFinite(latitude)
      ? { longitude, latitude }
      : { longitude: 0, latitude: 0 }
    const anchor = day.attractions?.length
      ? day.attractions[day.attractions.length - 1].location
      : day.hotel?.location
    const distance = distanceKm(anchor, location)
    const warnings = []
    if ((day.attractions || []).length >= 3) warnings.push('当天已有3个景点，加入后可能较赶')
    if (distance !== null && distance >= 12) warnings.push(`距前一站直线约${distance.toFixed(1)}公里，可能需要单独安排半天或改交通方式`)
    if (warnings.length) {
      const answer = await new Promise(resolve => uni.showModal({
        title: '加入前提示',
        content: warnings.join('；') + '。仍要加入吗？',
        confirmText: '仍然加入',
        success: result => resolve(result.confirm),
        fail: () => resolve(false)
      }))
      if (!answer) return
    }
    day.attractions.push({
      name,
      address: searchModal.manualDesc || r?.address || '',
      description: r?.description || '',
      visit_duration: parseInt(searchModal.manualDuration) || 60,
      location,
      ticket_price: parseInt(searchModal.manualCost) || 0,
      rating: r?.rating ? parseFloat(r.rating) : undefined,
      poi_id: r?.poi_id || r?.id || '',
      user_locked: true
    })
    tripPlan.value.removed_attractions = (tripPlan.value.removed_attractions || [])
      .filter(item => attractionKey(item.attraction) !== attractionKey(r || { name }))
  } else if (searchModal.type === 'meal') {
    day.meals.push({
      type: mealTypes[searchModal.mealTypeIdx].value,
      name,
      address: searchModal.manualDesc || r?.address || '',
      description: '',
      estimated_cost: parseInt(searchModal.manualCost) || 0,
      location: r ? { longitude: r.lng, latitude: r.lat } : undefined,
      poi_id: r?.poi_id || r?.id || undefined,
      opening_hours: searchModal.opentime || r?.opentime || '',
      opening_hours_verified: Boolean(searchModal.opentime || r?.opentime)
    })
  } else if (searchModal.type === 'hotel') {
    day.hotel = {
      name,
      address: searchModal.manualDesc || r?.address || '',
      price_range: '',
      rating: r?.rating || '',
      type: '',
      estimated_cost: parseInt(searchModal.manualCost) || 0,
      location: r ? { longitude: r.lng, latitude: r.lat } : undefined
    }
  }

  // Keep explicit transport choices before invalidating the derived timeline.
  rememberTransportChoices(day)
  // 手工编辑后的旧时间轴已失效，先退回按当前数组生成的基础顺序。
  day.timeline = []
  markDayEdited(searchModal.dayIndex)

  searchModal.show = false
  uni.showToast({ title: '添加成功', icon: 'success' })
}

// ====== 初始化 ======
onMounted(() => {
  try {
    const raw = uni.getStorageSync('tripPlan')
    if (raw) tripPlan.value = JSON.parse(raw)
  } catch (e) { console.error('读取失败:', e) }
  const windowInfo = typeof uni.getWindowInfo === 'function'
    ? uni.getWindowInfo()
    : uni.getSystemInfoSync()
  pageHeight.value = `${windowInfo.windowHeight}px`
  statusBarHeight.value = windowInfo.statusBarHeight || 0

  try {
    const capsule = uni.getMenuButtonBoundingClientRect()
    if (capsule?.left) {
      capsuleSafeRight.value = Math.max(0, windowInfo.windowWidth - capsule.left + 8)
      capsuleRowHeight.value = Math.max(40, capsule.bottom - statusBarHeight.value + 6)
    }
  } catch (error) {
    capsuleSafeRight.value = 0
  }
})

function goHome() {
  const returnHome = () => uni.reLaunch({ url: '/pages/index/index' })
  if (!editMode.value) {
    returnHome()
    return
  }
  uni.showModal({
    title: '修改尚未保存',
    content: '返回首页将放弃本次未完成的修改。',
    confirmText: '返回首页',
    cancelText: '继续编辑',
    success: ({ confirm }) => {
      if (confirm) returnHome()
    }
  })
}
function mealIcon(type) { return mealTypes.find(t => t.value === type)?.icon || '🍴' }
function mealTypeName(type) { return mealTypes.find(t => t.value === type)?.label || '用餐' }

function hubSubtitle(item, index) {
  if (item?.hub_role === 'intercity_departure') {
    return `进站及候车约${Number(item.duration_minutes || 0)}分钟`
  }
  if (item?.hub_role === 'intercity_arrival') {
    return `到站后出站及取行李约${Number(item.duration_minutes || 0)}分钟`
  }
  if (item?.hub_role === 'trip_arrival') {
    return item?.start_time
      ? '抵达后预留出站和取行李时间'
      : '已选择抵达枢纽，抵达时间待填写'
  }
  if (item?.hub_role === 'trip_departure') {
    return item?.start_time
      ? '已预留候车或值机时间'
      : '已选择离开枢纽，离开时间待填写'
  }
  if (index === 0) {
    return '抵达后预留出站和取行李时间'
  }
  return '已预留候车或值机时间'
}

function transportIcon(mode) {
  if (String(mode || '').includes('步行')) return '🚶'
  if (String(mode || '').includes('高铁')) return '🚄'
  if (String(mode || '').includes('火车')) return '🚆'
  if (String(mode || '').includes('飞机')) return '✈️'
  if (String(mode || '').includes('公共')) return '🚌'
  if (String(mode || '').includes('自驾')) return '🚗'
  if (String(mode || '').includes('打车')) return '🚕'
  return '↕'
}

function transportAdvice(item) {
  if (item?.is_intercity) {
    if (String(item.transport_mode || '').includes('参考')) {
      return '铁路段来自高德班次参考，具体车次和发到站时间请在12306核对'
    }
    return item.source === 'amap'
      ? '高德跨城综合路线参考，未取得可拆分的铁路明细'
      : '跨城时间为保守估算，请在12306核对具体车次'
  }
  const options = item?.alternatives || []
  if (!options.length) return ''
  const fastest = Math.min(...options.map(option => Number(option.duration_minutes) || 9999))
  const saved = Number(item.duration_minutes || 0) - fastest
  if (saved >= 30) return `有其他方式可节省约${saved}分钟`
  if (Number(item.duration_minutes || 0) >= 90) return '这段路耗时较长，可比较其他方式'
  return ''
}

function openTransportPicker(dayIndex, itemIndex, item) {
  if (!item?.alternatives?.length) return
  transportModal.dayIndex = dayIndex
  transportModal.itemIndex = itemIndex
  transportModal.item = item
  transportModal.show = true
}

function isFastestOption(option) {
  const options = transportModal.item?.alternatives || []
  if (!options.length) return false
  const fastest = Math.min(...options.map(item => Number(item.duration_minutes) || 9999))
  return Number(option.duration_minutes) === fastest
}

function clockMinutes(value) {
  if (!value || !String(value).includes(':')) return null
  const [hour, minute] = String(value).split(':').map(Number)
  return Number.isFinite(hour) && Number.isFinite(minute) ? hour * 60 + minute : null
}

function clockText(minutes) {
  const value = Math.max(0, Math.min(23 * 60 + 59, Math.round(minutes)))
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`
}

function rememberTransportChoices(day) {
  const choices = new Map()
  for (const item of [...(day.transport_overrides || []), ...(day.timeline || [])]) {
    if (item.kind !== 'transport' || !item.customized || item.is_intercity
        || !item.from_name || !item.to_name || !item.transport_mode
        || !(Number(item.duration_minutes) > 0)) continue
    choices.set(JSON.stringify([item.from_name, item.to_name]), {
      ...item,
      alternatives: (item.alternatives || []).map(option => ({ ...option }))
    })
  }
  day.transport_overrides = Array.from(choices.values())
}

function reflowTimeline(day, changedIndex) {
  const timeline = day.timeline || []
  const changed = timeline[changedIndex]
  let current = clockMinutes(changed?.start_time)
  if (current === null) return

  changed.end_time = clockText(current + Number(changed.duration_minutes || 0))
  current += Number(changed.duration_minutes || 0)
  const mealTargets = { breakfast: 8 * 60, lunch: 11 * 60 + 30, dinner: 17 * 60 + 30 }

  for (let index = changedIndex + 1; index < timeline.length; index++) {
    const item = timeline[index]
    if (item.kind === 'hub' && item.source === 'user_selected_untimed') continue
    if (item.kind === 'transport') {
      item.start_time = clockText(current)
      current += Number(item.duration_minutes || 0)
      item.end_time = clockText(current)
      continue
    }
    if (item.kind === 'meal' && mealTargets[item.meal_type]) {
      current = Math.max(current, mealTargets[item.meal_type])
    }
    item.start_time = clockText(current)
    if (Number(item.duration_minutes || 0) > 0) {
      current += Number(item.duration_minutes)
      item.end_time = clockText(current)
    } else {
      item.end_time = null
    }
  }
}

async function applyTransportOption(option) {
  const day = tripPlan.value?.days?.[transportModal.dayIndex]
  const item = day?.timeline?.[transportModal.itemIndex]
  if (!day || !item) return
  item.transport_mode = option.mode
  item.duration_minutes = Number(option.duration_minutes)
  item.distance_km = option.distance_km
  item.source = option.source
  item.customized = true
  rememberTransportChoices(day)
  reflowTimeline(day, transportModal.itemIndex)
  transportModal.show = false
  uni.setStorageSync('tripPlan', JSON.stringify(tripPlan.value))

  let checked = false
  let conflict = false
  uni.showLoading({ title: '正在校验时间', mask: true })
  try {
    tripPlan.value = await reflowTripPlan(tripPlan.value, [transportModal.dayIndex])
    checked = true
    conflict = (tripPlan.value.quality_report?.issues || []).some(issue => issue.severity === 'error')
    uni.setStorageSync('tripPlan', JSON.stringify(tripPlan.value))
  } catch (error) {
    // The selected option remains saved locally, but do not claim validation.
    console.warn('交通修改后的时间校验失败', error)
  } finally {
    uni.hideLoading()
  }

  const entryId = uni.getStorageSync('tripPlanEntryId')
  if (entryId) {
    try {
      await saveHistoryEntry(entryId, tripPlan.value)
    } catch (error) { /* 本地已保存，稍后仍可继续同步 */ }
  }
  uni.showToast({
    title: !checked ? '选择已保存，时间校验未完成' : (conflict ? '已调整，请查看时间冲突' : '这一段已调整'),
    icon: checked && !conflict ? 'success' : 'none',
    duration: checked && !conflict ? 1500 : 3000
  })
}

function timelineEntity(day, item) {
  const index = Number(item?.reference_index)
  if (item?.reference_type === 'attraction' && Number.isInteger(index)) return day.attractions?.[index]
  if (item?.reference_type === 'meal' && Number.isInteger(index)) return day.meals?.[index]
  if (item?.reference_type === 'hotel') return day.hotel
  return null
}

function displayTimeline(day) {
  if (Array.isArray(day.timeline) && day.timeline.length) {
    const result = []
    let previousEnd = null
    day.timeline.forEach((rawItem, index) => {
      const item = { ...rawItem, _timeline_index: index }
      const start = clockMinutes(item.start_time)
      if (previousEnd !== null && start !== null && start - previousEnd >= 60) {
        result.push({
          kind: 'note',
          name: '自由活动 / 休息',
          start_time: clockText(previousEnd),
          end_time: clockText(start),
          duration_minutes: start - previousEnd
        })
      }
      result.push(item)
      const end = clockMinutes(item.end_time)
      previousEnd = end !== null ? end : start
    })
    return result
  }

  // 兼容更新前保存的行程：先恢复正确的餐次顺序；重新生成后会带有
  // 后端计算的具体时刻和逐段交通时间。
  const result = []
  const meals = {}
  ;(day.meals || []).forEach((meal, index) => { meals[meal.type] = { meal, index } })
  const attrs = day.attractions || []
  const split = attrs.length ? Math.max(1, Math.ceil(attrs.length / 2)) : 0
  const addMeal = type => {
    const entry = meals[type]
    if (entry) result.push({ kind: 'meal', name: entry.meal.name, meal_type: type, reference_type: 'meal', reference_index: entry.index })
  }
  const addAttrs = (start, end) => attrs.slice(start, end).forEach((attr, offset) => result.push({
    kind: 'attraction', name: attr.name, duration_minutes: attr.visit_duration,
    reference_type: 'attraction', reference_index: start + offset
  }))
  addMeal('breakfast')
  addAttrs(0, split)
  addMeal('lunch')
  addAttrs(split, attrs.length)
  addMeal('dinner')
  return result
}

function removeTimelineItem(day, item, dayIndex) {
  const index = Number(item?.reference_index)
  if (item?.reference_type === 'attraction' && Number.isInteger(index)) {
    const removed = day.attractions.splice(index, 1)[0]
    if (removed) {
      tripPlan.value.removed_attractions = tripPlan.value.removed_attractions || []
      const exists = tripPlan.value.removed_attractions.some(entry => attractionKey(entry.attraction) === attractionKey(removed))
      if (!exists) tripPlan.value.removed_attractions.push({
        attraction: removed,
        original_day_index: day.day_index,
        reason: '你在编辑行程时移出了这个景点'
      })
    }
  }
  if (item?.reference_type === 'meal' && Number.isInteger(index)) day.meals.splice(index, 1)
  rememberTransportChoices(day)
  day.timeline = []
  markDayEdited(dayIndex)
}

function removeHotel(day, dayIndex) {
  rememberTransportChoices(day)
  day.hotel = null
  day.timeline = []
  markDayEdited(dayIndex)
}
function onMarkerTap(e) {} // 由 callout 自动处理

async function toggleEditMode() {
  if (reflowing.value) return
  const shouldSave = editMode.value
  editMode.value = !editMode.value
  if (!shouldSave || !tripPlan.value) return

  if (editedDays.size) {
    reflowing.value = true
    uni.showLoading({ title: '正在重排时间轴', mask: true })
    let reflowSucceeded = false
    try {
      tripPlan.value = await reflowTripPlan(tripPlan.value, Array.from(editedDays))
      editedDays.clear()
      reflowSucceeded = true
    } catch (error) {
      console.warn('行程自动重排失败:', error)
    } finally {
      uni.hideLoading()
      reflowing.value = false
    }
    uni.showToast({
      title: reflowSucceeded ? '时间轴已重新计算' : '修改已保留，时间轴暂未更新',
      icon: reflowSucceeded ? 'success' : 'none',
      duration: reflowSucceeded ? 1500 : 3000
    })
  }

  uni.setStorageSync('tripPlan', JSON.stringify(tripPlan.value))
  const entryId = uni.getStorageSync('tripPlanEntryId')
  if (!entryId) {
    uni.showToast({ title: '修改已保存在本机', icon: 'success' })
    return
  }
  try {
    await saveHistoryEntry(entryId, tripPlan.value)
    uni.showToast({ title: '修改已同步', icon: 'success' })
  } catch (error) {
    uni.showToast({ title: '已保存在本机，账户同步稍后重试', icon: 'none' })
  }
}

function dayIssues(dayIndex) {
  return (tripPlan.value?.quality_report?.issues || [])
    .filter(issue => issue.day_index === dayIndex && issue.severity === 'error')
}

function ticketNeedsConfirmation(attraction) {
  return attraction?.ticket_info?.confidence === 'low'
}

function verifiedFreeTicket(attraction) {
  return Boolean(
    attraction?.ticket_info &&
    Number(attraction.ticket_price) === 0 &&
    attraction.ticket_info.confidence !== 'low'
  )
}
</script>

<style scoped>
.result-page {
  height: 100vh;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: #f0f2f5;
}

/* ====== 顶栏 ====== */
.top-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20rpx 28rpx;
  background: linear-gradient(135deg, #667eea, #764ba2);
  color: #fff;
  z-index: 10;
  flex-shrink: 0;
}

.ai-disclosure {
  margin: 0 32rpx 16rpx;
  padding: 14rpx 18rpx;
  display: flex;
  align-items: center;
  gap: 14rpx;
  flex-shrink: 0;
  border-radius: 18rpx;
  background: #eeeafe;
  color: #625b72;
  font-size: 19rpx;
  line-height: 1.45;
}

.ai-disclosure__tag {
  padding: 5rpx 10rpx;
  flex-shrink: 0;
  border-radius: 10rpx;
  background: #6658e8;
  color: #fff;
  font-weight: 700;
}
.top-city { font-size: 36rpx; font-weight: 700; display: block; }
.top-date { font-size: 24rpx; opacity: 0.85; margin-top: 4rpx; display: block; }
.top-actions { display: flex; gap: 20rpx; }
.top-btn { font-size: 40rpx; padding: 8rpx; }

/* ====== 地图 ====== */
.map-panel {
  height: 360rpx;
  position: relative;
  flex-shrink: 0;
}
.map-view {
  width: 100%;
  height: 100%;
}
.map-legend {
  position: absolute;
  bottom: 8rpx;
  left: 0; right: 0;
  display: flex;
  justify-content: center;
  gap: 24rpx;
  background: rgba(255,255,255,0.9);
  padding: 8rpx 20rpx;
  font-size: 22rpx;
}
.map-legend .legend-item { color: #555; }
.map-legend .legend-loading { color: #667eea; }

/* ====== 天标签栏 ====== */
.day-tabs {
  display: block;
  background: #fff;
  padding: 14rpx 20rpx;
  gap: 14rpx;
  white-space: nowrap;
  flex-shrink: 0;
  box-shadow: 0 2rpx 8rpx rgba(0,0,0,0.05);
}
.day-tabs-inner {
  display: inline-flex;
  min-width: 100%;
  gap: 14rpx;
}
.day-tab {
  flex: 0 0 140rpx;
  text-align: center;
  padding: 14rpx 0;
  border-radius: 14rpx;
  background: #f5f7fa;
  transition: all 0.2s;
}
.day-tab.active {
  background: linear-gradient(135deg, #667eea, #764ba2);
}
.day-tab.active .tab-label,
.day-tab.active .tab-date { color: #fff; }
.tab-label { display: block; font-size: 26rpx; font-weight: 600; color: #555; }
.tab-date  { display: block; font-size: 22rpx; color: #999; margin-top: 4rpx; }

/* ====== Swiper ====== */
.day-swiper { flex: 1; min-height: 0; }
.day-scroll { height: 100%; padding: 20rpx; box-sizing: border-box; }

/* 日描述 */
.day-desc-box {
  background: linear-gradient(135deg, #f0f3ff, #faf5ff);
  border-radius: 16rpx;
  padding: 20rpx 24rpx;
  margin-bottom: 20rpx;
}
.day-desc { font-size: 28rpx; color: #666; line-height: 1.6; }

/* ====== 卡片 ====== */
.card {
  background: #fff;
  border-radius: 16rpx;
  padding: 24rpx;
  margin-bottom: 16rpx;
  box-shadow: 0 2rpx 12rpx rgba(0,0,0,0.04);
}
.card-head { display: flex; align-items: flex-start; gap: 14rpx; }

.hotel-emoji { font-size: 40rpx; flex-shrink: 0; }
.hotel-name { font-size: 30rpx; font-weight: 600; color: #333; display: block; }

.attr-num {
  width: 48rpx; height: 48rpx;
  background: linear-gradient(135deg, #667eea, #764ba2);
  border-radius: 50%;
  color: #fff;
  font-size: 26rpx; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.attr-body { flex: 1; }
.hotel-body { flex: 1; }
.attr-name { font-size: 30rpx; font-weight: 600; color: #333; display: block; }

.sub-text { font-size: 24rpx; color: #888; margin-top: 8rpx; display: block; }
.desc-text { font-size: 26rpx; color: #555; margin-top: 12rpx; line-height: 1.5; }

.tag-row { display: flex; flex-wrap: wrap; gap: 12rpx; margin-top: 12rpx; }
.tag {
  font-size: 22rpx;
  padding: 4rpx 14rpx;
  border-radius: 8rpx;
  background: #f0f3ff;
  color: #667eea;
}
.tag.price { background: #fff8e6; color: #e6a23c; }

.del-btn {
  font-size: 28rpx;
  color: #e74c3c;
  padding: 8rpx 14rpx;
  flex-shrink: 0;
}

/* 餐饮 */
.meal-row { display: flex; align-items: center; gap: 14rpx; }
.meal-icon { font-size: 36rpx; flex-shrink: 0; }
.meal-info { flex: 1; }
.meal-period { display: block; margin-bottom: 4rpx; font-size: 22rpx; color: #ef8d52; font-weight: 700; }
.meal-name { font-size: 28rpx; color: #333; display: block; }
.meal-right { display: flex; align-items: center; gap: 12rpx; }
.meal-cost { font-size: 26rpx; color: #e6a23c; font-weight: 500; }

/* 单日真实时间轴 */
.timeline-list { width: 100%; }
.timeline-row {
  display: flex;
  align-items: stretch;
  min-height: 96rpx;
}
.timeline-time {
  width: 86rpx;
  flex-shrink: 0;
  padding-top: 24rpx;
  color: #2f2940;
  font-size: 24rpx;
  font-weight: 700;
  text-align: right;
}
.timeline-time.muted { color: #9a94a8; font-size: 21rpx; font-weight: 500; }
.timeline-end { display: block; margin-top: 5rpx; color: #aaa4b2; font-size: 19rpx; font-weight: 400; }
.timeline-axis {
  position: relative;
  width: 56rpx;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.timeline-line { width: 3rpx; flex: 1; min-height: 14rpx; background: #ded9f4; }
.event-dot {
  width: 20rpx;
  height: 20rpx;
  flex-shrink: 0;
  border: 6rpx solid #f8f7fc;
  border-radius: 50%;
  background: #6658e8;
  box-shadow: 0 0 0 3rpx #c9c1fb;
}
.event-dot--meal { background: #f2a35d; box-shadow: 0 0 0 3rpx #f9d7b5; }
.event-dot--hotel { background: #57a77b; box-shadow: 0 0 0 3rpx #bce4cd; }
.transport-dot {
  width: 42rpx;
  height: 42rpx;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: #eeeafe;
  font-size: 22rpx;
}
.timeline-card {
  flex: 1;
  min-width: 0;
  margin-bottom: 18rpx;
}
.hotel-stop { background: #f2fbf5; }
.transport-row { min-height: 90rpx; }
.transport-info {
  flex: 1;
  min-width: 0;
  align-self: center;
  margin: 10rpx 0;
  padding: 12rpx 18rpx;
  border-radius: 16rpx;
  background: #f0eefb;
}
.transport-title-row { display: flex; align-items: center; justify-content: space-between; gap: 16rpx; }
.transport-main { display: block; color: #6658e8; font-size: 24rpx; font-weight: 700; }
.transport-change { flex-shrink: 0; color: #6658e8; font-size: 22rpx; font-weight: 700; }
.transport-sub {
  display: block;
  margin-top: 4rpx;
  overflow: hidden;
  color: #8b849b;
  font-size: 20rpx;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.transport-warning { display: block; margin-top: 7rpx; color: #d47b35; font-size: 21rpx; }
.transport-customized { display: block; margin-top: 7rpx; color: #3f9669; font-size: 21rpx; }

/* 单段交通选择 */
.transport-modal { max-height: 78vh; }
.transport-route-name {
  display: block;
  overflow: hidden;
  color: #40394f;
  font-size: 27rpx;
  font-weight: 700;
  text-align: center;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.transport-default-tip { display: block; margin: 10rpx 0 24rpx; color: #938ca1; font-size: 22rpx; text-align: center; }
.transport-option {
  display: flex;
  align-items: center;
  gap: 18rpx;
  margin-bottom: 14rpx;
  padding: 22rpx;
  border: 2rpx solid #ece9f4;
  border-radius: 18rpx;
  background: #faf9fd;
}
.transport-option.selected { border-color: #6658e8; background: #f0edff; }
.transport-option-icon { width: 46rpx; font-size: 34rpx; text-align: center; }
.transport-option-body { flex: 1; min-width: 0; }
.transport-option-title { display: flex; align-items: center; gap: 12rpx; color: #302a40; font-size: 27rpx; font-weight: 700; }
.fastest-tag { padding: 3rpx 10rpx; border-radius: 10rpx; background: #dcf3e6; color: #39865e; font-size: 19rpx; font-weight: 600; }
.transport-option-distance { display: block; margin-top: 5rpx; color: #96909f; font-size: 21rpx; }
.transport-option-time { flex-shrink: 0; color: #6658e8; font-size: 25rpx; font-weight: 700; }
.transport-close { width: 100%; margin-top: 22rpx; }

/* 添加 */
.add-card {
  border: 2rpx dashed #ccc;
  border-radius: 16rpx;
  padding: 24rpx;
  text-align: center;
  margin-bottom: 16rpx;
  color: #999;
  font-size: 26rpx;
  transition: all 0.2s;
}

.day-warning-box {
  margin: 0 24rpx 18rpx;
  padding: 18rpx 22rpx;
  border-radius: 16rpx;
  background: #fff2e8;
  border: 1rpx solid #ffd4b8;
}
.quality-banner {
  margin: 14rpx 22rpx 0;
  padding: 16rpx 20rpx;
  border-radius: 15rpx;
  background: #fff4e8;
  border: 1rpx solid #ffd8b5;
}
.quality-banner__title { display: block; color: #ad561d; font-size: 24rpx; font-weight: 700; }
.quality-banner__copy { display: block; margin-top: 4rpx; color: #8a654c; font-size: 21rpx; line-height: 1.45; }
.day-warning-title {
  display: block;
  color: #b95718;
  font-size: 25rpx;
  font-weight: 700;
  margin-bottom: 6rpx;
}
.day-warning-text {
  display: block;
  color: #8b5a3c;
  font-size: 22rpx;
  line-height: 1.55;
}
.tag.pending { background: #fff2df; color: #a76518; }
.free-time-card { background: #f8f7fb; border-style: dashed; }
.free-time-icon { width: 48rpx; font-size: 30rpx; text-align: center; }

/* 交通住宿 */
.meta-bar {
  display: flex; gap: 24rpx;
  padding: 20rpx 28rpx;
  background: #fff;
  border-radius: 16rpx;
  font-size: 24rpx; color: #888;
}

/* ====== 弹窗通用 ====== */
.modal-mask {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.5);
  display: flex; align-items: center; justify-content: center;
  z-index: 100;
}
.modal-box {
  width: 86%;
  max-height: 80vh;
  overflow-y: auto;
  background: #fff;
  border-radius: 24rpx;
  padding: 40rpx 30rpx;
}
.modal-title { font-size: 34rpx; font-weight: 700; text-align: center; display: block; margin-bottom: 24rpx; }
.modal-input {
  width: 100%;
  height: 80rpx;
  border: 2rpx solid #e8e8e8;
  border-radius: 14rpx;
  padding: 0 20rpx;
  font-size: 28rpx;
  margin-bottom: 16rpx;
  box-sizing: border-box;
  background: #fff;
}
.modal-input.half { width: calc(50% - 8rpx); display: inline-block; }
.picker-like {
  display: inline-flex;
  align-items: center;
  color: #333;
}
.manual-row { display: flex; gap: 16rpx; }
.modal-btns { display: flex; gap: 20rpx; margin-top: 24rpx; }
.btn-cancel {
  flex: 1; height: 80rpx; line-height: 80rpx;
  border-radius: 40rpx; border: 2rpx solid #e8e8e8;
  background: #fff; font-size: 28rpx; color: #666;
}
.btn-confirm {
  flex: 1; height: 80rpx; line-height: 80rpx;
  border-radius: 40rpx; border: none;
  background: linear-gradient(135deg, #667eea, #764ba2);
  font-size: 28rpx; color: #fff;
}

/* ====== POI 搜索 ====== */
.search-row { display: flex; gap: 16rpx; margin-bottom: 20rpx; }
.search-input {
  flex: 1; height: 80rpx;
  border: 2rpx solid #667eea;
  border-radius: 14rpx;
  padding: 0 20rpx;
  font-size: 28rpx;
  background: #fafbff;
}
.search-btn {
  height: 80rpx; line-height: 80rpx;
  padding: 0 30rpx;
  background: linear-gradient(135deg, #667eea, #764ba2);
  color: #fff;
  font-size: 26rpx;
  border-radius: 14rpx;
  border: none;
}
.search-results { max-height: 300rpx; margin-bottom: 16rpx; }
.search-item {
  padding: 20rpx;
  border: 2rpx solid #e8e8e8;
  border-radius: 12rpx;
  margin-bottom: 12rpx;
  background: #fafbff;
  transition: all 0.2s;
}
.search-item.picked { border-color: #667eea; background: #f0f3ff; }
.recommend-section { margin-bottom: 18rpx; }
.recommend-heading { margin: 8rpx 0 12rpx; }
.recommend-title { display: block; font-size: 27rpx; font-weight: 700; color: #333; }
.recommend-tip { display: block; margin-top: 4rpx; font-size: 22rpx; color: #999; }
.restore-results { max-height: 220rpx; margin-top: 12rpx; }
.restore-item { background: #fffaf0; border-color: #f1d7a0; }
.sr-reason { display: block; margin-top: 6rpx; font-size: 22rpx; color: #b27722; }
.sr-name { font-size: 28rpx; font-weight: 600; color: #333; display: block; }
.sr-addr { font-size: 24rpx; color: #888; margin-top: 6rpx; display: block; }
.sr-meta { display: flex; gap: 20rpx; margin-top: 8rpx; font-size: 22rpx; color: #888; }
.no-result { text-align: center; color: #aaa; font-size: 26rpx; padding: 30rpx; display: block; }

.manual-add { border-top: 2rpx solid #f0f0f0; padding-top: 20rpx; }
.manual-label { font-size: 26rpx; color: #888; margin-bottom: 12rpx; display: block; }

/* ====== 预算弹窗 ====== */
.budget-grid { display: flex; flex-wrap: wrap; gap: 14rpx; margin-bottom: 24rpx; }
.b-item {
  flex: 0 0 calc(50% - 7rpx);
  background: #f5f7fa;
  border-radius: 12rpx;
  padding: 24rpx;
  text-align: center;
  box-sizing: border-box;
}
.b-label { display: block; font-size: 24rpx; color: #888; margin-bottom: 8rpx; }
.b-val { display: block; font-size: 36rpx; font-weight: 700; color: #667eea; }
.budget-total {
  display: flex; justify-content: space-between;
  padding-top: 20rpx;
  border-top: 2rpx solid #f0f0f0;
  font-size: 28rpx;
  margin-bottom: 24rpx;
}
.total-num { font-size: 44rpx; font-weight: 800; color: #764ba2; }

/* 天气 */
.weather-item {
  padding: 16rpx 0;
  border-bottom: 2rpx solid #f5f5f5;
  font-size: 26rpx; color: #555;
}
.w-date { font-weight: 600; color: #333; display: block; margin-bottom: 6rpx; }
.sub-text { font-size: 24rpx; color: #888; margin-top: 8rpx; display: block; text-align: center; }

/* 空状态 */
.empty-state {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  height: 100vh;
}
.empty-icon { font-size: 160rpx; margin-bottom: 30rpx; }
.empty-text { font-size: 30rpx; color: #888; margin-bottom: 40rpx; }

/* 通用 */
.btn-primary {
  width: 70%;
  height: 88rpx;
  line-height: 88rpx;
  background: linear-gradient(135deg, #667eea, #764ba2);
  color: #fff;
  font-size: 30rpx;
  font-weight: 600;
  border-radius: 44rpx;
  border: none;
}

/* ====== Figma 版视觉覆盖 ====== */
.result-page {
  background: #f8f7fc;
}

.top-bar {
  display: block;
  padding: 0 32rpx 20rpx;
  background: #f8f7fc;
  color: #252038;
}

.top-heading {
  display: flex;
  align-items: center;
  width: 100%;
}

.home-entry {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  margin-right: 18rpx;
  padding: 8rpx 14rpx 8rpx 10rpx;
  border-radius: 24rpx;
  background: #eeeafe;
  color: #6658e8;
}

.home-entry__arrow {
  font-size: 34rpx;
  line-height: 1;
}

.home-entry__label {
  margin-left: 2rpx;
  font-size: 22rpx;
  font-weight: 700;
}

.top-left {
  flex: 1;
  min-width: 0;
}

.top-city {
  font-size: 38rpx;
  font-weight: 800;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.top-date {
  color: #7d768f;
  opacity: 1;
}

.top-actions {
  width: 100%;
  margin-top: 14rpx;
  gap: 10rpx;
}

.top-btn {
  display: block;
  flex: 1;
  padding: 10rpx 16rpx;
  border-radius: 24rpx;
  background: #fff;
  color: #7d768f;
  font-size: 22rpx;
}

.top-btn.active {
  background: #eeeafe;
  color: #6658e8;
  font-weight: 700;
}

.map-panel {
  height: 320rpx;
  margin: 0 32rpx 22rpx;
  overflow: hidden;
  border-radius: 36rpx;
  background: #e9eef3;
}

.map-legend {
  left: 20rpx;
  right: auto;
  bottom: 16rpx;
  padding: 10rpx 18rpx;
  gap: 14rpx;
  border-radius: 22rpx;
  font-size: 19rpx;
}

.day-tabs {
  display: block;
  margin: 0 32rpx 18rpx;
  padding: 8rpx;
  white-space: normal;
  border: 2rpx solid #e9e6f2;
  border-radius: 28rpx;
  box-shadow: none;
}

.day-tabs-inner {
  display: inline-flex;
  min-width: 100%;
  gap: 8rpx;
}

.day-tab {
  width: 116rpx;
  flex: 0 0 116rpx;
  padding: 12rpx 0;
  border-radius: 22rpx;
  background: #fff;
}

.day-tab.active {
  background: #6658e8;
}

.tab-label {
  color: #797287;
  font-size: 23rpx;
}

.tab-date {
  font-size: 19rpx;
}

.day-swiper {
  min-height: 0;
  flex: 1;
}

.day-scroll {
  padding: 0 32rpx calc(60rpx + env(safe-area-inset-bottom));
}

@media screen and (max-height: 700px) {
  .map-panel {
    height: 270rpx;
  }

  .top-actions {
    margin-top: 8rpx;
  }
}

.day-desc-box {
  padding: 22rpx 26rpx;
  border-radius: 28rpx;
  background: #f2eeff;
}

.day-desc {
  color: #4f42b1;
  font-size: 25rpx;
  font-weight: 600;
}

.card {
  padding: 26rpx;
  border: 2rpx solid #e9e6f2;
  border-radius: 30rpx;
  box-shadow: none;
}

.attr-num {
  width: 56rpx;
  height: 56rpx;
  background: #6658e8;
}

.attr-name,
.hotel-name {
  color: #302a45;
}

.card .sub-text,
.card .desc-text {
  color: #7d768f;
  text-align: left;
}

.card .sub-text.dish {
  color: #6658e8;
  font-weight: 600;
}

.tag {
  border-radius: 18rpx;
  background: #f0edff;
  color: #6658e8;
}

.tag.verified {
  background: #eaf8f0;
  color: #27875f;
}

.tag.source {
  background: #f3f1f8;
  color: #716a80;
}

.add-card {
  border-color: #cfc8e5;
  border-radius: 28rpx;
  background: #faf9fd;
  color: #6658e8;
}

.meta-bar {
  border: 2rpx solid #e9e6f2;
  border-radius: 28rpx;
}

.modal-box {
  border-radius: 40rpx;
}

.btn-confirm,
.search-btn,
.btn-primary {
  background: #6658e8;
}

/* 暖纸张手绘行程详情 */
.result-page {
  background: var(--background);
}

.top-bar {
  padding: 18rpx 40rpx 16rpx;
  display: block;
  background: var(--background);
  color: var(--text);
}

.top-heading {
  display: flex;
  align-items: center;
  gap: 22rpx;
}

.home-entry {
  width: 72rpx;
  height: 72rpx;
  flex: 0 0 72rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2rpx solid var(--border);
  border-radius: 50%;
  background: var(--surface);
}

.home-entry__arrow {
  color: var(--brand);
  font-size: 38rpx;
  font-weight: 700;
}

.top-left {
  min-width: 0;
  flex: 1;
}

.top-city {
  color: var(--text);
  font-size: 40rpx;
}

.top-date {
  color: var(--muted);
  font-size: 23rpx;
  opacity: 1;
}

.ai-disclosure {
  margin: 0 40rpx 18rpx;
  padding: 12rpx 16rpx;
  border-radius: 18rpx;
  background: var(--brand-soft);
  color: var(--muted);
}

.ai-disclosure__tag {
  background: var(--brand);
}

.map-panel {
  height: 264rpx;
  margin: 0 40rpx 22rpx;
  overflow: hidden;
  border-radius: 40rpx;
  background: var(--brand-soft);
}

.map-view {
  border-radius: 40rpx;
}

.map-legend {
  left: 18rpx;
  right: 18rpx;
  bottom: 14rpx;
  padding: 8rpx 14rpx;
  border-radius: 20rpx;
  background: rgba(255, 254, 250, 0.9);
  font-size: 19rpx;
}

.map-legend .legend-loading {
  color: var(--brand);
}

.day-tabs {
  margin: 0 40rpx 22rpx;
  padding: 10rpx;
  border: 2rpx solid var(--border);
  border-radius: 30rpx;
  background: var(--surface);
  box-shadow: none;
}

.day-tabs-inner {
  gap: 4rpx;
}

.day-tab {
  flex: 0 0 126rpx;
  padding: 13rpx 0;
  border-radius: 24rpx;
  background: transparent;
}

.day-tab.active {
  background: var(--brand);
}

.tab-label {
  color: var(--muted);
}

.tab-date {
  color: var(--muted);
}

.day-swiper {
  margin-bottom: calc(100rpx + env(safe-area-inset-bottom));
}

.day-scroll {
  padding: 0 40rpx 40rpx;
}

.day-desc-box {
  margin-bottom: 26rpx;
  padding: 26rpx 30rpx;
  border-radius: 30rpx;
  background: var(--accent-soft);
}

.day-desc {
  color: var(--text);
  font-size: 25rpx;
}

.card {
  margin-bottom: 22rpx;
  padding: 28rpx;
  border: 2rpx solid var(--border);
  border-radius: 34rpx;
  background: var(--surface);
  box-shadow: none;
}

.hotel-card {
  background: var(--surface);
}

.hotel-emoji {
  width: 62rpx;
  height: 62rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 20rpx;
  background: var(--brand-soft);
  color: var(--brand);
  font-size: 32rpx;
}

.hotel-name,
.attr-name,
.meal-name {
  color: var(--text);
  font-weight: 700;
}

.schedule-heading {
  margin: 32rpx 6rpx 22rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--text);
  font-size: 31rpx;
  font-weight: 800;
}

.schedule-edit {
  color: var(--brand);
  font-size: 25rpx;
}

.attr-num {
  width: 58rpx;
  height: 58rpx;
  background: var(--accent);
}

.card .sub-text,
.card .desc-text {
  color: var(--muted);
}

.card .sub-text.dish {
  color: var(--success);
}

.tag {
  background: var(--accent-soft);
  color: var(--text);
}

.tag.price {
  background: var(--accent-soft);
  color: var(--warning);
}

.tag.verified,
.tag.source {
  background: var(--success-soft);
  color: var(--success);
}

.add-card {
  border-color: var(--border);
  background: var(--surface);
  color: var(--brand);
}

.meta-bar {
  border-color: var(--border);
  border-radius: 28rpx;
  background: var(--surface);
  color: var(--muted);
}

.detail-bottom-nav {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 30;
  height: calc(100rpx + env(safe-area-inset-bottom));
  padding-bottom: env(safe-area-inset-bottom);
  display: flex;
  align-items: center;
  border-top: 2rpx solid var(--border);
  background: rgba(255, 254, 250, 0.98);
}

.detail-nav-item {
  position: relative;
  width: 33.333%;
  color: var(--muted);
  font-size: 25rpx;
  text-align: center;
}

.detail-nav-item.active {
  color: var(--brand);
  font-weight: 700;
}

.detail-nav-dot {
  position: absolute;
  left: 50%;
  top: -24rpx;
  width: 12rpx;
  height: 12rpx;
  border-radius: 50%;
  background: var(--accent);
  transform: translateX(-50%);
}

.modal-box {
  border: 2rpx solid var(--border);
  background: var(--surface);
}

.search-input,
.modal-input,
.search-item {
  border-color: var(--border);
  background: var(--background);
}

.btn-confirm,
.search-btn,
.btn-primary {
  background: var(--brand);
}
</style>
