"""Hourly forecasts for many points, with fallbacks so one rate-limited service
doesn't take the app down.

Provider chain (first one that works wins):

1. **Open-Meteo** (primary): many coordinates per request, batched and cached.
   If it returns HTTP 429, it's put on a cool-down (its Retry-After, or 15 minutes)
   so later requests go straight to the fallback instead of hitting it again.
2. **MET Norway Locationforecast** (fallback): keyless, global, one request per point.
   Points are merged onto a coarser grid (0.1 degrees or more) so a plan needs only
   a few dozen requests, fetched in parallel within MET's rate limit.
3. **The user's browser** (last resort): if both fail, the API answers 503 with a
   `weather_request` describing exactly which Open-Meteo call to make. The frontend
   makes that call from the reviewer's own IP and re-submits the data, and the server
   scores it with the same code.

Every provider is converted into the same `Forecast` structure (Open-Meteo's hourly
layout, metric units), so unit conversion and risk scoring are identical whatever
the source:
    wind_speed_10m / wind_gusts_10m  mph
    rain + showers                   mm in the preceding hour  -> in/hr (/ 25.4)
    snowfall                         cm in the preceding hour  -> in/hr (/ 2.54)
"""
from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from django.conf import settings
from django.core.cache import cache

from .geo import LatLng
from .http import RateLimited, UpstreamError, get_json

logger = logging.getLogger(__name__)

HOURLY_VARS = ("wind_speed_10m", "wind_gusts_10m", "rain", "showers", "snowfall", "temperature_2m", "weather_code")
BATCH_SIZE = 100  # locations per Open-Meteo request
MAX_FORECAST_DAYS = 16
MM_PER_IN = 25.4
CM_PER_IN = 2.54
COORD_DECIMALS = 2  # ~1 km; finer than the forecast models, so rounding improves cache hits

OPEN_METEO_COOLDOWN_KEY = "cooldown:open-meteo"
OPEN_METEO_DEFAULT_COOLDOWN = 15 * 60
OPEN_METEO_MAX_COOLDOWN = 60 * 60

MET_MS_TO_MPH = 2.236936
MET_MAX_WORKERS = 6
MET_DEFAULT_MAX_POINTS = 80
MET_MIN_CELL_DEG = 0.1
# Open-Meteo's own snow ratio: 10 mm water equivalent is about 7 cm of snow.
SNOW_CM_PER_MM_WATER = 0.7

CLIENT_MAX_POINTS = 1000
CLIENT_MAX_HOURS = 24 * MAX_FORECAST_DAYS

SOURCE_OPEN_METEO = "open-meteo"
SOURCE_MET_NORWAY = "met-norway"
SOURCE_CLIENT = "browser-open-meteo"


class WeatherError(Exception):
    pass


class WeatherUnavailable(WeatherError):
    """Every server-side provider failed; carries what the browser should fetch instead."""

    def __init__(self, message: str, request: dict):
        super().__init__(message)
        self.request = request


@dataclass(frozen=True)
class HourlyWeather:
    wind_mph: float
    gust_mph: float | None
    rain_in_hr: float
    snow_in_hr: float
    temp_c: float | None
    weather_code: int | None

    def as_dict(self) -> dict:
        return {
            "wind_mph": round(self.wind_mph, 1),
            "gust_mph": None if self.gust_mph is None else round(self.gust_mph, 1),
            "rain_in_hr": round(self.rain_in_hr, 3),
            "snow_in_hr": round(self.snow_in_hr, 3),
            "temp_c": None if self.temp_c is None else round(self.temp_c, 1),
            "weather_code": self.weather_code,
        }


class Forecast:
    """Hourly series for one location, in Open-Meteo's layout. Look up by UTC hour."""

    def __init__(self, times: Sequence[str], hourly: dict[str, list]):
        self._index = {t: i for i, t in enumerate(times)}
        self._hourly = hourly
        self.times = list(times)

    @staticmethod
    def hour_key(when: datetime) -> str:
        """Nearest whole UTC hour, formatted like Open-Meteo (`2026-11-20T14:00`)."""
        when = when.astimezone(timezone.utc)
        rounded = (when + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
        return rounded.strftime("%Y-%m-%dT%H:00")

    def at(self, when: datetime) -> HourlyWeather | None:
        return self.at_key(self.hour_key(when))

    def _value(self, name: str, i: int) -> Any:
        series = self._hourly.get(name)
        if not series or i >= len(series):
            return None
        return series[i]

    def at_key(self, key: str) -> HourlyWeather | None:
        i = self._index.get(key)
        if i is None:
            return None
        wind = self._value("wind_speed_10m", i)
        if wind is None:
            return None  # hour exists but the model has no data

        def num(name: str) -> float:
            v = self._value(name, i)
            return float(v) if v is not None else 0.0

        gust = self._value("wind_gusts_10m", i)
        temp = self._value("temperature_2m", i)
        code = self._value("weather_code", i)
        return HourlyWeather(
            wind_mph=float(wind),
            gust_mph=None if gust is None else float(gust),
            rain_in_hr=(num("rain") + num("showers")) / MM_PER_IN,
            snow_in_hr=num("snowfall") / CM_PER_IN,
            temp_c=None if temp is None else float(temp),
            weather_code=None if code is None else int(code),
        )


def round_point(p: LatLng) -> LatLng:
    return round(p[0], COORD_DECIMALS), round(p[1], COORD_DECIMALS)


def forecast_window_ok(start: datetime, end: datetime) -> bool:
    now = datetime.now(timezone.utc)
    return start >= now - timedelta(days=2) and end <= now + timedelta(days=MAX_FORECAST_DAYS - 1)


def _hour_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    start_h = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    end_h = end.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
    return start_h, end_h


def open_meteo_params(batch: Sequence[LatLng], start_h: datetime, end_h: datetime) -> dict:
    params = {
        "hourly": ",".join(HOURLY_VARS),
        "wind_speed_unit": "mph",
        "timezone": "GMT",
        "start_hour": start_h.strftime("%Y-%m-%dT%H:%M"),
        "end_hour": end_h.strftime("%Y-%m-%dT%H:%M"),
    }
    if batch:
        params["latitude"] = ",".join(f"{lat:.2f}" for lat, _ in batch)
        params["longitude"] = ",".join(f"{lng:.2f}" for _, lng in batch)
    return params


# --------------------------------------------------------------------------- Open-Meteo


def _fetch_open_meteo(points: list[LatLng], start_h: datetime, end_h: datetime) -> dict[LatLng, Forecast]:
    out: dict[LatLng, Forecast] = {}
    for i in range(0, len(points), BATCH_SIZE):
        batch = points[i : i + BATCH_SIZE]
        data = get_json("open-meteo", settings.OPEN_METEO_URL, open_meteo_params(batch, start_h, end_h), ttl=60 * 60)
        items = data if isinstance(data, list) else [data]
        if len(items) != len(batch):
            raise UpstreamError("open-meteo", "unexpected number of locations in response")
        for p, item in zip(batch, items):
            hourly = item.get("hourly") or {}
            out[p] = Forecast(hourly.get("time", []), hourly)
    return out


# --------------------------------------------------------------------------- MET Norway


def _met_hour(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def met_to_forecast(timeseries: list[dict], start_h: datetime, end_h: datetime) -> Forecast:
    """Convert a MET Norway `timeseries` into Open-Meteo's hourly layout.

    * Wind and temperature are instantaneous values. Steps are hourly for about 2.5 days,
      then 6-hourly, and the gaps are interpolated linearly.
    * Precipitation: Open-Meteo reports the hour *ending* at T, while MET's `next_1_hours`
      at T-1 covers [T-1, T]. Past the hourly range, `next_6_hours` is spread evenly.
    * MET's compact product has no snowfall, so precipitation is split by temperature:
      all snow at <= 0 C, all rain at >= 2 C, linear in between. Snow uses Open-Meteo's
      ratio (1 mm water = 0.7 cm snow), so both sources describe snow the same way.
    """
    steps = []
    for entry in timeseries:
        details = entry.get("data", {}).get("instant", {}).get("details", {})
        if "wind_speed" not in details:
            continue
        steps.append((_met_hour(entry["time"]), entry["data"], details))
    steps.sort(key=lambda s: s[0])

    times: list[str] = []
    cols: dict[str, list] = {name: [] for name in HOURLY_VARS}
    t = start_h
    k = 0
    while t <= end_h:
        times.append(t.strftime("%Y-%m-%dT%H:00"))
        while k + 1 < len(steps) and steps[k + 1][0] <= t:
            k += 1
        wind = temp = precip = None
        if steps and steps[0][0] <= t <= steps[-1][0]:
            t0, _, d0 = steps[k]
            if t0 == t or k + 1 >= len(steps):
                wind, temp = d0["wind_speed"], d0.get("air_temperature")
            else:
                t1, _, d1 = steps[k + 1]
                f = (t - t0) / (t1 - t0)
                wind = d0["wind_speed"] + (d1["wind_speed"] - d0["wind_speed"]) * f
                if d0.get("air_temperature") is not None and d1.get("air_temperature") is not None:
                    temp = d0["air_temperature"] + (d1["air_temperature"] - d0["air_temperature"]) * f
            precip = _met_precip_hour_ending(steps, t)
        if wind is None:
            for name in HOURLY_VARS:
                cols[name].append(None)
        else:
            precip = precip or 0.0
            snow_frac = 0.0 if temp is None else 1.0 if temp <= 0 else 0.0 if temp >= 2 else (2 - temp) / 2
            cols["wind_speed_10m"].append(wind * MET_MS_TO_MPH)
            cols["wind_gusts_10m"].append(None)  # not in MET's compact product
            cols["rain"].append(precip * (1 - snow_frac))
            cols["showers"].append(0.0)
            cols["snowfall"].append(precip * snow_frac * SNOW_CM_PER_MM_WATER)
            cols["temperature_2m"].append(temp)
            cols["weather_code"].append(None)
        t += timedelta(hours=1)
    return Forecast(times, cols)


def _met_precip_hour_ending(steps: list, t: datetime) -> float | None:
    """Precipitation (mm) in the hour [t-1h, t]."""
    begin = t - timedelta(hours=1)
    best = None
    for st, data, _ in steps:
        if st > begin:
            break
        best = (st, data)
    if best is None:
        return None
    st, data = best
    if st == begin and "next_1_hours" in data:
        return data["next_1_hours"].get("details", {}).get("precipitation_amount")
    if "next_6_hours" in data and begin < st + timedelta(hours=6):
        amount = data["next_6_hours"].get("details", {}).get("precipitation_amount")
        return None if amount is None else amount / 6
    return None


def _coarse_groups(points: list[LatLng], max_points: int) -> dict[LatLng, list[LatLng]]:
    """Merge points into grid cells (>= 0.1 degrees) until there are at most `max_points` cells."""
    cell = MET_MIN_CELL_DEG
    while True:
        groups: dict[LatLng, list[LatLng]] = {}
        for p in points:
            key = (round(math.floor(p[0] / cell) * cell + cell / 2, 4), round(math.floor(p[1] / cell) * cell + cell / 2, 4))
            groups.setdefault(key, []).append(p)
        if len(groups) <= max_points:
            return groups
        cell *= 1.5


def _fetch_met_point(p: LatLng) -> list[dict]:
    data = get_json(
        "met-norway",
        settings.MET_NORWAY_URL,
        {"lat": f"{p[0]:.4f}", "lon": f"{p[1]:.4f}"},
        ttl=45 * 60,
        retries=1,
    )
    return data.get("properties", {}).get("timeseries", [])


def _fetch_met_norway(points: list[LatLng], start_h: datetime, end_h: datetime, max_points: int) -> dict[LatLng, Forecast]:
    groups = _coarse_groups(points, max_points)
    keys = list(groups)
    with ThreadPoolExecutor(max_workers=MET_MAX_WORKERS) as pool:
        series = list(pool.map(_fetch_met_point, keys))
    out: dict[LatLng, Forecast] = {}
    for key, ts in zip(keys, series):
        forecast = met_to_forecast(ts, start_h, end_h)
        for p in groups[key]:
            out[p] = forecast
    return out


# --------------------------------------------------------------------------- chain


def _open_meteo_cooling_down() -> bool:
    return bool(cache.get(OPEN_METEO_COOLDOWN_KEY))


def _start_open_meteo_cooldown(exc: RateLimited) -> None:
    seconds = exc.retry_after if exc.retry_after else OPEN_METEO_DEFAULT_COOLDOWN
    seconds = int(min(max(seconds, 60), OPEN_METEO_MAX_COOLDOWN))
    cache.set(OPEN_METEO_COOLDOWN_KEY, True, seconds)
    logger.warning("Open-Meteo rate limited; using fallback for %ss", seconds)


def fetch_forecasts(
    points: Iterable[LatLng],
    start: datetime,
    end: datetime,
    *,
    fallback_max_points: int = MET_DEFAULT_MAX_POINTS,
) -> tuple[dict[LatLng, Forecast], str]:
    """Forecasts for all points covering [start, end] (UTC), plus the name of the source used.

    Keys are coordinates rounded to 0.01 degrees (see `lookup`). Raises WeatherUnavailable
    (with a browser-side request description) when every server-side provider fails.
    """
    unique = sorted({round_point(p) for p in points})
    if not unique:
        return {}, SOURCE_OPEN_METEO
    start_h, end_h = _hour_window(start, end)
    errors = []

    if settings.WEATHER_PRIMARY_ENABLED and not _open_meteo_cooling_down():
        try:
            return _fetch_open_meteo(unique, start_h, end_h), SOURCE_OPEN_METEO
        except RateLimited as exc:
            _start_open_meteo_cooldown(exc)
            errors.append(str(exc))
        except UpstreamError as exc:
            errors.append(str(exc))
    else:
        errors.append("open-meteo: cooling down after rate limit")

    if settings.WEATHER_FALLBACK_ENABLED:
        try:
            return _fetch_met_norway(unique, start_h, end_h, fallback_max_points), SOURCE_MET_NORWAY
        except UpstreamError as exc:
            errors.append(str(exc))

    raise WeatherUnavailable(
        "Weather services are busy right now (" + "; ".join(errors) + ").",
        browser_request(unique, start_h, end_h),
    )


def browser_request(points: list[LatLng], start_h: datetime, end_h: datetime) -> dict:
    """What the browser should fetch from Open-Meteo when the server can't."""
    return {
        "url": settings.OPEN_METEO_PUBLIC_URL,
        "points": [[lat, lng] for lat, lng in points],
        "batch_size": BATCH_SIZE,
        "params": open_meteo_params([], start_h, end_h),
    }


def forecasts_from_client(payload: Any) -> dict[LatLng, Forecast]:
    """Validate weather the browser fetched from Open-Meteo and build Forecasts from it.

    Expected shape: {"points": [[lat, lng], ...], "hourly": [{"time": [...], <var>: [...]}, ...]}
    with one `hourly` object per point (Open-Meteo's own response layout).
    """
    if not isinstance(payload, dict):
        raise WeatherError("client_weather must be an object.")
    pts, hourly = payload.get("points"), payload.get("hourly")
    if not isinstance(pts, list) or not isinstance(hourly, list) or len(pts) != len(hourly):
        raise WeatherError("client_weather needs matching 'points' and 'hourly' lists.")
    if len(pts) > CLIENT_MAX_POINTS:
        raise WeatherError("client_weather has too many points.")
    out: dict[LatLng, Forecast] = {}
    for p, h in zip(pts, hourly):
        if not (isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) for v in p)):
            raise WeatherError("client_weather points must be [lat, lng].")
        if not isinstance(h, dict) or not isinstance(h.get("time"), list):
            raise WeatherError("client_weather hourly entries need a 'time' list.")
        times = h["time"][:CLIENT_MAX_HOURS]
        if not all(isinstance(t, str) and len(t) == 16 for t in times):
            raise WeatherError("client_weather times must look like 2026-11-21T14:00.")
        cols: dict[str, list] = {}
        for name in HOURLY_VARS:
            series = h.get(name) or []
            if not isinstance(series, list):
                raise WeatherError(f"client_weather '{name}' must be a list.")
            cleaned = []
            for v in series[: len(times)]:
                if v is None:
                    cleaned.append(None)
                elif isinstance(v, (int, float)) and math.isfinite(v) and -100 <= v <= 1000:
                    cleaned.append(float(v))
                else:
                    raise WeatherError(f"client_weather '{name}' has an invalid value.")
            cols[name] = cleaned
        out[round_point((float(p[0]), float(p[1])))] = Forecast(times, cols)
    return out


def lookup(forecasts: dict[LatLng, Forecast], point: LatLng, when: datetime) -> HourlyWeather | None:
    f = forecasts.get(round_point(point))
    return f.at(when) if f else None
