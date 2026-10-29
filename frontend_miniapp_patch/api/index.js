// 后端已部署到正式 HTTPS 域名。开发版、体验版和正式版统一访问该地址，
// 避免在应用启动阶段调用 getAccountInfoSync 进行环境判断导致兼容性白屏。
export const API_BASE_URL = 'https://api.example.com'
export const LEGAL_CONSENT_VERSION = '2026-08-05'

const AUTH_KEY = 'tripAuth'

export function getSession() {
  try {
    const raw = uni.getStorageSync(AUTH_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function setSession(session) {
  uni.setStorageSync(AUTH_KEY, JSON.stringify(session))
}

export function clearSession(options = {}) {
  uni.removeStorageSync(AUTH_KEY)
  if (options.clearTripData) {
    ;[
      'tripPlan',
      'tripPlanEntryId',
      'tripDraft',
      'tripDraftActive',
      'postLoginRoute'
    ].forEach(key => uni.removeStorageSync(key))
  }
}

function responseErrorMessage(data, statusCode) {
  const detail = data?.detail ?? data?.message
  if (Array.isArray(detail)) {
    const messages = detail
      .map(item => {
        if (typeof item === 'string') return item
        if (!item || typeof item !== 'object') return ''
        const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : ''
        return `${field ? `${field}：` : ''}${item.msg || item.message || ''}`
      })
      .filter(Boolean)
    if (messages.length) return messages.join('；')
  }
  if (detail && typeof detail === 'object') {
    return detail.msg || detail.message || JSON.stringify(detail)
  }
  return detail || `请求失败(${statusCode})`
}

function request(url, options = {}) {
  const session = getSession()
  const header = { 'Content-Type': 'application/json', ...(options.header || {}) }
  if (session?.access_token) header.Authorization = `Bearer ${session.access_token}`

  return new Promise((resolve, reject) => {
    uni.request({
      url: API_BASE_URL + url,
      method: options.method || 'GET',
      data: options.data || {},
      header,
      timeout: options.timeout || 30000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
          return
        }
        if (res.statusCode === 401) clearSession()
        const message = responseErrorMessage(res.data, res.statusCode)
        const error = new Error(message)
        error.status = res.statusCode
        reject(error)
      },
      fail(err) {
        console.error('网络请求失败:', err)
        reject(new Error('网络连接失败，请检查后端服务和网络地址'))
      }
    })
  })
}

export function login(username, password, consentVersion = null) {
  return request('/api/auth/login', {
    method: 'POST',
    data: { username, password, consent_version: consentVersion }
  }).then(session => {
    setSession(session)
    return session
  })
}

export function register(username, password, consentVersion = null) {
  return request('/api/auth/register', {
    method: 'POST',
    data: { username, password, consent_version: consentVersion }
  }).then(session => {
    setSession(session)
    return session
  })
}

export function wechatLogin(code, consentVersion = null) {
  return request('/api/auth/wechat/login', {
    method: 'POST',
    data: { code, consent_version: consentVersion }
  }).then(session => {
    setSession(session)
    return session
  })
}

export function bindWechat(code) {
  return request('/api/auth/wechat/bind', {
    method: 'POST',
    data: { code }
  }).then(session => {
    setSession(session)
    return session
  })
}

export function mergeWechatAccount(username, password) {
  return request('/api/auth/wechat/merge', {
    method: 'POST',
    data: { username, password }
  }).then(session => {
    setSession(session)
    return session
  })
}

export function fetchCurrentUser() {
  return request('/api/auth/me')
}

export function deleteAccount() {
  return request('/api/auth/account', {
    method: 'DELETE',
    data: { confirm: 'DELETE' }
  })
}

async function getWechatContentCheckCode() {
  return new Promise(resolve => {
    uni.login({
      provider: 'weixin',
      success: result => resolve(result.code || ''),
      fail: () => resolve('')
    })
  })
}

async function withContentCheckCode(data) {
  const code = await getWechatContentCheckCode()
  return code ? { ...data, wechat_content_check_code: code } : data
}

export async function createTripPlanTask(formData) {
  return request('/api/trip/plan/authenticated', {
    method: 'POST',
    data: await withContentCheckCode(formData)
  })
}

export function getTripPlanTask(taskId) {
  return request(`/api/trip/plan/${taskId}`)
}

export function reflowTripPlan(plan, dayIndices = []) {
  return request('/api/trip/plan/reflow', {
    method: 'POST',
    data: { plan, day_indices: dayIndices },
    timeout: 60000
  })
}

export async function recommendRouteOptions(data) {
  return request('/api/trip/route-options', {
    method: 'POST',
    data: await withContentCheckCode(data)
  })
}

export async function validateRouteDraft(data) {
  return request('/api/trip/route-options/validate', {
    method: 'POST',
    data: await withContentCheckCode(data)
  })
}

export function fetchAccountHistory() {
  return request('/api/history')
}

export function saveHistoryEntry(id, plan) {
  return request(`/api/history/${encodeURIComponent(id)}`, {
    method: 'PUT',
    data: { id, plan }
  })
}

export function removeHistoryEntry(id) {
  return request(`/api/history/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function searchPOI(city, keywords) {
  return request('/api/poi/search', { data: { keywords, city } })
}

export function fetchAttractionSuggestions(city, keywords = '') {
  return request('/api/poi/attraction-suggestions', { data: { city, keywords } })
}

export function fetchTransportHubs(city, transport) {
  return request('/api/poi/transport-hubs', { data: { city, transport } })
}

export function searchPOIDetail(city, keywords) {
  return request('/api/poi/search-detail', { data: { keywords, city } })
}

export function fetchTicketInfo(name, city) {
  return request('/api/poi/ticket-info', { data: { name, city } })
}

export function fetchDrivingRoute(origin, destination) {
  return request('/api/map/driving-route', { data: { origin, destination } })
}

export function healthCheck() {
  return request('/health')
}

export default {
  login,
  register,
  wechatLogin,
  bindWechat,
  mergeWechatAccount,
  deleteAccount,
  getSession,
  setSession,
  clearSession,
  createTripPlanTask,
  getTripPlanTask,
  reflowTripPlan,
  recommendRouteOptions,
  validateRouteDraft,
  fetchAccountHistory,
  saveHistoryEntry,
  removeHistoryEntry,
  searchPOI,
  fetchAttractionSuggestions,
  fetchTransportHubs,
  searchPOIDetail,
  fetchTicketInfo,
  fetchDrivingRoute,
  healthCheck
}
