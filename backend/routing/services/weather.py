"""Hourly forecasts from Open-Meteo (free, no key), batched and cached.

Units are requested in metric and converted here so the conversions are explicit:
    wind_speed_10m / wind_gusts_10m  -> mph (Open-Meteo `wind_speed_unit=mph`)
    rain + showers  (mm per hour)    -> inches per hour (/ 25.4)
    snowfall        (cm per hour)    -> inches per hour (/ 2.54)
Hourly values are totals for the preceding hour, i.e. already a per-hour rate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from django.conf import settings

from .geo import LatLng
from .http import UpstreamError, get_json

HOURLY_VARS = ("wind_speed_10m", "wind_gusts_10m", "rain", "showers", "snowfall", "temperature_2m", "weather_code")
BATCH_SIZE = 100  # locations per Open-Meteo request
MAX_FORECAST_DAYS = 16
MM_PER_IN = 25.4
CM_PER_IN = 2.54
COORD_DECIMALS = 2  # ~1 km; Open-Meteo models are coarser, so rounding improves cache hits


class WeatherError(Exception):
    pass


@dataclass(frozen=True)
class HourlyWeather:
    wind_mph: float
    gust_mph: float
    rain_in_hr: float
    snow_in_hr: float
    temp_c: float | None
    weather_code: int | None

    def as_dict(self) -> dict:
        return {
            "wind_mph": round(self.wind_mph, 1),
            "gust_mph": round(self.gust_mph, 1),
            "rain_in_hr": round(self.rain_in_hr, 3),
            "snow_in_hr": round(self.snow_in_hr, 3),
            "temp_c": None if self.temp_c is None else round(self.temp_c, 1),
            "weather_code": self.weather_code,
        }


class Forecast:
    """Hourly series for one location; look up by UTC hour."""

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

    def at_key(self, key: str) -> HourlyWeather | None:
        i = self._index.get(key)
        if i is None:
            return None
        h = self._hourly

        def val(name: str) -> float:
            v = h.get(name, [None] * (i + 1))[i]
            return float(v) if v is not None else 0.0

        if h.get("wind_speed_10m", [None])[i] is None:
            return None  # hour exists but model has no data
        code = h.get("weather_code", [None] * (i + 1))[i]
        temp = h.get("temperature_2m", [None] * (i + 1))[i]
        return HourlyWeather(
            wind_mph=val("wind_speed_10m"),
            gust_mph=val("wind_gusts_10m"),
            rain_in_hr=(val("rain") + val("showers")) / MM_PER_IN,
            snow_in_hr=val("snowfall") / CM_PER_IN,
            temp_c=None if temp is None else float(temp),
            weather_code=None if code is None else int(code),
        )


def _round(p: LatLng) -> LatLng:
    return round(p[0], COORD_DECIMALS), round(p[1], COORD_DECIMALS)


def forecast_window_ok(start: datetime, end: datetime) -> bool:
    now = datetime.now(timezone.utc)
    return start >= now - timedelta(days=2) and end <= now + timedelta(days=MAX_FORECAST_DAYS - 1)


def fetch_forecasts(points: Iterable[LatLng], start: datetime, end: datetime) -> dict[LatLng, Forecast]:
    """Forecasts for all points covering [start, end] (UTC). Keys are rounded coordinates.

    Duplicate points are fetched once; requests are batched (many coordinates per call)
    and cached per batch for an hour.
    """
    unique = sorted({_round(p) for p in points})
    if not unique:
        return {}
    start_h = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    end_h = end.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
    out: dict[LatLng, Forecast] = {}
    for i in range(0, len(unique), BATCH_SIZE):
        batch = unique[i : i + BATCH_SIZE]
        params = {
            "latitude": ",".join(f"{lat:.2f}" for lat, _ in batch),
            "longitude": ",".join(f"{lng:.2f}" for _, lng in batch),
            "hourly": ",".join(HOURLY_VARS),
            "wind_speed_unit": "mph",
            "timezone": "GMT",
            "start_hour": start_h.strftime("%Y-%m-%dT%H:%M"),
            "end_hour": end_h.strftime("%Y-%m-%dT%H:%M"),
        }
        try:
            data = get_json("open-meteo", settings.OPEN_METEO_URL, params, ttl=60 * 60)
        except UpstreamError as exc:
            raise WeatherError(f"Weather service unavailable ({exc}).") from exc
        items = data if isinstance(data, list) else [data]
        if len(items) != len(batch):
            raise WeatherError("Weather service returned an unexpected number of locations.")
        for p, item in zip(batch, items):
            hourly = item.get("hourly") or {}
            out[p] = Forecast(hourly.get("time", []), hourly)
    return out


def lookup(forecasts: dict[LatLng, Forecast], point: LatLng, when: datetime) -> HourlyWeather | None:
    f = forecasts.get(_round(point))
    return f.at(when) if f else None
