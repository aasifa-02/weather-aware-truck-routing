import { memo, useEffect, useMemo } from 'react'
import L from 'leaflet'
import { CircleMarker, ImageOverlay, MapContainer, Marker, Polyline, Popup, TileLayer, Tooltip, useMap } from 'react-leaflet'
import type { Checkpoint, Plan, RouteOption } from '../types'
import { RISK_COLOR, RISK_LABEL } from '../lib/risk'
import { fmtDateTime } from '../lib/format'

interface Props {
  plan: Plan | null
  selectedId: string
  onSelect: (id: string) => void
  colorFor: (id: string) => string
  heatUrl: string | null
  heatBounds: L.LatLngBoundsExpression | null
  heatOpacity: number
  truck: [number, number] | null
  truckLabel: string
}

const US_CENTER: [number, number] = [39.5, -98.35]

export function MapView({ plan, selectedId, onSelect, colorFor, heatUrl, heatBounds, heatOpacity, truck, truckLabel }: Props) {
  return (
    <MapContainer center={US_CENTER} zoom={4} preferCanvas className="map" zoomControl>
      <TileLayer
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · Weather: <a href="https://open-meteo.com/">Open-Meteo</a> · Routing: <a href="https://project-osrm.org/">OSRM</a>'
        maxZoom={18}
      />
      {heatUrl && heatBounds && (
        <ImageOverlay url={heatUrl} bounds={heatBounds} opacity={heatOpacity} zIndex={250} className="heat-overlay" />
      )}
      {plan && <FitToPlan plan={plan} />}
      {plan && <RouteLayers plan={plan} selectedId={selectedId} onSelect={onSelect} colorFor={colorFor} />}
      {plan && <Endpoints plan={plan} />}
      {truck && (
        <Marker position={truck} icon={truckIcon} zIndexOffset={1000} interactive={false}>
          <Tooltip direction="top" offset={[0, -12]} permanent className="truck-tip">
            {truckLabel}
          </Tooltip>
        </Marker>
      )}
    </MapContainer>
  )
}

const truckIcon = L.divIcon({ className: 'truck-icon', html: '<span>🚚</span>', iconSize: [28, 28], iconAnchor: [14, 14] })

function FitToPlan({ plan }: { plan: Plan }) {
  const map = useMap()
  useEffect(() => {
    const pts = plan.routes.flatMap((r) => r.geometry)
    if (pts.length) map.fitBounds(L.latLngBounds(pts), { padding: [30, 30] })
  }, [plan, map])
  return null
}

const Endpoints = memo(function Endpoints({ plan }: { plan: Plan }) {
  const mk = (label: string) => L.divIcon({ className: 'endpoint', html: `<span>${label}</span>`, iconSize: [26, 26], iconAnchor: [13, 13] })
  return (
    <>
      <Marker position={[plan.origin.lat, plan.origin.lng]} icon={mk('A')}>
        <Tooltip direction="top" offset={[0, -12]}>Origin: {plan.origin.name}</Tooltip>
      </Marker>
      <Marker position={[plan.destination.lat, plan.destination.lng]} icon={mk('B')}>
        <Tooltip direction="top" offset={[0, -12]}>Destination: {plan.destination.name}</Tooltip>
      </Marker>
    </>
  )
})

interface LayerProps {
  plan: Plan
  selectedId: string
  onSelect: (id: string) => void
  colorFor: (id: string) => string
}

// Memoised so slider movement (which only changes the heat overlay) never re-renders routes.
const RouteLayers = memo(function RouteLayers({ plan, selectedId, onSelect, colorFor }: LayerProps) {
  // Draw the selected route last so it sits on top.
  const ordered = useMemo(
    () => [...plan.routes].sort((a, b) => Number(a.id === selectedId) - Number(b.id === selectedId)),
    [plan, selectedId],
  )
  return (
    <>
      {ordered.map((r) => (
        <RouteLine key={r.id} route={r} selected={r.id === selectedId} color={colorFor(r.id)} onSelect={onSelect} />
      ))}
      {ordered.map((r) => (
        <Checkpoints key={`cp-${r.id}`} route={r} selected={r.id === selectedId} />
      ))}
    </>
  )
})

function RouteLine({ route, selected, color, onSelect }: { route: RouteOption; selected: boolean; color: string; onSelect: (id: string) => void }) {
  const handlers = useMemo(() => ({ click: () => onSelect(route.id) }), [onSelect, route.id])
  return (
    <>
      {selected && <Polyline positions={route.geometry} pathOptions={{ color: '#ffffff', weight: 11, opacity: 0.9 }} interactive={false} />}
      <Polyline
        positions={route.geometry}
        pathOptions={{
          color,
          weight: selected ? 6 : 4,
          opacity: selected ? 1 : 0.55,
          dashArray: route.recommended || selected ? undefined : '8 8',
        }}
        eventHandlers={handlers}
      >
        <Tooltip sticky>
          {route.name}
          {route.recommended ? ' (recommended)' : ''}: {Math.round(route.distance_mi)} mi
        </Tooltip>
      </Polyline>
    </>
  )
}

function Checkpoints({ route, selected }: { route: RouteOption; selected: boolean }) {
  return (
    <>
      {route.checkpoints.map((cp) => (
        <CircleMarker
          key={cp.mile}
          center={[cp.lat, cp.lng]}
          radius={selected ? 7 : 4}
          pathOptions={{
            color: '#ffffff',
            weight: selected ? 2 : 1,
            fillColor: RISK_COLOR[cp.risk],
            fillOpacity: selected ? 1 : 0.7,
            opacity: selected ? 1 : 0.7,
          }}
        >
          <Popup>
            <CheckpointPopup route={route} cp={cp} />
          </Popup>
        </CircleMarker>
      ))}
    </>
  )
}

function CheckpointPopup({ route, cp }: { route: RouteOption; cp: Checkpoint }) {
  const w = cp.weather
  return (
    <div className="cp-popup">
      <div className="cp-title">
        {route.name} · mile {Math.round(cp.mile)}
      </div>
      <div className="cp-risk" style={{ background: RISK_COLOR[cp.risk] }}>
        {RISK_LABEL[cp.risk]}
      </div>
      <div>ETA {fmtDateTime(cp.eta)}</div>
      {w ? (
        <table>
          <tbody>
            <tr>
              <td>Wind</td>
              <td>
                {w.wind_mph.toFixed(0)} mph <small>(gusts {w.gust_mph.toFixed(0)})</small>
              </td>
              <td>{cp.condition_risk && RISK_LABEL[cp.condition_risk.wind]}</td>
            </tr>
            <tr>
              <td>Rain</td>
              <td>{w.rain_in_hr.toFixed(2)} in/hr</td>
              <td>{cp.condition_risk && RISK_LABEL[cp.condition_risk.rain]}</td>
            </tr>
            <tr>
              <td>Snow</td>
              <td>{w.snow_in_hr.toFixed(2)} in/hr</td>
              <td>{cp.condition_risk && RISK_LABEL[cp.condition_risk.snow]}</td>
            </tr>
            {w.temp_c !== null && (
              <tr>
                <td>Temp</td>
                <td>{Math.round((w.temp_c * 9) / 5 + 32)} °F</td>
                <td />
              </tr>
            )}
          </tbody>
        </table>
      ) : (
        <div>No forecast available for this hour.</div>
      )}
      {cp.drivers.length > 0 && <div className="cp-drivers">{cp.drivers.join(' · ')}</div>}
    </div>
  )
}
