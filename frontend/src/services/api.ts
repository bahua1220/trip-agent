import axios from 'axios'
import type { TripFormData, TripTaskCreatedResponse, TripTaskStatusResponse, POISearchResponse, POIDetailResponse, TicketInfo, RouteDraftRequest, RouteOption, RouteRecommendationRequest } from '@/types'
import type { TripHistoryEntry } from './tripHistory'
import { clearSession, getSession } from './auth'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  // 行程生成已改为后台任务：创建和轮询都应快速返回，避免页面被单次长请求占住。
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
apiClient.interceptors.request.use(
  (config) => {
    const session = getSession()
    if (session) config.headers.Authorization = `Bearer ${session.access_token}`
    console.log('发送请求:', config.method?.toUpperCase(), config.url)
    return config
  },
  (error) => {
    console.error('请求错误:', error)
    return Promise.reject(error)
  }
)

export async function syncHistoryEntry(entry: TripHistoryEntry): Promise<void> {
  if (!getSession()) return
  await apiClient.put(`/api/history/${entry.id}`, { id: entry.id, plan: entry.plan })
}

export async function fetchAccountHistory(): Promise<TripHistoryEntry[]> {
  if (!getSession()) return []
  const response = await apiClient.get<TripHistoryEntry[]>('/api/history')
  return response.data
}

export async function removeAccountHistory(id: string): Promise<void> {
  if (getSession()) await apiClient.delete(`/api/history/${id}`)
}

// 响应拦截器
apiClient.interceptors.response.use(
  (response) => {
    console.log('收到响应:', response.status, response.config.url)
    return response
  },
  (error) => {
    if (error.response?.status === 401 && getSession()) clearSession()
    console.error('响应错误:', error.response?.status, error.message)
    return Promise.reject(error)
  }
)

/**
 * 创建后台旅行规划任务
 */
export async function createTripPlanTask(formData: TripFormData): Promise<TripTaskCreatedResponse> {
  try {
    const response = await apiClient.post<TripTaskCreatedResponse>(
      '/api/trip/plan/authenticated',
      formData
    )
    return response.data
  } catch (error: any) {
    console.error('创建旅行规划任务失败:', error)
    const taskError = new Error(
      error.response?.data?.detail || error.message || '创建旅行规划任务失败'
    ) as Error & { status?: number }
    taskError.status = error.response?.status
    throw taskError
  }
}

/** 查询后台旅行规划任务的真实进度与结果。 */
export async function getTripPlanTask(taskId: string): Promise<TripTaskStatusResponse> {
  try {
    const response = await apiClient.get<TripTaskStatusResponse>(`/api/trip/plan/${taskId}`)
    return response.data
  } catch (error: any) {
    const taskError = new Error(
      error.response?.data?.detail || error.message || '查询旅行规划任务失败'
    ) as Error & { status?: number }
    taskError.status = error.response?.status
    throw taskError
  }
}

/** 根据区域和旅行强度生成可选择的自驾环线。 */
export async function recommendRouteOptions(
  request: RouteRecommendationRequest
): Promise<RouteOption[]> {
  try {
    const response = await apiClient.post<RouteOption[]>('/api/trip/route-options', request)
    return response.data
  } catch (error: any) {
    throw new Error(error.response?.data?.detail || error.message || '生成候选路线失败')
  }
}

/** 重新计算用户编辑后的环线并返回风险提示。 */
export async function validateRouteDraft(
  request: RouteDraftRequest
): Promise<RouteOption> {
  try {
    const response = await apiClient.post<RouteOption>(
      '/api/trip/route-options/validate',
      request
    )
    return response.data
  } catch (error: any) {
    throw new Error(error.response?.data?.detail || error.message || '路线重新校验失败')
  }
}

/**
 * 搜索 POI（用于编辑模式下添加景点）
 */
export async function searchPOI(city: string, keywords: string): Promise<POISearchResponse> {
  try {
    const response = await apiClient.get<POISearchResponse>('/api/poi/search', {
      params: { keywords, city }
    })
    return response.data
  } catch (error: any) {
    console.error('POI搜索失败:', error)
    throw new Error(error.response?.data?.detail || error.message || 'POI搜索失败')
  }
}

/**
 * 搜索 POI 详情（含评分、人均消费，用于编辑模式自动补全酒店/餐厅信息）
 */
export async function searchPOIDetail(city: string, keywords: string): Promise<POIDetailResponse> {
  try {
    const response = await apiClient.get<POIDetailResponse>('/api/poi/search-detail', {
      params: { keywords, city }
    })
    return response.data
  } catch (error: any) {
    console.error('POI详情搜索失败:', error)
    throw new Error(error.response?.data?.detail || error.message || 'POI详情搜索失败')
  }
}

/**
 * 查询景点门票信息（LLM估价，Tavily不可用时走兜底）
 */
export async function fetchTicketInfo(name: string, city: string): Promise<TicketInfo | null> {
  try {
    const response = await apiClient.get('/api/poi/ticket-info', {
      params: { name, city }
    })
    const d = (response.data as any)?.data
    if (d) {
      return {
        price: d.price ?? 0,
        opening_hours: d.opening_hours ?? '',
        purchase_method: d.purchase_method ?? '',
        source: d.source ?? 'llm-estimate',
        source_url: d.source_url ?? '',
        confidence: d.confidence ?? 'low'
      }
    }
    return null
  } catch (error: any) {
    console.error('查询门票信息失败:', error)
    return null
  }
}

/**
 * 获取驾车导航路径（polyline 坐标数组）
 */
export async function fetchDrivingRoute(
  origin: string,
  destination: string
): Promise<[number, number][] | null> {
  try {
    const response = await apiClient.get('/api/map/driving-route', {
      params: { origin, destination }
    })
    const d = (response.data as any)?.data
    if (d?.path && Array.isArray(d.path) && d.path.length > 0) {
      return d.path as [number, number][]
    }
    return null
  } catch (error: any) {
    console.error('获取导航路径失败:', error)
    return null
  }
}

/**
 * 健康检查
 */
export async function healthCheck(): Promise<any> {
  try {
    const response = await apiClient.get('/health')
    return response.data
  } catch (error: any) {
    console.error('健康检查失败:', error)
    throw new Error(error.message || '健康检查失败')
  }
}

export default apiClient

