// 类型定义

export interface Location {
  longitude: number
  latitude: number
}

export interface TicketInfo {
  price: number
  opening_hours: string
  purchase_method: string
  source: string
  source_url?: string
  confidence?: 'medium' | 'low'
}

export interface Attraction {
  name: string
  address: string
  location: Location
  visit_duration: number
  description: string
  category?: string
  rating?: number
  image_url?: string
  ticket_price?: number
  ticket_info?: TicketInfo
}

export interface Meal {
  type: 'breakfast' | 'lunch' | 'dinner' | 'snack'
  name: string
  address?: string
  location?: Location
  description?: string
  estimated_cost?: number
  poi_id?: string
  recommended_dish?: string
  rating?: number
  data_source?: string
}

export interface Hotel {
  name: string
  address: string
  location?: Location
  price_range: string
  rating: string
  distance: string
  type: string
  estimated_cost?: number
}

export interface Budget {
  total_attractions: number
  total_hotels: number
  total_meals: number
  total_transportation: number
  total: number
}

export interface RouteSegment {
  origin: string
  destination: string
  transport_mode: string
  distance_km: number
  duration_minutes: number
  source: 'amap' | 'estimated'
  requires_confirmation: boolean
  exceeds_daily_limit: boolean
}

export interface RouteRecommendationRequest {
  region: string
  travel_days: number
  start_location?: string
  preferences: string[]
  max_drive_hours_per_day: number
}

export interface RouteDraftRequest {
  region: string
  nodes: string[]
  travel_days: number
  max_drive_hours_per_day: number
}

export interface RouteOption {
  id: string
  name: string
  summary: string
  nodes: string[]
  segments: RouteSegment[]
  total_distance_km: number
  total_drive_minutes: number
  max_segment_minutes: number
  fit_score: number
  tags: string[]
  warnings: string[]
}

export interface QualityIssue {
  code: string
  severity: 'error' | 'warning'
  message: string
  day_index?: number
}

export interface QualityReport {
  passed: boolean
  issues: QualityIssue[]
  critic_advice: string
}

export interface DayPlan {
  date: string
  day_index: number
  description: string
  transportation: string
  accommodation: string
  hotel?: Hotel
  attractions: Attraction[]
  meals: Meal[]
  start_location?: string
  end_location?: string
  overnight_city?: string
  route_segment?: RouteSegment
  is_transit_day?: boolean
}

export interface WeatherInfo {
  date: string
  day_weather: string
  night_weather: string
  day_temp: number
  night_temp: number
  wind_direction: string
  wind_power: string
  location_name?: string
}

export interface TripPlan {
  city: string
  start_date: string
  end_date: string
  days: DayPlan[]
  weather_info: WeatherInfo[]
  overall_suggestions: string
  budget?: Budget
  quality_report?: QualityReport
  trip_mode?: 'single_city' | 'multi_city' | 'road_trip_loop'
  route_nodes?: string[]
}

export interface TripFormData {
  city: string
  start_date: string
  end_date: string
  travel_days: number
  transportation: string
  accommodation: string
  preferences: string[]
  free_text_input: string
  arrival_transport?: string
  arrival_time?: string
  departure_time?: string
  trip_mode: 'single_city' | 'multi_city' | 'road_trip_loop'
  destinations: string[]
  start_location?: string
  end_location?: string
  return_to_start: boolean
  intercity_transport: 'auto' | 'high_speed_rail' | 'train' | 'flight' | 'self_drive'
  max_drive_hours_per_day: number
}

export interface TripPlanResponse {
  success: boolean
  message: string
  data?: TripPlan
}

export interface TripTaskCreatedResponse {
  task_id: string
  status: 'queued' | 'running' | 'succeeded' | 'degraded' | 'failed'
  progress: number
  message: string
}

export interface TripTaskStatusResponse extends TripTaskCreatedResponse {
  data?: TripPlan
  error?: string
}

/** POI 搜索结果项 */
export interface POISearchItem {
  id: string
  name: string
  address: string
  typecode: string
  type: string
  lng: number
  lat: number
}

/** POI 详情项（含评分、人均） */
export interface POIDetailItem {
  id: string
  name: string
  address: string
  typecode: string
  type: string
  lng: number
  lat: number
  rating: string    // 评分，如 "4.5"
  cost: string       // 人均消费，如 "80"
  opentime: string   // 营业时间
}

/** POI 搜索响应 */
export interface POISearchResponse {
  success: boolean
  message: string
  data: POISearchItem[]
}

/** POI 详情搜索响应 */
export interface POIDetailResponse {
  success: boolean
  message: string
  data: POIDetailItem | null
}

