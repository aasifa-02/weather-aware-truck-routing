"""Trip planning: routes -> checkpoints -> weather at ETA -> risk -> ranking."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.cache import cache

from . import geocoding, routing_api, weather
from .geo import LatLng, checkpoint_miles, simplify
from .risk import RankInput, Risk, classify_conditions, explain_recommendation, rank_routes, summarize_route

RISK_KEYS = {
    Risk.LOW: "low",
    Risk.MODERATE: "moderate",
    Risk.HIGH: "high",
    Risk.SEVERE: "severe",
    Risk.NO_TRAVEL: "no_travel",
}
ROUTE_IDS = "ABC"
GEOMETRY_TOLERANCE_DEG = 0.0015  # ~150 m; plenty for display, keeps payload small
PLAN_TTL = 60 * 60 * 6


class PlanError(Exception):
    pass


@dataclass
class TripRequest:
    origin: str
    destination: str
    departure: datetime  # timezone-aware
    load_lb: float
    interval_mi: float = 25


def plan_trip(req: TripRequest) -> dict:
    try:
        origin = geocoding.geocode(req.origin)
        dest = geocoding.geocode(req.destination)
    except geocoding.GeocodeError as exc:
        raise PlanError(str(exc)) from exc

    try:
        options = routing_api.get_route_options((origin.lat, origin.lng), (dest.lat, dest.lng))
    except routing_api.RoutingError as exc:
        raise PlanError(str(exc)) from exc

    # Sample every route at the chosen interval and compute the ETA at each checkpoint.
    sampled: list[list[tuple[float, LatLng, datetime]]] = []
    for opt in options:
        pts = []
        for mile in checkpoint_miles(opt.line.total_miles, req.interval_mi):
            pos, secs = opt.line.locate(mile)
            pts.append((mile, pos, (req.departure + timedelta(seconds=round(secs))).replace(microsecond=0)))
        sampled.append(pts)

    last_eta = max(p[-1][2] for p in sampled)
    if not weather.forecast_window_ok(req.departure, last_eta):
        raise PlanError(
            "Forecasts are only available up to about 15 days ahead. "
            "Pick a departure time between now and two weeks out."
        )

    # One batched weather fetch for every checkpoint on every route.
    try:
        forecasts = weather.fetch_forecasts(
            [pos for pts in sampled for _, pos, _ in pts], req.departure, last_eta
        )
    except weather.WeatherError as exc:
        raise PlanError(str(exc)) from exc

    routes, rank_inputs = [], []
    for idx, (opt, pts) in enumerate(zip(options, sampled)):
        route_id = ROUTE_IDS[idx]
        checkpoints, risks, miles, missing = [], [], [], 0
        for mile, pos, eta in pts:
            wx = weather.lookup(forecasts, pos, eta)
            if wx is None:
                missing += 1
                level, drivers, cond = Risk.LOW, ("No forecast data for this hour",), None
            else:
                result = classify_conditions(wx.wind_mph, wx.rain_in_hr, wx.snow_in_hr, req.load_lb)
                level, drivers = result.risk, result.drivers
                cond = {
                    "wind": RISK_KEYS[result.wind],
                    "rain": RISK_KEYS[result.rain],
                    "snow": RISK_KEYS[result.snow],
                }
            risks.append(level)
            miles.append(mile)
            checkpoints.append(
                {
                    "mile": round(mile, 1),
                    "lat": round(pos[0], 5),
                    "lng": round(pos[1], 5),
                    "eta": eta.isoformat(),
                    "forecast_hour": weather.Forecast.hour_key(eta),
                    "weather": wx.as_dict() if wx else None,
                    "risk": RISK_KEYS[level],
                    "risk_score": int(level),
                    "condition_risk": cond,
                    "drivers": list(drivers),
                }
            )
        summary = summarize_route(miles, risks, opt.distance_mi)
        rank_inputs.append(RankInput(route_id, summary, opt.duration_s))
        routes.append(
            {
                "id": route_id,
                "name": f"Route {route_id}",
                "via": opt.summary,
                "source": opt.source,
                "distance_mi": round(opt.distance_mi, 1),
                "duration_s": round(opt.duration_s),
                "arrival": (req.departure + timedelta(seconds=round(opt.duration_s))).replace(microsecond=0).isoformat(),
                "geometry": [[round(a, 5), round(b, 5)] for a, b in simplify(opt.line.coords, GEOMETRY_TOLERANCE_DEG)],
                "checkpoints": checkpoints,
                "summary": {
                    "miles_by_risk": {RISK_KEYS[r]: round(m, 1) for r, m in summary.miles_by_risk.items()},
                    "avg_risk": round(summary.avg_risk, 3),
                    "max_risk": RISK_KEYS[summary.max_risk],
                    "no_travel": summary.no_travel_miles > 0,
                    "missing_forecasts": missing,
                },
            }
        )

    ranked = rank_routes(rank_inputs)
    best = ranked[0]
    order = {r.route_id: i + 1 for i, r in enumerate(ranked)}
    for r in routes:
        r["rank"] = order[r["id"]]
        r["recommended"] = r["id"] == best.route_id
        r["recommendation_reason"] = (
            explain_recommendation(best, ranked[1:], {r['id']: r['name'] for r in routes}) if r["recommended"] else None
        )
    routes.sort(key=lambda r: r["rank"])

    warnings = []
    if all(r["summary"]["no_travel"] for r in routes):
        warnings.append("Every route crosses No Travel conditions at this departure time. Consider delaying the trip.")
    elif best.summary.severe_miles > 0:
        warnings.append("The recommended route still has Severe-risk miles. Check conditions before departing.")
    if len(routes) < 3:
        warnings.append(f"Only {len(routes)} distinct route(s) could be found for this trip.")

    plan_id = uuid.uuid4().hex
    cache.set(
        f"plan:{plan_id}",
        {
            "routes": [[tuple(p) for p in r["geometry"]] for r in routes],
            "departure": req.departure.isoformat(),
            "load_lb": req.load_lb,
        },
        PLAN_TTL,
    )

    return {
        "plan_id": plan_id,
        "origin": origin.as_dict(),
        "destination": dest.as_dict(),
        "departure": req.departure.replace(microsecond=0).isoformat(),
        "load_lb": req.load_lb,
        "interval_mi": req.interval_mi,
        "recommended_route_id": best.route_id,
        "routes": routes,
        "warnings": warnings,
    }
