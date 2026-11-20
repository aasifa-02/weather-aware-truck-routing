import type { HeatMetric, RiskKey } from '../types'

export const RISK_ORDER: RiskKey[] = ['low', 'moderate', 'high', 'severe', 'no_travel']

export const RISK_LABEL: Record<RiskKey, string> = {
  low: 'Low',
  moderate: 'Moderate',
  high: 'High',
  severe: 'Severe',
  no_travel: 'No Travel',
}

export const RISK_COLOR: Record<RiskKey, string> = {
  low: '#16a34a',
  moderate: '#eab308',
  high: '#f97316',
  severe: '#dc2626',
  no_travel: '#7e22ce',
}

const RISK_RGB: [number, number, number][] = RISK_ORDER.map((k) => hexToRgb(RISK_COLOR[k]))

export const ROUTE_COLORS = ['#2563eb', '#0d9488', '#c026d3']

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

/** Thresholds (lower bounds of Moderate, High, Severe, No Travel) - mirrors the backend. */
export const METRIC_THRESHOLDS: Record<Exclude<HeatMetric, 'risk'>, [number, number, number, number]> = {
  wind: [25, 35, 45, 55],
  rain: [0.1, 0.25, 0.5, 1.0],
  snow: [0.5, 1.0, 2.0, 3.0],
}

export const METRIC_LABEL: Record<HeatMetric, string> = {
  risk: 'Overall risk (with load rules)',
  wind: 'Wind (mph)',
  rain: 'Rain (in/hr)',
  snow: 'Snow (in/hr)',
}

/** Map a raw value onto a continuous 0..4 risk scale using the band thresholds. */
export function continuousScore(value: number, t: readonly number[]): number {
  if (value <= 0) return 0
  const edges = [0, ...t]
  for (let k = 1; k < edges.length; k++) {
    if (value < edges[k]) return k - 1 + (value - edges[k - 1]) / (edges[k] - edges[k - 1])
  }
  return 4
}

/** RGBA for a 0..4 score. Calm areas fade out so the base map stays readable. */
export function scoreToRgba(score: number): [number, number, number, number] {
  const s = Math.max(0, Math.min(4, score))
  const lo = Math.floor(s)
  const hi = Math.min(4, lo + 1)
  const f = s - lo
  const a = RISK_RGB[lo]
  const b = RISK_RGB[hi]
  const alpha = s < 0.15 ? 0 : Math.min(0.75, 0.18 + s * 0.16)
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, alpha * 255]
}

export function discreteRiskRgba(level: number): [number, number, number, number] {
  const [r, g, b] = RISK_RGB[Math.max(0, Math.min(4, level))]
  const alpha = level === 0 ? 0.07 : 0.35 + level * 0.1
  return [r, g, b, alpha * 255]
}
