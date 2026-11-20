import { useState, type FormEvent } from 'react'
import type { TripInput } from '../types'
import { defaultDepartureLocal, localInputToIso, localTimeZone, toLocalInput } from '../lib/format'

interface Props {
  loading: boolean
  onSubmit: (input: TripInput) => void
}

const EXAMPLES: { label: string; origin: string; destination: string }[] = [
  { label: 'Dallas → Denver', origin: 'Dallas, TX', destination: 'Denver, CO' },
  { label: 'Chicago → Minneapolis', origin: 'Chicago, IL', destination: 'Minneapolis, MN' },
  { label: 'Salt Lake → Cheyenne', origin: 'Salt Lake City, UT', destination: 'Cheyenne, WY' },
]

export function TripForm({ loading, onSubmit }: Props) {
  const [origin, setOrigin] = useState('Dallas, TX')
  const [destination, setDestination] = useState('Denver, CO')
  const [departure, setDeparture] = useState(defaultDepartureLocal)
  const [load, setLoad] = useState('40000')
  const [interval, setInterval] = useState<TripInput['interval_mi']>(25)

  // Forecasts reach ~2 weeks out; computed once so the limits don't shift while typing.
  const [limits] = useState(() => {
    const now = Date.now()
    return { min: toLocalInput(new Date(now - 3600 * 1000)), max: toLocalInput(new Date(now + 13 * 24 * 3600 * 1000)) }
  })

  function submit(e: FormEvent) {
    e.preventDefault()
    onSubmit({
      origin: origin.trim(),
      destination: destination.trim(),
      departure: localInputToIso(departure),
      load_lb: Number(load),
      interval_mi: interval,
    })
  }

  return (
    <form className="trip-form" onSubmit={submit}>
      <label>
        <span>Origin</span>
        <input value={origin} onChange={(e) => setOrigin(e.target.value)} placeholder="City, state or address" required />
      </label>
      <label>
        <span>Destination</span>
        <input value={destination} onChange={(e) => setDestination(e.target.value)} placeholder="City, state or address" required />
      </label>
      <div className="row">
        <label>
          <span>Departure ({localTimeZone})</span>
          <input
            type="datetime-local"
            value={departure}
            min={limits.min}
            max={limits.max}
            onChange={(e) => setDeparture(e.target.value)}
            required
          />
        </label>
      </div>
      <div className="row two">
        <label>
          <span>Load weight (lb)</span>
          <input type="number" min={0} max={200000} step={500} value={load} onChange={(e) => setLoad(e.target.value)} required />
        </label>
        <label>
          <span>Checkpoint every</span>
          <select value={interval} onChange={(e) => setInterval(Number(e.target.value) as TripInput['interval_mi'])}>
            <option value={10}>10 miles</option>
            <option value={25}>25 miles</option>
            <option value={50}>50 miles</option>
          </select>
        </label>
      </div>
      <button type="submit" className="primary" disabled={loading}>
        {loading ? 'Planning…' : 'Plan safest route'}
      </button>
      <div className="examples">
        {EXAMPLES.map((ex) => (
          <button
            key={ex.label}
            type="button"
            className="chip"
            onClick={() => {
              setOrigin(ex.origin)
              setDestination(ex.destination)
            }}
          >
            {ex.label}
          </button>
        ))}
      </div>
    </form>
  )
}
