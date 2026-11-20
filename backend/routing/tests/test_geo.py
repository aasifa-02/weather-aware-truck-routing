import pytest

from routing.services.geo import build_timed_polyline, checkpoint_miles, haversine_mi, simplify


def test_haversine_known_distance():
    # Dallas -> Denver great-circle distance is ~663 miles.
    assert haversine_mi((32.7767, -96.797), (39.7392, -104.9903)) == pytest.approx(663, abs=5)


@pytest.mark.parametrize(
    "total, interval, expected",
    [
        (100, 25, [0, 25, 50, 75, 100]),
        (110, 25, [0, 25, 50, 75, 100, 110]),
        (102, 25, [0, 25, 50, 75, 102]),  # tiny tail merged into the destination
        (8, 10, [0, 8]),
    ],
)
def test_checkpoint_miles(total, interval, expected):
    assert checkpoint_miles(total, interval) == pytest.approx(expected)


def test_timed_polyline_uses_segment_durations():
    coords = [(0.0, 0.0), (0.0, 1.0), (0.0, 2.0)]  # two equal-length segments
    line = build_timed_polyline(coords, [100, 300], total_miles=None, total_seconds=400)
    pos, secs = line.locate(line.total_miles * 0.75)
    assert pos[1] == pytest.approx(1.5)
    assert secs == pytest.approx(100 + 150)  # slow second segment


def test_timed_polyline_falls_back_to_constant_speed():
    coords = [(0.0, 0.0), (0.0, 1.0), (0.0, 2.0)]
    line = build_timed_polyline(coords, None, total_miles=100, total_seconds=3600)
    assert line.total_miles == pytest.approx(100)
    assert line.locate(50)[1] == pytest.approx(1800)


def test_simplify_keeps_endpoints_and_corners():
    pts = [(0, 0), (0, 0.5), (0, 1), (1, 1)]
    assert simplify(pts, 0.01) == [(0, 0), (0, 1), (1, 1)]


def test_trim_spur_removes_out_and_back_detour():
    from routing.services.routing_api import trim_spur

    # East along the equator, a 0.1-degree (~6.9 mi) spur north and back, then on east.
    out = [(0.0, x / 100) for x in range(0, 101)]  # lng 0 -> 1
    spur_in = [(y / 100, 1.0) for y in range(1, 11)]  # up to lat 0.1 (the via-point)
    spur_out = [(y / 100, 1.0) for y in range(9, -1, -1)]  # back down
    on = [(0.0, 1 + x / 100) for x in range(1, 101)]
    coords = out + spur_in + spur_out + on
    line = build_timed_polyline(coords, [10.0] * (len(coords) - 1))
    via_index = len(out) + len(spur_in) - 1
    trimmed = trim_spur(line, via_index)
    straight = haversine_mi((0, 0), (0, 2))
    assert trimmed.total_miles == pytest.approx(straight, rel=0.01)
    assert trimmed.total_seconds < line.total_seconds - 100  # spur drive time removed too


def test_trim_spur_leaves_normal_route_alone():
    from routing.services.routing_api import trim_spur

    coords = [(0.0, x / 100) for x in range(0, 201)]
    line = build_timed_polyline(coords, [10.0] * 200)
    assert trim_spur(line, 100).total_miles == pytest.approx(line.total_miles)
