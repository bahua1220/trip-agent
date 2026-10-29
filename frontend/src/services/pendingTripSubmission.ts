import type { TripFormData } from '@/types'

const STORAGE_KEY = 'pendingTripSubmission'
const MAX_SUBMISSION_AGE_MS = 60 * 60 * 1000

interface PendingTripSubmission {
  request: TripFormData
  createdAt: string
}

export const savePendingTripSubmission = (request: TripFormData): void => {
  const submission: PendingTripSubmission = {
    request,
    createdAt: new Date().toISOString()
  }
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(submission))
}

export const getPendingTripSubmission = (): TripFormData | null => {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return null

    const submission = JSON.parse(raw) as Partial<PendingTripSubmission>
    const createdAt = submission.createdAt ? new Date(submission.createdAt).getTime() : Number.NaN
    const request = submission.request
    if (
      !request
      || typeof request.city !== 'string'
      || typeof request.start_date !== 'string'
      || typeof request.end_date !== 'string'
      || !Number.isFinite(createdAt)
      || Date.now() - createdAt >= MAX_SUBMISSION_AGE_MS
    ) {
      sessionStorage.removeItem(STORAGE_KEY)
      return null
    }

    return request
  } catch {
    sessionStorage.removeItem(STORAGE_KEY)
    return null
  }
}

export const clearPendingTripSubmission = (): void => {
  sessionStorage.removeItem(STORAGE_KEY)
}
