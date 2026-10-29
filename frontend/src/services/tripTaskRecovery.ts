const STORAGE_KEY = 'pendingTripPlanTask'
const MAX_TASK_AGE_MS = 2 * 60 * 60 * 1000

export interface PendingTripPlanTask {
  taskId: string
  createdAt: string
}

export const savePendingTripPlanTask = (taskId: string): PendingTripPlanTask => {
  const task = {
    taskId,
    createdAt: new Date().toISOString()
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(task))
  return task
}

export const getPendingTripPlanTask = (): PendingTripPlanTask | null => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null

    const task = JSON.parse(raw) as Partial<PendingTripPlanTask>
    const createdAt = task.createdAt ? new Date(task.createdAt).getTime() : Number.NaN
    if (
      typeof task.taskId !== 'string'
      || !task.taskId
      || !Number.isFinite(createdAt)
      || Date.now() - createdAt >= MAX_TASK_AGE_MS
    ) {
      localStorage.removeItem(STORAGE_KEY)
      return null
    }

    return {
      taskId: task.taskId,
      createdAt: task.createdAt as string
    }
  } catch {
    localStorage.removeItem(STORAGE_KEY)
    return null
  }
}

export const clearPendingTripPlanTask = (taskId?: string): void => {
  if (!taskId) {
    localStorage.removeItem(STORAGE_KEY)
    return
  }

  const current = getPendingTripPlanTask()
  if (!current || current.taskId === taskId) {
    localStorage.removeItem(STORAGE_KEY)
  }
}
