export type RiskKey = 'low' | 'moderate' | 'high' | 'severe' | 'no_travel'

export interface Place {
  query: string
  name: string
  lat: number
  lng: number
}

export interface CheckpointWeather {
  wind_mph: number
  gust_mph: number
  rain_in_hr: number
  snow_in_hr: number
  temp_c: number | null
  weather_code: number | null
}

export interface Checkpoint {
  mile: number
  lat: number
  lng: number
  eta: string
  forecast_hour: string
  weather: CheckpointWeather | null
  risk: RiskKey
  risk_score: number
  condition_risk: { wind: RiskKey; rain: RiskKey; snow: RiskKey } | null
  drivers: string[]
}

export interface RouteSummary {
  miles_by_risk: Record<RiskKey, number>
  avg_risk: number
  max_risk: RiskKey
  no_travel: boolean
  missing_forecasts: number
}

export interface RouteOption {
  id: string
  name: string
  via: string
  source: 'osrm' | 'via-point'
  distance_mi: number
  duration_s: number
  arrival: string
  geometry: [number, number][]
  checkpoints: Checkpoint[]
  summary: RouteSummary
  rank: number
  recommended: boolean
  recommendation_reason: string | null
}

export interface Plan {
  plan_id: string
  origin: Place
  destination: Place
  departure: string
  load_lb: number
  interval_mi: number
  recommended_route_id: string
  routes: RouteOption[]
  warnings: string[]
}

export interface TripInput {
  origin: string
  destination: string
  departure: string // ISO with offset
  load_lb: number
  interval_mi: 10 | 25 | 50
}

export interface HeatCell {
  i: number
  j: number
  lat: number
  lng: number
  wind: (number | null)[]
  rain: (number | null)[]
  snow: (number | null)[]
  risk: (number | null)[]
}

export interface Heatmap {
  plan_id: string
  start: string
  hours: string[]
  grid: { nx: number; ny: number; bounds: { south: number; west: number; north: number; east: number } }
  cells: HeatCell[]
}

export type HeatMetric = 'risk' | 'wind' | 'rain' | 'snow'
