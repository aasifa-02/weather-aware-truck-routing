import pytest

from routing.services.geo import haversine_mi, mercator_y
from routing.services.heatmap import CORRIDOR_MI, build_grid


def test_grid_cells_stay_inside_corridor_and_bounds():
    route = [(32.78, -96.80), (35.2, -101.8), (39.74, -104.99)]
    bounds, nx, ny, cells = build_grid([route])
    assert 0 < len(cells) < nx * ny  # corridor mask removed some cells
    for i, j, lat, lng in cells:
        assert 0 <= i < nx and 0 <= j < ny
        assert bounds["south"] < lat < bounds["north"] and bounds["west"] < lng < bounds["east"]
        # densified route is within CORRIDOR_MI of every kept cell
        assert min(haversine_mi((lat, lng), p) for p in _dense(route)) <= CORRIDOR_MI + 1


def test_grid_is_square_in_mercator_space():
    bounds, nx, ny, _ = build_grid([[(30.0, -100.0), (40.0, -90.0)]])
    import math

    cell_x = math.radians(bounds["east"] - bounds["west"]) / nx
    cell_y = (mercator_y(bounds["north"]) - mercator_y(bounds["south"])) / ny
    assert cell_x == pytest.approx(cell_y, rel=1e-6)


def _dense(route, step=0.05):
    out = []
    for a, b in zip(route, route[1:]):
        n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) / step) + 1
        out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n + 1)]
    return out
