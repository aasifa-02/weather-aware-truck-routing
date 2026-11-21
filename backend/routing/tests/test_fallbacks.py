"""Rate limiting, fallback providers and the browser-weather path (no network)."""
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
import requests
from django.core.cache import cache
from rest_framework.test import APIClient

from routing.services import geocoding, http, routing_api, weather
from routing.services.geo import build_timed_polyline
from routing.services.risk import Risk, classify_conditions


@pytest.fixture(autouse=True)
def _locmem_cache(settings):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()
    yield
    cache.clear()


def _resp(status, body=None, headers=None):
    r = mock.Mock(spec=requests.Response)
    r.status_code = status
    r.headers = headers or {}
    r.json.return_value = body if body is not None else {}
    r.text = str(body)
    return r


# ------------------------------------------------------------------ HTTP / Retry-After


class TestRetryAfter:
    def test_short_retry_after_is_honoured_then_succeeds(self):
        calls = [_resp(429, headers={"Retry-After": "0"}), _resp(200, {"ok": True})]
        with mock.patch.object(http._session, "get", side_effect=calls) as get, mock.patch.object(http.time, "sleep"):
            assert http.get_json("svc", "https://example.test/a") == {"ok": True}
        assert get.call_count == 2

    def test_long_retry_after_fails_fast(self):
        with mock.patch.object(http._session, "get", return_value=_resp(429, headers={"Retry-After": "3600"})) as get:
            with pytest.raises(http.RateLimited) as info:
                http.get_json("svc", "https://example.test/b")
        assert info.value.retry_after == 3600
        assert get.call_count == 1

    def test_errors_are_not_cached(self):
        with mock.patch.object(http._session, "get", side_effect=[_resp(500), _resp(500), _resp(500)]), \
                mock.patch.object(http.time, "sleep"):
            with pytest.raises(http.UpstreamError):
                http.get_json("svc", "https://example.test/c")
        with mock.patch.object(http._session, "get", return_value=_resp(200, {"v": 1})):
            assert http.get_json("svc", "https://example.test/c") == {"v": 1}


# ------------------------------------------------------------------ MET Norway conversion


def _met_series(start: datetime, hours: int, wind_ms=10.0, temp=5.0, precip_mm=2.0):
    out = []
    for h in range(hours):
        t = start + timedelta(hours=h)
        out.append({
            "time": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "data": {
                "instant": {"details": {"wind_speed": wind_ms, "air_temperature": temp}},
                "next_1_hours": {"details": {"precipitation_amount": precip_mm}},
            },
        })
    return out


T0 = datetime(2026, 11, 21, 0, tzinfo=timezone.utc)


class TestMetConversion:
    def test_units_and_hour_alignment(self):
        fc = weather.met_to_forecast(_met_series(T0, 6), T0, T0 + timedelta(hours=5))
        wx = fc.at(T0 + timedelta(hours=2))
        assert wx.wind_mph == pytest.approx(22.37, abs=0.01)  # 10 m/s
        assert wx.rain_in_hr == pytest.approx(2.0 / 25.4)
        assert wx.snow_in_hr == 0 and wx.gust_mph is None
        # First hour has no "previous hour" precipitation in MET's data -> treated as 0.
        assert fc.at(T0).rain_in_hr == 0

    def test_cold_precip_becomes_snow_with_open_meteo_ratio(self):
        fc = weather.met_to_forecast(_met_series(T0, 4, temp=-3.0, precip_mm=10.0), T0, T0 + timedelta(hours=3))
        wx = fc.at(T0 + timedelta(hours=2))
        assert wx.rain_in_hr == 0
        assert wx.snow_in_hr == pytest.approx(7.0 / 2.54)  # 10 mm water -> 7 cm snow

    def test_mixed_zone_splits_linearly(self):
        fc = weather.met_to_forecast(_met_series(T0, 4, temp=1.0, precip_mm=4.0), T0, T0 + timedelta(hours=3))
        wx = fc.at(T0 + timedelta(hours=2))
        assert wx.rain_in_hr == pytest.approx(2.0 / 25.4)
        assert wx.snow_in_hr == pytest.approx(2.0 * 0.7 / 2.54)

    def test_six_hourly_steps_are_interpolated_and_spread(self):
        ts = []
        for k, wind in enumerate((10.0, 22.0)):
            t = T0 + timedelta(hours=6 * k)
            ts.append({
                "time": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "data": {
                    "instant": {"details": {"wind_speed": wind, "air_temperature": 10.0}},
                    "next_6_hours": {"details": {"precipitation_amount": 6.0}},
                },
            })
        fc = weather.met_to_forecast(ts, T0, T0 + timedelta(hours=6))
        wx = fc.at(T0 + timedelta(hours=3))
        assert wx.wind_mph == pytest.approx(16.0 * weather.MET_MS_TO_MPH)
        assert wx.rain_in_hr == pytest.approx(1.0 / 25.4)

    def test_same_weather_scores_identically_from_either_source(self):
        om = weather.Forecast(
            ["2026-11-21T02:00"],
            {"wind_speed_10m": [22.369], "wind_gusts_10m": [30.0], "rain": [2.0], "showers": [0.0],
             "snowfall": [0.0], "temperature_2m": [5.0], "weather_code": [61]},
        ).at_key("2026-11-21T02:00")
        met = weather.met_to_forecast(_met_series(T0, 4), T0, T0 + timedelta(hours=3)).at_key("2026-11-21T02:00")
        for load in (10_000, 45_000):
            a = classify_conditions(om.wind_mph, om.rain_in_hr, om.snow_in_hr, load)
            b = classify_conditions(met.wind_mph, met.rain_in_hr, met.snow_in_hr, load)
            assert a.risk == b.risk


# ------------------------------------------------------------------ provider chain


POINTS = [(40.0, -105.0), (41.0, -104.0)]
START = datetime.now(timezone.utc) + timedelta(hours=1)
END = START + timedelta(hours=6)


class TestProviderChain:
    def test_open_meteo_used_when_healthy(self):
        with mock.patch.object(weather, "_fetch_open_meteo", return_value={"x": 1}) as om, \
                mock.patch.object(weather, "_fetch_met_norway") as met:
            data, source = weather.fetch_forecasts(POINTS, START, END)
        assert source == weather.SOURCE_OPEN_METEO and om.called and not met.called

    def test_rate_limit_falls_back_to_met_and_cools_down(self):
        with mock.patch.object(weather, "_fetch_open_meteo", side_effect=http.RateLimited("open-meteo", 1200)) as om, \
                mock.patch.object(weather, "_fetch_met_norway", return_value={"y": 2}):
            assert weather.fetch_forecasts(POINTS, START, END)[1] == weather.SOURCE_MET_NORWAY
            # Second call skips Open-Meteo entirely while it is cooling down.
            assert weather.fetch_forecasts(POINTS, START, END)[1] == weather.SOURCE_MET_NORWAY
        assert om.call_count == 1

    def test_everything_down_raises_with_browser_request(self):
        with mock.patch.object(weather, "_fetch_open_meteo", side_effect=http.UpstreamError("open-meteo", "HTTP 502")), \
                mock.patch.object(weather, "_fetch_met_norway", side_effect=http.RateLimited("met-norway", None)):
            with pytest.raises(weather.WeatherUnavailable) as info:
                weather.fetch_forecasts(POINTS, START, END)
        req = info.value.request
        assert req["points"] == [[40.0, -105.0], [41.0, -104.0]]
        assert req["params"]["hourly"].startswith("wind_speed_10m")
        assert req["params"]["wind_speed_unit"] == "mph"

    def test_met_groups_points_to_respect_request_budget(self):
        pts = [(40 + i * 0.01, -105.0) for i in range(300)]  # a dense line of points
        groups = weather._coarse_groups(pts, 20)
        assert len(groups) <= 20
        assert sum(len(v) for v in groups.values()) == 300


# ------------------------------------------------------------------ client weather validation


class TestClientWeather:
    def test_valid_payload(self):
        payload = {"points": [[40.0, -105.0]], "hourly": [{"time": ["2026-11-21T02:00"], "wind_speed_10m": [50],
                   "rain": [0], "showers": [0], "snowfall": [0]}]}
        fc = weather.forecasts_from_client(payload)
        assert fc[(40.0, -105.0)].at_key("2026-11-21T02:00").wind_mph == 50

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            {"points": [[1, 2]], "hourly": []},
            {"points": [["a", 2]], "hourly": [{"time": []}]},
            {"points": [[1, 2]], "hourly": [{"time": ["bad"]}]},
            {"points": [[1, 2]], "hourly": [{"time": ["2026-11-21T02:00"], "wind_speed_10m": ["x"]}]},
            {"points": [[1, 2]], "hourly": [{"time": ["2026-11-21T02:00"], "wind_speed_10m": [1e9]}]},
        ],
    )
    def test_rejects_bad_payloads(self, payload):
        with pytest.raises(weather.WeatherError):
            weather.forecasts_from_client(payload)


# ------------------------------------------------------------------ API: browser fallback round trip


def _route(lng, duration):
    coords = [(32.0 + k * 0.5, lng) for k in range(10)]
    line = build_timed_polyline(coords, None, total_miles=None, total_seconds=duration)
    return routing_api.RouteOption(line, line.total_miles, duration, "Road", "osrm")


@mock.patch.object(routing_api, "get_route_options")
@mock.patch.object(geocoding, "geocode")
def test_plan_falls_back_to_browser_weather(m_geo, m_routes):
    m_geo.side_effect = lambda q: geocoding.Place(q, q, 32.0, -97.0)
    m_routes.return_value = [_route(-97.0, 30_000), _route(-96.0, 33_000), _route(-95.0, 36_000)]
    departure = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    body = {"origin": "A", "destination": "B", "departure": departure.isoformat(), "load_lb": 35_000, "interval_mi": 25}
    client = APIClient()

    with mock.patch.object(weather, "_fetch_open_meteo", side_effect=http.RateLimited("open-meteo", None)), \
            mock.patch.object(weather, "_fetch_met_norway", side_effect=http.UpstreamError("met-norway", "HTTP 503")):
        first = client.post("/api/plan/", body, format="json")
    assert first.status_code == 503
    req = first.json()["weather_request"]
    assert first.json()["code"] == "weather_unavailable" and len(req["points"]) > 10

    # The browser fetches Open-Meteo itself: 50 mph wind everywhere -> No Travel at 35,000 lb.
    hours = [(departure + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00") for h in range(-1, 14)]
    hourly = [{"time": hours, "wind_speed_10m": [50.0] * len(hours), "rain": [0.0] * len(hours),
               "showers": [0.0] * len(hours), "snowfall": [0.0] * len(hours)} for _ in req["points"]]
    second = client.post("/api/plan/", {**body, "client_weather": {"points": req["points"], "hourly": hourly}}, format="json")
    assert second.status_code == 200, second.content
    plan = second.json()
    assert plan["weather_source"] == weather.SOURCE_CLIENT
    assert all(r["summary"]["no_travel"] for r in plan["routes"])
    assert plan["routes"][0]["checkpoints"][1]["risk"] == "no_travel"
    assert Risk.NO_TRAVEL == classify_conditions(50, 0, 0, 35_000).risk


# ------------------------------------------------------------------ routing fallback


def test_osrm_falls_back_to_mirror_on_rate_limit():
    ok = {"code": "Ok", "routes": []}
    with mock.patch.object(routing_api, "get_json", side_effect=[http.RateLimited("osrm", None), ok]) as gj:
        assert routing_api._osrm([(40.0, -105.0), (41.0, -104.0)], alternatives=True) == []
    assert "routing.openstreetmap.de" in gj.call_args_list[1].args[1]


def test_osrm_no_route_is_final():
    with mock.patch.object(routing_api, "get_json", side_effect=http.UpstreamError("osrm", "HTTP 400: Impossible route")) as gj:
        with pytest.raises(routing_api.RoutingError):
            routing_api._osrm([(40.0, -105.0), (41.0, -104.0)], alternatives=True)
    assert gj.call_count == 1


def test_geocoding_falls_back_to_photon():
    photon = {"features": [{"properties": {"name": "Denver", "state": "Colorado", "country": "United States"},
                            "geometry": {"coordinates": [-104.99, 39.74]}}]}
    with mock.patch.object(geocoding, "get_json", side_effect=[http.RateLimited("nominatim", None), photon]):
        place = geocoding.geocode("Denver, CO")
    assert (place.lat, place.lng) == (39.74, -104.99) and place.name.startswith("Denver")
