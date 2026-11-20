import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { HeatMetric, Heatmap, Plan, TripInput } from './types'
import { fetchHeatmap, planTrip } from './lib/api'
import { buildFrames, hourlyPeak } from './lib/heatFrames'
import { ROUTE_COLORS } from './lib/risk'
import { truckPosition } from './lib/truck'
import { fmtDateTime } from './lib/format'
import { TripForm } from './components/TripForm'
import { RouteCards } from './components/RouteCards'
import { MapView } from './components/MapView'
import { HeatControls } from './components/HeatControls'

export default function App() {
  const [plan, setPlan] = useState<Plan | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState('')

  const [heat, setHeat] = useState<Heatmap | null>(null)
  const [heatLoading, setHeatLoading] = useState(false)
  const [heatError, setHeatError] = useState<string | null>(null)
  const [metric, setMetric] = useState<HeatMetric>('risk')
  const [hour, setHour] = useState(0)
  const [heatVisible, setHeatVisible] = useState(true)
  const [playing, setPlaying] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const onSubmit = useCallback(async (input: TripInput) => {
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    setLoading(true)
    setError(null)
    setHeat(null)
    setHeatError(null)
    setPlaying(false)
    setHour(0)
    try {
      const result = await planTrip(input, ctrl.signal)
      setPlan(result)
      setSelectedId(result.recommended_route_id)
      // Heatmap loads after the routes are on screen so the main answer is never delayed.
      setHeatLoading(true)
      fetchHeatmap(result.plan_id, ctrl.signal)
        .then(setHeat)
        .catch((e: Error) => e.name !== 'AbortError' && setHeatError(e.message))
        .finally(() => setHeatLoading(false))
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  // Stable colour per route letter (A/B/C) regardless of ranking order.
  const colorFor = useCallback((id: string) => ROUTE_COLORS[Math.max(0, 'ABC'.indexOf(id))], [])

  // All 49 frames for the active metric are rendered once; the slider just picks one.
  const frames = useMemo(() => (heat ? buildFrames(heat, metric) : []), [heat, metric])
  const peaks = useMemo(() => (heat ? hourlyPeak(heat, metric) : []), [heat, metric])
  const heatBounds = useMemo(
    () =>
      heat
        ? ([
            [heat.grid.bounds.south, heat.grid.bounds.west],
            [heat.grid.bounds.north, heat.grid.bounds.east],
          ] as [[number, number], [number, number]])
        : null,
    [heat],
  )

  const hoursCount = heat?.hours.length ?? 0
  const tick = useCallback(() => setHour((h) => (hoursCount ? (h + 1) % hoursCount : 0)), [hoursCount])

  useEffect(() => {
    // Spacebar toggles playback when the slider has focus elsewhere on the page.
    const onKey = (e: KeyboardEvent) => {
      if (e.code === 'Space' && (e.target as HTMLElement)?.tagName === 'BODY' && heat) {
        e.preventDefault()
        setPlaying((p) => !p)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [heat])

  const selected = plan?.routes.find((r) => r.id === selectedId) ?? null
  const truck = useMemo(() => {
    if (!selected || !heat) return null
    return truckPosition(selected, heat.hours[hour])
  }, [selected, heat, hour])
  const truckLabel = truck
    ? truck.status === 'arrived'
      ? 'Arrived'
      : truck.status === 'before'
        ? 'Not departed'
        : `Mile ${Math.round(truck.mile)}`
    : ''

  return (
    <div className="layout">
      <aside className="sidebar">
        <header>
          <h1>Weather-Aware Truck Routing</h1>
          <p>Compare three routes by forecast weather at the time the truck reaches each checkpoint.</p>
        </header>
        <TripForm loading={loading} onSubmit={onSubmit} />
        {error && <div className="error">{error}</div>}
        {plan && (
          <section className="results">
            <div className="trip-meta">
              <div>
                <strong>{shortName(plan.origin.name)}</strong> → <strong>{shortName(plan.destination.name)}</strong>
              </div>
              <div className="muted">
                Depart {fmtDateTime(plan.departure)} · {plan.load_lb.toLocaleString()} lb · checkpoint every {plan.interval_mi} mi
              </div>
            </div>
            {plan.warnings.map((w) => (
              <div key={w} className="warning">
                {w}
              </div>
            ))}
            <RouteCards routes={plan.routes} selectedId={selectedId} onSelect={setSelectedId} colorFor={colorFor} />
          </section>
        )}
        <footer className="muted small">
          Routing © OSRM / OpenStreetMap · Geocoding Nominatim / Photon · Forecasts Open-Meteo
        </footer>
      </aside>
      <main className="map-pane">
        <MapView
          plan={plan}
          selectedId={selectedId}
          onSelect={setSelectedId}
          colorFor={colorFor}
          heatUrl={heatVisible && frames.length ? frames[hour] : null}
          heatBounds={heatBounds}
          heatOpacity={0.85}
          truck={truck?.pos ?? null}
          truckLabel={truckLabel}
        />
        {plan && (
          <HeatControls
            hours={heat?.hours ?? []}
            hour={hour}
            onHour={setHour}
            onTick={tick}
            metric={metric}
            onMetric={setMetric}
            visible={heatVisible}
            onVisible={setHeatVisible}
            playing={playing}
            onPlaying={setPlaying}
            peaks={peaks}
            loading={heatLoading}
            error={heatError}
          />
        )}
        {loading && <div className="map-loading">Fetching routes and forecasts…</div>}
      </main>
    </div>
  )
}

function shortName(name: string): string {
  return name.split(',').slice(0, 2).join(',')
}
