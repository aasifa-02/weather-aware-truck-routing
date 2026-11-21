import type { Heatmap, Plan, TripInput } from '../types'
import { fetchWeatherInBrowser, type WeatherRequest } from './browserWeather'

const BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') ?? ''

class ApiError extends Error {
  status: number
  body: unknown
  constructor(message: string, status: number, body: unknown) {
    super(message)
    this.status = status
    this.body = body
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new Error('Could not reach the server. It may be waking up; please try again in a moment.')
  }
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    throw new ApiError(formatError(body) ?? `Request failed (${res.status})`, res.status, body)
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

/** The server's weather sources are all busy: it tells us what to fetch ourselves. */
function weatherRequestFrom(e: unknown): WeatherRequest | null {
  if (!(e instanceof ApiError) || e.status !== 503) return null
  const b = e.body as { code?: string; weather_request?: WeatherRequest } | null
  return b?.code === 'weather_unavailable' && b.weather_request ? b.weather_request : null
}

export async function planTrip(input: TripInput, signal?: AbortSignal): Promise<Plan> {
  const post = (extra: object = {}) =>
    request<Plan>('/api/plan/', { method: 'POST', body: JSON.stringify({ ...input, ...extra }), signal })
  try {
    return await post()
  } catch (e) {
    const wr = weatherRequestFrom(e)
    if (!wr) throw e
    const client_weather = await fetchWeatherInBrowser(wr, signal)
    return post({ client_weather })
  }
}

export async function fetchHeatmap(planId: string, signal?: AbortSignal): Promise<Heatmap> {
  const path = `/api/heatmap/${planId}/`
  try {
    return await request<Heatmap>(path, { signal })
  } catch (e) {
    const wr = weatherRequestFrom(e)
    if (!wr) throw e
    const client_weather = await fetchWeatherInBrowser(wr, signal)
    return request<Heatmap>(path, { method: 'POST', body: JSON.stringify({ client_weather }), signal })
  }
}
