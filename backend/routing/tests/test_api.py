"""API tests with upstream services mocked (no network)."""
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from routing.services import geocoding, routing_api, weather
from routing.services.geo import build_timed_polyline


@pytest.fixture(autouse=True)
def _clear_cache(settings, tmp_path):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()
    yield
    cache.clear()


def _route(lng_offset: float, duration: float) -> routing_api.RouteOption:
    coords = [(32.0 + k * 0.5, -97.0 + lng_offset) for k in range(10)]
    line = build_timed_polyline(coords, None, total_miles=None, total_seconds=duration)
    return routing_api.RouteOption(line, line.total_miles, duration, f"Road {lng_offset}", "osrm")


class _FakeForecast:
    def __init__(self, wind):
        self.wind = wind

    def at(self, when):
        return weather.HourlyWeather(self.wind, self.wind, 0.0, 0.0, 5.0, 3)


def _fake_lookup(forecasts, point, when):
    # Route at lng -97.0 gets strong wind; the others are calm.
    wind = 50.0 if abs(point[1] + 97.0) < 0.01 else 10.0
    return _FakeForecast(wind).at(when)


@mock.patch.object(weather, "lookup", side_effect=_fake_lookup)
@mock.patch.object(weather, "fetch_forecasts", return_value=({}, "open-meteo"))
@mock.patch.object(routing_api, "get_route_options")
@mock.patch.object(geocoding, "geocode")
def test_plan_recommends_safest_route(m_geo, m_routes, _m_fetch, _m_lookup):
    m_geo.side_effect = lambda q: geocoding.Place(q, q, 32.0, -97.0)
    m_routes.return_value = [_route(0.0, 30_000), _route(1.0, 33_000), _route(2.0, 36_000)]
    departure = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    resp = APIClient().post(
        "/api/plan/",
        {"origin": "A town", "destination": "B town", "departure": departure, "load_lb": 35_000, "interval_mi": 25},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert len(body["routes"]) == 3
    # Fastest route has 50 mph wind with >30k lb -> No Travel, so the calm 2nd route wins.
    fastest = next(r for r in body["routes"] if r["duration_s"] == 30_000)
    assert fastest["summary"]["no_travel"] is True
    rec = next(r for r in body["routes"] if r["recommended"])
    assert rec["duration_s"] == 33_000
    assert rec["rank"] == 1 and rec["recommendation_reason"]
    cp = rec["checkpoints"][1]
    assert cp["mile"] == 25 and cp["risk"] == "low" and cp["eta"] > departure[:16]


def test_plan_validation_errors():
    resp = APIClient().post("/api/plan/", {"origin": "X", "destination": "X", "load_lb": -5}, format="json")
    assert resp.status_code == 400


def test_heatmap_unknown_plan():
    assert APIClient().get("/api/heatmap/nope/").status_code == 404
