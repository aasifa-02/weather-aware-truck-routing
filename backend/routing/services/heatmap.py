"""Weather heatmap over the trip corridor for hours 0-48 after departure.

The grid is regular in Web-Mercator space (equal pixel rows on the map), so the browser
can draw it as a single image overlay with no distortion. Only cells within
`CORRIDOR_MI` of a route are fetched, which keeps Open-Meteo usage low. All 49 hours
are returned in one response; the slider then just swaps precomputed frames.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from django.core.cache import cache

from . import weather
from .geo import LatLng, haversine_mi, inverse_mercator_y, mercator_y
from .risk import classify_conditions

HOURS = 48
CORRIDOR_MI = 60
PAD_DEG = 1.0
TARGET_CELLS_LONG_SIDE = 28
MIN_STEP_DEG = 0.15
MAX_CELLS = 450
FALLBACK_MAX_POINTS = 120  # MET Norway is one request per point


class HeatmapError(Exception):
    pass


def _densify(route: list[LatLng], step_mi: float = 8) -> list[LatLng]:
    out = [route[0]]
    for a, b in zip(route, route[1:]):
        d = haversine_mi(a, b)
        n = max(1, int(d // step_mi))
        for k in range(1, n + 1):
            t = k / n
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def _near_corridor(p: LatLng, samples: list[LatLng], radius_mi: float) -> bool:
    # Cheap bounding test before the haversine.
    dlat = radius_mi / 69.0
    dlng = radius_mi / (69.0 * max(0.2, math.cos(math.radians(p[0]))))
    for s in samples:
        if abs(s[0] - p[0]) <= dlat and abs(s[1] - p[1]) <= dlng and haversine_mi(p, s) <= radius_mi:
            return True
    return False


def build_grid(routes: list[list[LatLng]]):
    pts = [p for r in routes for p in r]
    south = min(p[0] for p in pts) - PAD_DEG
    north = max(p[0] for p in pts) + PAD_DEG
    west = min(p[1] for p in pts) - PAD_DEG
    east = max(p[1] for p in pts) + PAD_DEG

    y0, y1 = mercator_y(south), mercator_y(north)
    x_span_rad = math.radians(east - west)
    long_side = max(x_span_rad, y1 - y0)
    step = max(long_side / TARGET_CELLS_LONG_SIDE, math.radians(MIN_STEP_DEG))
    nx = max(1, math.ceil(x_span_rad / step))
    ny = max(1, math.ceil((y1 - y0) / step))
    # Re-fit the bounds to whole cells.
    east = west + math.degrees(nx * step)
    y1 = y0 + ny * step
    north = inverse_mercator_y(y1)

    samples = [p for r in routes for p in _densify(r)]
    cells = []
    for j in range(ny):  # row 0 = north
        yc = y1 - (j + 0.5) * step
        lat = inverse_mercator_y(yc)
        for i in range(nx):
            lng = west + math.degrees((i + 0.5) * step)
            if _near_corridor((lat, lng), samples, CORRIDOR_MI):
                cells.append((i, j, lat, lng))
    if len(cells) > MAX_CELLS:  # very long trips: thin out evenly
        stride = math.ceil(len(cells) / MAX_CELLS)
        cells = cells[::stride]
    bounds = {"south": south, "west": west, "north": north, "east": east}
    return bounds, nx, ny, cells


def build_heatmap(plan_id: str, client_weather: dict | None = None) -> dict:
    cache_key = f"heatmap:{plan_id}"
    hit = cache.get(cache_key)
    if hit and client_weather is None:
        return hit
    plan = cache.get(f"plan:{plan_id}")
    if not plan:
        raise HeatmapError("Plan expired. Please plan the trip again.")

    departure = datetime.fromisoformat(plan["departure"]).astimezone(timezone.utc)
    start = departure.replace(minute=0, second=0, microsecond=0)
    if departure.minute >= 30:
        start += timedelta(hours=1)
    end = start + timedelta(hours=HOURS)

    bounds, nx, ny, cells = build_grid(plan["routes"])
    points = [(lat, lng) for _, _, lat, lng in cells]
    if client_weather is not None:
        try:
            forecasts = weather.forecasts_from_client(client_weather)
        except weather.WeatherError as exc:
            raise HeatmapError(str(exc)) from exc
        source = weather.SOURCE_CLIENT
    else:
        # WeatherUnavailable propagates so the view can hand the fetch to the browser.
        forecasts, source = weather.fetch_forecasts(points, start, end, fallback_max_points=FALLBACK_MAX_POINTS)

    hours = [(start + timedelta(hours=h)) for h in range(HOURS + 1)]
    keys = [weather.Forecast.hour_key(t) for t in hours]
    load = plan["load_lb"]

    out_cells = []
    for i, j, lat, lng in cells:
        f = forecasts.get(weather.round_point((lat, lng)))
        wind, rain, snow, risk = [], [], [], []
        for k in keys:
            wx = f.at_key(k) if f else None
            if wx is None:
                wind.append(None), rain.append(None), snow.append(None), risk.append(None)
                continue
            wind.append(round(wx.wind_mph, 1))
            rain.append(round(wx.rain_in_hr, 3))
            snow.append(round(wx.snow_in_hr, 3))
            risk.append(int(classify_conditions(wx.wind_mph, wx.rain_in_hr, wx.snow_in_hr, load).risk))
        out_cells.append({"i": i, "j": j, "lat": round(lat, 3), "lng": round(lng, 3),
                          "wind": wind, "rain": rain, "snow": snow, "risk": risk})

    result = {
        "plan_id": plan_id,
        "start": start.isoformat(),
        "hours": [t.isoformat() for t in hours],
        "grid": {"nx": nx, "ny": ny, "bounds": {k: round(v, 5) for k, v in bounds.items()}},
        "cells": out_cells,
        "weather_source": source,
    }
    cache.set(cache_key, result, 60 * 60)
    return result
