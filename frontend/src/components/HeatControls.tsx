import { useEffect, useRef } from 'react'
import type { HeatMetric } from '../types'
import { METRIC_LABEL, RISK_COLOR, scoreToRgba } from '../lib/risk'
import { fmtDateTime } from '../lib/format'

interface Props {
  hours: string[]
  hour: number
  onHour: (h: number) => void
  onTick: () => void
  metric: HeatMetric
  onMetric: (m: HeatMetric) => void
  visible: boolean
  onVisible: (v: boolean) => void
  playing: boolean
  onPlaying: (p: boolean) => void
  peaks: number[]
  loading: boolean
  error: string | null
}

export function HeatControls(p: Props) {
  const { hours, hour, onHour, onTick, playing, onPlaying } = p
  const timer = useRef<number | null>(null)

  // Play animation: advance one hour every 450 ms, loop at the end.
  useEffect(() => {
    if (!playing || hours.length === 0) return
    timer.current = window.setInterval(() => {
      onTick()
    }, 450)
    return () => {
      if (timer.current) window.clearInterval(timer.current)
    }
  }, [playing, hours.length, onTick])

  const max = Math.max(0, hours.length - 1)
  return (
    <div className="heat-controls">
      <div className="heat-head">
        <label className="toggle">
          <input type="checkbox" checked={p.visible} onChange={(e) => p.onVisible(e.target.checked)} />
          Weather heatmap
        </label>
        <select value={p.metric} onChange={(e) => p.onMetric(e.target.value as HeatMetric)} disabled={!p.visible}>
          {(Object.keys(METRIC_LABEL) as HeatMetric[]).map((m) => (
            <option key={m} value={m}>
              {METRIC_LABEL[m]}
            </option>
          ))}
        </select>
        {p.loading && <span className="muted">Loading 48 h forecast…</span>}
        {p.error && <span className="error-inline">{p.error}</span>}
      </div>
      {hours.length > 0 && (
        <>
          <div className="slider-row">
            <button type="button" className="play" onClick={() => onPlaying(!playing)} aria-label={playing ? 'Pause' : 'Play'}>
              {playing ? '❚❚' : '▶'}
            </button>
            <div className="slider-wrap">
              <div className="peaks" aria-hidden title="Worst conditions anywhere in the corridor, per hour">
                {p.peaks.map((s, i) => {
                  const [r, g, b] = scoreToRgba(Math.max(s, 0.2))
                  return <span key={i} style={{ background: `rgb(${r},${g},${b})`, opacity: s < 0.15 ? 0.25 : 1 }} />
                })}
              </div>
              <input
                type="range"
                min={0}
                max={max}
                step={1}
                value={hour}
                onChange={(e) => {
                  onPlaying(false)
                  onHour(Number(e.target.value))
                }}
                aria-label="Forecast hour"
              />
            </div>
          </div>
          <div className="slider-label">
            <strong>+{hour} h</strong> from departure · {fmtDateTime(hours[hour])}
          </div>
        </>
      )}
      <Legend />
    </div>
  )
}

function Legend() {
  const items: [string, string][] = [
    ['Low', RISK_COLOR.low],
    ['Moderate', RISK_COLOR.moderate],
    ['High', RISK_COLOR.high],
    ['Severe', RISK_COLOR.severe],
    ['No Travel', RISK_COLOR.no_travel],
  ]
  return (
    <div className="legend">
      {items.map(([label, color]) => (
        <span key={label}>
          <i style={{ background: color }} />
          {label}
        </span>
      ))}
    </div>
  )
}
