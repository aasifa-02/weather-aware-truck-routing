import type { RouteOption } from '../types'
import { RISK_COLOR, RISK_LABEL, RISK_ORDER } from '../lib/risk'
import { fmtDateTime, fmtDuration, fmtMiles } from '../lib/format'

interface Props {
  routes: RouteOption[]
  selectedId: string
  onSelect: (id: string) => void
  colorFor: (id: string) => string
}

export function RouteCards({ routes, selectedId, onSelect, colorFor }: Props) {
  return (
    <div className="route-cards">
      {routes.map((r) => (
        <RouteCard key={r.id} route={r} selected={r.id === selectedId} color={colorFor(r.id)} onSelect={() => onSelect(r.id)} />
      ))}
    </div>
  )
}

function RouteCard({ route, selected, color, onSelect }: { route: RouteOption; selected: boolean; color: string; onSelect: () => void }) {
  const s = route.summary
  const total = route.distance_mi || 1
  return (
    <button
      type="button"
      className={`route-card${selected ? ' selected' : ''}${route.recommended ? ' recommended' : ''}`}
      onClick={onSelect}
      aria-pressed={selected}
    >
      <div className="card-head">
        <span className="swatch" style={{ background: color }} />
        <strong>{route.name}</strong>
        <span className="rank">#{route.rank}</span>
        {route.recommended && <span className="badge rec">Recommended</span>}
        {s.no_travel && <span className="badge danger">No Travel segment</span>}
      </div>
      {route.via && <div className="via">via {route.via}</div>}
      <div className="stats">
        <div>
          <span className="k">Distance</span>
          <span className="v">{fmtMiles(route.distance_mi)}</span>
        </div>
        <div>
          <span className="k">Drive time</span>
          <span className="v">{fmtDuration(route.duration_s)}</span>
        </div>
        <div>
          <span className="k">Avg risk</span>
          <span className="v">{s.avg_risk.toFixed(2)} / 4</span>
        </div>
        <div>
          <span className="k">Worst</span>
          <span className="v" style={{ color: RISK_COLOR[s.max_risk] }}>{RISK_LABEL[s.max_risk]}</span>
        </div>
      </div>
      <div className="risk-bar" title="Miles by risk level">
        {RISK_ORDER.map((k) =>
          s.miles_by_risk[k] > 0 ? (
            <span key={k} style={{ width: `${(s.miles_by_risk[k] / total) * 100}%`, background: RISK_COLOR[k] }} />
          ) : null,
        )}
      </div>
      <table className="risk-table">
        <tbody>
          {RISK_ORDER.map((k) => (
            <tr key={k} className={s.miles_by_risk[k] > 0 ? '' : 'zero'}>
              <td>
                <i style={{ background: RISK_COLOR[k] }} />
                {RISK_LABEL[k]}
              </td>
              <td>{s.miles_by_risk[k].toFixed(0)} mi</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="arrival">Arrive {fmtDateTime(route.arrival)}</div>
      {route.recommendation_reason && <div className="why">Why: {route.recommendation_reason}</div>}
      {s.missing_forecasts > 0 && <div className="note">{s.missing_forecasts} checkpoint(s) had no forecast data.</div>}
    </button>
  )
}

