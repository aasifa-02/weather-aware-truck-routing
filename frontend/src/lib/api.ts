import type { Heatmap, Plan, TripInput } from '../types'

const BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') ?? ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    })
  } catch {
    throw new Error('Could not reach the server. Is the backend running?')
  }
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    throw new Error(formatError(body) ?? `Request failed (${res.status})`)
  }
  return body as T
}

function formatError(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null
  const b = body as Record<string, unknown>
  if (typeof b.detail === 'string') return b.detail
  // DRF field errors: { field: ["msg"] }
  const parts = Object.entries(b).map(([k, v]) => {
    const msg = Array.isArray(v) ? v.join(' ') : String(v)
    return k === 'non_field_errors' ? msg : `${k.replace('_', ' ')}: ${msg}`
  })
  return parts.join(' ') || null
}

export function planTrip(input: TripInput, signal?: AbortSignal): Promise<Plan> {
  return request<Plan>('/api/plan/', { method: 'POST', body: JSON.stringify(input), signal })
}

export function fetchHeatmap(planId: string, signal?: AbortSignal): Promise<Heatmap> {
  return request<Heatmap>(`/api/heatmap/${planId}/`, { signal })
}
