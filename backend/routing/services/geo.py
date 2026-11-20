"""Small geometry helpers (lat/lng in degrees, distances in miles)."""
from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass
from typing import Sequence

EARTH_RADIUS_MI = 3958.7613
METERS_PER_MILE = 1609.344

LatLng = tuple[float, float]


def haversine_mi(a: LatLng, b: LatLng) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(min(1.0, math.sqrt(h)))


def cumulative(values: Sequence[float]) -> list[float]:
    out, total = [0.0], 0.0
    for v in values:
        total += v
        out.append(total)
    return out


def destination_point(origin: LatLng, bearing_deg: float, distance_mi: float) -> LatLng:
    lat1, lon1 = map(math.radians, origin)
    brg = math.radians(bearing_deg)
    d = distance_mi / EARTH_RADIUS_MI
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(brg))
    lon2 = lon1 + math.atan2(
        math.sin(brg) * math.sin(d) * math.cos(lat1), math.cos(d) - math.sin(lat1) * math.sin(lat2)
    )
    return math.degrees(lat2), (math.degrees(lon2) + 540) % 360 - 180


def bearing_deg(a: LatLng, b: LatLng) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


@dataclass
class TimedPolyline:
    """A polyline with cumulative distance (mi) and elapsed time (s) at every vertex."""

    coords: list[LatLng]
    miles: list[float]
    seconds: list[float]

    @property
    def total_miles(self) -> float:
        return self.miles[-1]

    @property
    def total_seconds(self) -> float:
        return self.seconds[-1]

    def locate(self, mile: float) -> tuple[LatLng, float]:
        """Interpolated position and elapsed seconds at `mile` along the line."""
        mile = min(max(mile, 0.0), self.total_miles)
        i = bisect_left(self.miles, mile)
        if i <= 0:
            return self.coords[0], self.seconds[0]
        if i >= len(self.miles):
            return self.coords[-1], self.seconds[-1]
        m0, m1 = self.miles[i - 1], self.miles[i]
        t = 0.0 if m1 == m0 else (mile - m0) / (m1 - m0)
        (la0, lo0), (la1, lo1) = self.coords[i - 1], self.coords[i]
        pos = (la0 + (la1 - la0) * t, lo0 + (lo1 - lo0) * t)
        sec = self.seconds[i - 1] + (self.seconds[i] - self.seconds[i - 1]) * t
        return pos, sec


def build_timed_polyline(
    coords: Sequence[LatLng],
    segment_seconds: Sequence[float] | None,
    total_miles: float | None = None,
    total_seconds: float | None = None,
) -> TimedPolyline:
    """Attach distance and time to each vertex.

    `segment_seconds` are per-segment travel times from the routing engine (OSRM
    annotations). If they're missing or don't line up with the geometry, time is spread
    proportionally to distance (constant average speed), scaled to `total_seconds`.
    Distances are scaled to the routing engine's reported length when given, so
    checkpoint mileage matches the summary the driver sees.
    """
    coords = list(coords)
    seg_mi = [haversine_mi(coords[i], coords[i + 1]) for i in range(len(coords) - 1)]
    miles = cumulative(seg_mi)
    if total_miles and miles[-1] > 0:
        k = total_miles / miles[-1]
        miles = [m * k for m in miles]

    if segment_seconds is not None and len(segment_seconds) == len(seg_mi):
        seconds = cumulative(segment_seconds)
        if total_seconds and seconds[-1] > 0:
            k = total_seconds / seconds[-1]
            seconds = [s * k for s in seconds]
    else:
        total = total_seconds or 0.0
        seconds = [total * (m / miles[-1]) if miles[-1] else 0.0 for m in miles]
    return TimedPolyline(coords, miles, seconds)


def checkpoint_miles(total_miles: float, interval_mi: float) -> list[float]:
    """0, interval, 2*interval, ..., plus the destination."""
    if total_miles <= 0:
        return [0.0]
    out = []
    m = 0.0
    while m < total_miles - 1e-6:
        out.append(m)
        m += interval_mi
    # Keep the destination as its own checkpoint unless the last sample is basically on it.
    if total_miles - out[-1] < interval_mi * 0.2 and len(out) > 1:
        out[-1] = total_miles
    else:
        out.append(total_miles)
    return out


def simplify(coords: Sequence[LatLng], tolerance_deg: float) -> list[LatLng]:
    """Ramer-Douglas-Peucker (iterative). Used only to shrink geometry sent to the browser."""
    n = len(coords)
    if n < 3:
        return list(coords)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    tol2 = tolerance_deg * tolerance_deg
    while stack:
        start, end = stack.pop()
        (ay, ax), (by, bx) = coords[start], coords[end]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        best_i, best_d = -1, tol2
        for i in range(start + 1, end):
            py, px = coords[i]
            if seg2 == 0:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
                d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d > best_d:
                best_i, best_d = i, d
        if best_i != -1:
            keep[best_i] = True
            stack.append((start, best_i))
            stack.append((best_i, end))
    return [c for c, k in zip(coords, keep) if k]


def resample(line: TimedPolyline, step_mi: float) -> list[LatLng]:
    """Evenly spaced points along a line (for corridor tests / overlap checks)."""
    pts = []
    m = 0.0
    while m <= line.total_miles:
        pts.append(line.locate(m)[0])
        m += step_mi
    pts.append(line.coords[-1])
    return pts


def mercator_y(lat: float) -> float:
    lat = max(min(lat, 85.0), -85.0)
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def inverse_mercator_y(y: float) -> float:
    return math.degrees(2 * math.atan(math.exp(y)) - math.pi / 2)
