// Run the generating-page state logic itself, without Vue, network or WeChat.
const { readFileSync, existsSync } = require('node:fs')
const { join } = require('node:path')
const vm = require('node:vm')
const assert = require('node:assert/strict')
const { test } = require('node:test')

const paths = [join(__dirname, '../pages/generating/generating.vue')]
const actual = join(process.env.TRIP_MINIAPP_SOURCE || join(__dirname, '../../miniapp'), 'pages/generating/generating.vue')
if (existsSync(actual)) paths.push(actual)

function runtime(path) {
  const page = readFileSync(path, 'utf8')
  const script = page.match(/<script setup>([^]*?)<\/script>/)[1]
    .replace(/^import[^]*?from\s+['"][^'"]+['"]\s*$/gm, '')
  const saved = new Map()
  const context = vm.createContext({
    console, JSON, Date, Math, setTimeout,
    ref: value => ({ value }), computed: getter => Object.defineProperty({}, 'value', { get: getter }),
    onLoad() {}, onUnload() {},
    uni: { setStorageSync: (key, value) => saved.set(key, value), getStorageSync: key => saved.get(key),
      removeStorageSync: key => saved.delete(key) },
    getSession: () => ({ user: { id: 'offline-user' } }),
    getTripPlanTask: async () => ({ status: 'failed', progress: 100, message: '行程未通过检查', error: '第三天景点不足' }),
    createTripPlanTask: async () => { throw new Error('本测试不创建真实任务') },
    saveHistoryEntry: async () => {},
  })
  vm.runInContext(script, context)
  return { page, context, saved, run: source => vm.runInContext(source, context) }
}

for (const [index, path] of paths.entries()) {
  const prefix = index ? '实际uni-app源码' : '补丁源码'

  test(`${prefix}：failed+100不会显示成功或等待自动继续`, async () => {
    const state = runtime(path)
    state.run("taskId.value='test-task'; status.value='running'; progress.value=97; requestData.value={city:'南京'}")
    await state.context.pollTask()
    assert.equal(state.run('progressLabel.value'), '未完成')
    assert.equal(state.run('completed.value'), false)
    assert.equal(state.context.stepState(3), 'error')
    assert.equal(state.context.stepStatusLabel(3), '未通过')
    assert.equal(state.context.stepState(4), 'pending')
    assert.equal(state.context.stepStatusLabel(4), '未执行')
    assert.doesNotMatch(state.run('pageTitle.value'), /正在规划/)
    assert.match(state.run('recoveryCopy.value'), /不会自动继续/)
    assert.doesNotMatch(state.run('recoveryTitle.value'), /安全保存|正在恢复/)
    assert.equal(JSON.parse(state.saved.get('tripGenerationTask')).status, 'failed')
    assert.equal(state.saved.has('tripPlan'), false)
  })

  test(`${prefix}：真正断线的运行中任务仍提示恢复连接`, () => {
    const state = runtime(path)
    state.run("taskId.value='test-task'; status.value='running'; progress.value=60; recovering.value=true")
    assert.match(state.run('recoveryTitle.value'), /恢复任务连接/)
    assert.match(state.run('recoveryCopy.value'), /自动重新连接/)
    assert.equal(state.context.stepState(2), 'current')
  })

  test(`${prefix}：成功结果继续显示100%并允许查看`, async () => {
    const state = runtime(path)
    state.run("taskId.value='test-task'; requestData.value={city:'南京'}")
    await state.context.finishTask({ city: '南京', days: [] })
    assert.equal(state.run('progressLabel.value'), '100%')
    assert.equal(state.run('completed.value'), true)
    for (let i = 0; i < 5; i++) assert.equal(state.context.stepState(i), 'done')
    assert.equal(JSON.parse(state.saved.get('tripPlan')).city, '南京')
  })

  test(`${prefix}：创建失败不承诺已有后台任务`, () => {
    const state = runtime(path)
    state.context.markFailed('请求未通过校验')
    assert.match(state.run('recoveryTitle.value'), /未创建/)
    assert.equal(state.context.stepState(0), 'error')
    assert.equal(state.context.stepState(1), 'pending')
    assert.match(state.page, /progressLabel/)
  })
}
