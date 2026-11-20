"""Route options from the public OSRM server.

OSRM's `alternatives=3` often returns only one or two routes for long trips. When that
happens we ask for extra routes forced through via-points offset to either side of the
fastest route's midpoint, then keep candidates that are genuinely different (low overlap)
and not absurdly slower. This always tries to give the driver three real choices.
"""
from __future__ import annotations

import logging
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from django.conf import settings

from .geo import (
    METERS_PER_MILE,
    LatLng,
    TimedPolyline,
    bearing_deg,
    build_timed_polyline,
    destination_point,
    haversine_mi,
    resample,
)
from .http import UpstreamError, get_json

logger = logging.getLogger(__name__)

MAX_DURATION_RATIO = 1.6  # drop alternatives more than 60% slower than the fastest
MAX_OVERLAP = 0.80  # drop alternatives sharing more than 80% of road with an existing one
VIA_OFFSETS = ((0.5, 0.15), (0.5, -0.15), (0.5, 0.28), (0.5, -0.28), (0.35, 0.2), (0.65, -0.2))
TARGET_ROUTES = 3


class RoutingError(Exception):
    pass


@dataclass
class RouteOption:
    line: TimedPolyline
    distance_mi: float
    duration_s: float
    summary: str  # main roads, e.g. "US 287, I 25"
    source: str  # "osrm" or "via-point"


def _osrm(points: list[LatLng], alternatives: bool) -> list[dict]:
    coords = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)
    params = {
        "overview": "full",
        "geometries": "geojson",
        "annotations": "duration",
        "steps": "true",  # needed for the leg summaries (main road names)
        "alternatives": "3" if alternatives else "false",
    }
    data = get_json("osrm", f"{settings.OSRM_URL}/route/v1/driving/{coords}", params, ttl=60 * 60 * 24)
    if data.get("code") != "Ok":
        raise RoutingError(data.get("message") or f"Routing failed ({data.get('code')}).")
    # Drop the bulky turn-by-turn steps right away; we only keep what we use.
    for r in data["routes"]:
        for leg in r["legs"]:
            leg.pop("steps", None)
    return data["routes"]


SPUR_STEP_MI = 0.25
SPUR_TOLERANCE_MI = 0.3
SPUR_GAP_MI = 2.0
SPUR_MAX_MI = 60.0


def trim_spur(line: TimedPolyline, via_index: int) -> TimedPolyline:
    """Remove an out-and-back detour around a forced via-point.

    If the nearest road to a via-point is a side road, OSRM drives in and back out the same
    way (sometimes with a small loop to turn around). Walking backwards from the via-point,
    every inbound point then has an outbound point on top of it. The furthest such pair
    marks the spur's entrance; that stretch (and its drive time) is cut out.
    """
    via_mile = line.miles[via_index]
    reach = min(via_mile, line.total_miles - via_mile, SPUR_MAX_MI)
    steps = int(reach / SPUR_STEP_MI)
    if steps < 2:
        return line
    fwd = [(via_mile + k * SPUR_STEP_MI, line.locate(via_mile + k * SPUR_STEP_MI)[0]) for k in range(steps + 1)]
    cut: tuple[float, float] | None = None
    misses = 0
    for k in range(1, steps + 1):
        m_back = via_mile - k * SPUR_STEP_MI
        p = line.locate(m_back)[0]
        dist, m_fwd = min((haversine_mi(p, q), m) for m, q in fwd)
        if dist <= SPUR_TOLERANCE_MI:
            cut, misses = (m_back, m_fwd), 0
        else:
            misses += 1
            if misses * SPUR_STEP_MI >= SPUR_GAP_MI:
                break
    if cut is None or cut[1] - cut[0] < 1.0:
        return line
    i = max(0, bisect_right(line.miles, cut[0]) - 1)
    j = min(len(line.miles) - 1, bisect_left(line.miles, cut[1]))
    coords = line.coords[: i + 1] + line.coords[j:]
    secs = [b - a for a, b in zip(line.seconds, line.seconds[1:])]
    bridge_mi = haversine_mi(line.coords[i], line.coords[j])
    seg = secs[:i] + [bridge_mi / 45 * 3600] + secs[j:]
    removed = (line.miles[j] - line.miles[i]) - bridge_mi
    return build_timed_polyline(coords, seg, line.total_miles - removed, sum(seg))


def _to_option(route: dict, source: str) -> RouteOption:
    coords = [(lat, lng) for lng, lat in route["geometry"]["coordinates"]]
    seg_seconds: list[float] = []
    via_indices: list[int] = []
    for k, leg in enumerate(route["legs"]):
        seg_seconds.extend(leg.get("annotation", {}).get("duration", []))
        if k < len(route["legs"]) - 1:
            via_indices.append(len(seg_seconds))  # vertex index of the via-point
    distance_mi = route["distance"] / METERS_PER_MILE
    line = build_timed_polyline(coords, seg_seconds or None, distance_mi, route["duration"])
    for idx in reversed(via_indices):
        if idx < len(line.coords):
            line = trim_spur(line, idx)
    names: list[str] = []
    for leg in route["legs"]:
        for part in (leg.get("summary") or "").split(","):
            part = part.strip()
            if part and part not in names:
                names.append(part)
    return RouteOption(line, line.total_miles, line.total_seconds, ", ".join(names[:3]), source)


class _CellIndex:
    """Coarse spatial hash of a route for fast 'how much road is shared' checks."""

    CELL = 0.02  # degrees, roughly 1.4 mi

    def __init__(self, line: TimedPolyline):
        self.cells = {self._key(p) for p in resample(line, 0.5)}

    def _key(self, p: LatLng) -> tuple[int, int]:
        return int(p[0] // self.CELL), int(p[1] // self.CELL)

    def near(self, p: LatLng) -> bool:
        y, x = self._key(p)
        return any((y + dy, x + dx) in self.cells for dy in (-1, 0, 1) for dx in (-1, 0, 1))


def overlap_ratio(candidate: TimedPolyline, existing: TimedPolyline) -> float:
    index = _CellIndex(existing)
    samples = resample(candidate, 2.0)
    return sum(index.near(p) for p in samples) / len(samples)


def _is_distinct(candidate: RouteOption, chosen: list[RouteOption]) -> bool:
    fastest = min(o.duration_s for o in chosen)
    if candidate.duration_s > fastest * MAX_DURATION_RATIO:
        return False
    return all(overlap_ratio(candidate.line, o.line) <= MAX_OVERLAP for o in chosen)


def get_route_options(origin: LatLng, destination: LatLng) -> list[RouteOption]:
    if haversine_mi(origin, destination) < 1:
        raise RoutingError("Origin and destination are too close together.")
    try:
        routes = _osrm([origin, destination], alternatives=True)
    except UpstreamError as exc:
        raise RoutingError(f"Routing service unavailable ({exc}).") from exc
    if not routes:
        raise RoutingError("No drivable route found between those places.")

    chosen: list[RouteOption] = [_to_option(routes[0], "osrm")]
    for r in routes[1:]:
        opt = _to_option(r, "osrm")
        if len(chosen) < TARGET_ROUTES and _is_distinct(opt, chosen):
            chosen.append(opt)

    if len(chosen) < TARGET_ROUTES:
        base = chosen[0].line
        straight = haversine_mi(origin, destination)
        heading = bearing_deg(origin, destination)
        for frac, offset in VIA_OFFSETS:
            if len(chosen) >= TARGET_ROUTES:
                break
            anchor, _ = base.locate(base.total_miles * frac)
            side = heading + (90 if offset > 0 else -90)
            via = destination_point(anchor, side, abs(offset) * straight)
            try:
                extra = _osrm([origin, via, destination], alternatives=False)
            except (UpstreamError, RoutingError) as exc:
                logger.info("Via-point alternative failed: %s", exc)
                continue
            opt = _to_option(extra[0], "via-point")
            if _is_distinct(opt, chosen):
                chosen.append(opt)

    chosen.sort(key=lambda o: o.duration_s)
    return chosen
