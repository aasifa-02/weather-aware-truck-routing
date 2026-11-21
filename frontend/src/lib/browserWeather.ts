/**
 * Last-resort weather path. If every server-side weather source is rate limited, the API
 * replies 503 with a `weather_request`: the exact Open-Meteo call to make. The browser
 * makes it from the user's own connection and sends the raw data back; the server still
 * does all unit conversion and risk scoring, so the results are identical.
 */
export interface WeatherRequest {
  url: string
  points: [number, number][]
  batch_size: number
  params: Record<string, string>
}

export interface ClientWeather {
  points: [number, number][]
  hourly: Record<string, unknown>[]
}

export async function fetchWeatherInBrowser(req: WeatherRequest, signal?: AbortSignal): Promise<ClientWeather> {
  const batches: [number, number][][] = []
  for (let i = 0; i < req.points.length; i += req.batch_size) batches.push(req.points.slice(i, i + req.batch_size))

  const results = await Promise.all(
    batches.map(async (batch) => {
      const qs = new URLSearchParams({
        ...req.params,
        latitude: batch.map((p) => p[0].toFixed(2)).join(','),
        longitude: batch.map((p) => p[1].toFixed(2)).join(','),
      })
      const res = await fetch(`${req.url}?${qs}`, { signal })
      if (!res.ok) throw new Error(`Weather service unavailable from this browser too (HTTP ${res.status}). Please try again in a few minutes.`)
      const body = await res.json()
      const items: { hourly?: Record<string, unknown> }[] = Array.isArray(body) ? body : [body]
      if (items.length !== batch.length) throw new Error('Unexpected weather response.')
      return items.map((it) => it.hourly ?? { time: [] })
    }),
  )
  return { points: req.points, hourly: results.flat() }
}
