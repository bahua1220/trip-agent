import type { TripPlan } from '@/types'
import { syncHistoryEntry } from './api'

const STORAGE_KEY = 'tripPlanHistory'
const MAX_HISTORY_ITEMS = 20

export interface TripHistoryEntry {
  id: string
  plan: TripPlan
  createdAt: string
  updatedAt: string
}

const createId = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`

export const getTripHistory = (): TripHistoryEntry[] => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const entries = raw ? JSON.parse(raw) : []
    return Array.isArray(entries) ? entries : []
  } catch {
    return []
  }
}

export const saveTripPlanHistory = (plan: TripPlan, existingId?: string): TripHistoryEntry => {
  const history = getTripHistory()
  const now = new Date().toISOString()
  const previous = existingId ? history.find(item => item.id === existingId) : undefined
  const entry: TripHistoryEntry = {
    id: previous?.id || createId(),
    plan: JSON.parse(JSON.stringify(plan)),
    createdAt: previous?.createdAt || now,
    updatedAt: now
  }
  const nextHistory = [entry, ...history.filter(item => item.id !== entry.id)].slice(0, MAX_HISTORY_ITEMS)
  localStorage.setItem(STORAGE_KEY, JSON.stringify(nextHistory))
  void syncHistoryEntry(entry).catch(() => undefined)
  return entry
}

export const removeTripPlanHistory = (id: string) => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(getTripHistory().filter(item => item.id !== id)))
}
