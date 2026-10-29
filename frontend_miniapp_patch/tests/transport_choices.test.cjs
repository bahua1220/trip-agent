// Execute the actual result-page functions with a stubbed uni runtime.
const { readFileSync } = require('node:fs')
const { join } = require('node:path')
const vm = require('node:vm')
const assert = require('node:assert/strict')
const { test } = require('node:test')

const page = readFileSync(join(__dirname, '../pages/result/result.vue'), 'utf8')
const functions = ['clockMinutes', 'clockText', 'rememberTransportChoices', 'reflowTimeline', 'applyTransportOption']
const source = functions.map(name => {
  const match = page.match(new RegExp(`^(?:async )?function ${name}\\([^]*?^}`, 'm'))
  assert.ok(match, `结果页缺少函数 ${name}`)
  return match[0]
}).join('\n')

function leg(changes = {}) {
  return {
    kind: 'transport', from_name: '早餐店', to_name: '第一景点',
    start_time: '09:00', end_time: '09:30', duration_minutes: 30,
    transport_mode: '公共交通', alternatives: [], ...changes
  }
}

function runtime() {
  const state = { saved: new Map(), toasts: [], calls: [] }
  const context = vm.createContext({
    console: { warn() {} },
    tripPlan: { value: { days: [{ timeline: [leg()] }] } },
    transportModal: { dayIndex: 0, itemIndex: 0, show: true },
    uni: {
      setStorageSync(key, value) { state.saved.set(key, value) },
      getStorageSync() { return '' }, showLoading() {}, hideLoading() {},
      showToast(value) { state.toasts.push(value) }
    },
    async reflowTripPlan(plan, indices) {
      state.calls.push({ plan: JSON.parse(JSON.stringify(plan)), indices: [...indices] })
      return JSON.parse(JSON.stringify(plan))
    },
    async saveHistoryEntry() {}
  })
  vm.runInContext(source, context)
  return { context, state }
}

test('交通选择独立保存，清空时间轴后仍在，重复编辑使用最新选择', () => {
  const { context } = runtime()
  const day = context.tripPlan.value.days[0]
  day.timeline[0] = leg({ customized: true, transport_mode: '步行', duration_minutes: 15 })
  context.rememberTransportChoices(day)
  day.timeline = []
  context.rememberTransportChoices(day)
  assert.equal(day.transport_overrides[0].transport_mode, '步行')
  day.timeline = [leg({ customized: true, transport_mode: '骑行', duration_minutes: 8 })]
  context.rememberTransportChoices(day)
  assert.equal(day.transport_overrides.length, 1)
  assert.equal(day.transport_overrides[0].transport_mode, '骑行')
})

test('前端预览不把未填写时间的离开枢纽变成确定时刻', () => {
  const { context } = runtime()
  const day = { timeline: [leg({ duration_minutes: 15 }), {
    kind: 'hub', name: '南京南站', hub_role: 'trip_departure',
    source: 'user_selected_untimed', start_time: null, end_time: null, duration_minutes: 0
  }] }
  context.reflowTimeline(day, 0)
  assert.equal(day.timeline[1].start_time, null)
  assert.equal(day.timeline[1].end_time, null)
})

test('切换交通调用后端校验，显示后端冲突并保存后端时间轴', async () => {
  const { context, state } = runtime()
  context.reflowTripPlan = async (plan, indices) => {
    state.calls.push({ plan: JSON.parse(JSON.stringify(plan)), indices: [...indices] })
    return { ...plan, quality_report: { passed: false, issues: [
      { code: 'attraction_opening_conflict', severity: 'error', message: '闭馆前无法完成游览' }
    ] } }
  }
  await context.applyTransportOption({ mode: '步行', duration_minutes: 15, source: 'amap' })
  assert.equal(state.calls.length, 1)
  assert.deepEqual(state.calls[0].indices, [0])
  assert.equal(state.calls[0].plan.days[0].transport_overrides[0].transport_mode, '步行')
  assert.equal(context.tripPlan.value.quality_report.passed, false)
  assert.match(state.toasts.at(-1).title, /冲突/)
  assert.equal(state.toasts.at(-1).icon, 'none')
  assert.equal(JSON.parse(state.saved.get('tripPlan')).quality_report.passed, false)
})

test('后端校验失败时保留选择，但不宣称校验成功', async () => {
  const { context, state } = runtime()
  context.reflowTripPlan = async () => { throw new Error('网络不可用') }
  await context.applyTransportOption({ mode: '步行', duration_minutes: 15, source: 'amap' })
  const saved = JSON.parse(state.saved.get('tripPlan'))
  assert.equal(saved.days[0].transport_overrides[0].transport_mode, '步行')
  assert.match(state.toasts.at(-1).title, /校验未完成/)
  assert.equal(state.toasts.at(-1).icon, 'none')
})
