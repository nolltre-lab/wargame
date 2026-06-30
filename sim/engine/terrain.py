"""
General-purpose land/water terrain queries, backed by a real global coastline
dataset (Natural Earth 10m land polygons) rather than hand-authored regional
heuristics. Works for any theater on the map, not just the Baltic.

Ground units must route over land; naval units must route over water. When a
straight-line route between two valid points crosses the wrong domain, we
fall back to grid-based A* pathfinding over a local patch of coastline to
find a route that stays in-domain.
"""
from __future__ import annotations

import heapq
import json
import os
from functools import lru_cache
from typing import List, Tuple

from shapely.geometry import Point, shape
from shapely.prepared import prep
from shapely.strtree import STRtree

_DATA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "coastline", "ne_10m_land.geojson"
)

_polygons: list = []
_prepared: list = []
_tree: STRtree | None = None


def _load() -> None:
    global _tree, _polygons, _prepared
    if _tree is not None:
        return
    with open(_DATA_PATH) as f:
        data = json.load(f)
    polys = []
    for feat in data["features"]:
        geom = shape(feat["geometry"])
        if geom.geom_type == "Polygon":
            polys.append(geom)
        elif geom.geom_type == "MultiPolygon":
            polys.extend(list(geom.geoms))
    _polygons = polys
    _prepared = [prep(p) for p in polys]
    _tree = STRtree(polys)


def is_land(lat: float, lon: float) -> bool:
    _load()
    pt = Point(lon, lat)
    for idx in _tree.query(pt):
        if _prepared[idx].contains(pt):
            return True
    return False


def is_water(lat: float, lon: float) -> bool:
    return not is_land(lat, lon)


def _route_crosses(lat1, lon1, lat2, lon2, want_land: bool, samples: int) -> bool:
    for i in range(1, samples):
        t = i / samples
        lat = lat1 + t * (lat2 - lat1)
        lon = lon1 + t * (lon2 - lon1)
        if is_land(lat, lon) != want_land:
            return True
    return False


def route_crosses_water(lat1, lon1, lat2, lon2, samples: int = 20) -> bool:
    """True if a straight line between two LAND points dips into water."""
    return _route_crosses(lat1, lon1, lat2, lon2, want_land=True, samples=samples)


def route_crosses_land(lat1, lon1, lat2, lon2, samples: int = 20) -> bool:
    """True if a straight line between two WATER points clips land."""
    return _route_crosses(lat1, lon1, lat2, lon2, want_land=False, samples=samples)


# ── Grid A* fallback router ────────────────────────────────────────────────────
#
# Only invoked when the direct line fails the domain check above (i.e. rarely —
# once per mission assignment, not per tick). Builds a coarse grid over the
# local area between start and destination and searches for an in-domain path.

_GRID_DEG = 0.04   # ~4.4 km north-south per cell
_PAD_DEG = 0.6      # padding around the start/dest bounding box


def find_route(
    lat1: float, lon1: float, lat2: float, lon2: float, domain: str
) -> List[Tuple[float, float]]:
    """
    Return waypoints (excluding the start, including the destination) routing
    from (lat1, lon1) to (lat2, lon2) while staying within `domain`
    ('land' for ground units, 'water' for naval units).
    Falls back to the direct route if no in-domain path is found.
    """
    want_land = domain == "land"
    crosses = route_crosses_water if want_land else route_crosses_land
    if not crosses(lat1, lon1, lat2, lon2):
        return [(lat2, lon2)]

    lat_min = min(lat1, lat2) - _PAD_DEG
    lat_max = max(lat1, lat2) + _PAD_DEG
    lon_min = min(lon1, lon2) - _PAD_DEG
    lon_max = max(lon1, lon2) + _PAD_DEG

    nx = max(2, int((lon_max - lon_min) / _GRID_DEG))
    ny = max(2, int((lat_max - lat_min) / _GRID_DEG))

    def cell_of(lat: float, lon: float) -> Tuple[int, int]:
        cx = int((lon - lon_min) / _GRID_DEG)
        cy = int((lat - lat_min) / _GRID_DEG)
        return max(0, min(nx - 1, cx)), max(0, min(ny - 1, cy))

    def cell_center(c: Tuple[int, int]) -> Tuple[float, float]:
        cx, cy = c
        return lat_min + (cy + 0.5) * _GRID_DEG, lon_min + (cx + 0.5) * _GRID_DEG

    @lru_cache(maxsize=None)
    def passable(c: Tuple[int, int]) -> bool:
        clat, clon = cell_center(c)
        return is_land(clat, clon) == want_land

    start = cell_of(lat1, lon1)
    goal = cell_of(lat2, lon2)

    def heuristic(c: Tuple[int, int]) -> float:
        clat, clon = cell_center(c)
        glat, glon = cell_center(goal)
        return ((clat - glat) ** 2 + (clon - glon) ** 2) ** 0.5

    neighbors = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    open_heap: list = [(heuristic(start), 0.0, start)]
    came_from: dict = {}
    g_score = {start: 0.0}
    closed: set = set()

    found = False
    while open_heap:
        _, g, current = heapq.heappop(open_heap)
        if current in closed:
            continue
        closed.add(current)
        if current == goal:
            found = True
            break
        for dx, dy in neighbors:
            nb = (current[0] + dx, current[1] + dy)
            if not (0 <= nb[0] < nx and 0 <= nb[1] < ny):
                continue
            if nb not in (start, goal) and not passable(nb):
                continue
            step = 1.41421356 if dx and dy else 1.0
            tentative = g + step
            if tentative < g_score.get(nb, float("inf")):
                g_score[nb] = tentative
                came_from[nb] = current
                heapq.heappush(open_heap, (tentative + heuristic(nb), tentative, nb))

    if not found:
        return [(lat2, lon2)]  # no in-domain path within the grid — best effort

    path_cells = [goal]
    node = goal
    while node in came_from:
        node = came_from[node]
        path_cells.append(node)
    path_cells.reverse()

    pts = [cell_center(c) for c in path_cells]
    waypoints = _simplify(pts)
    waypoints.append((lat2, lon2))
    return waypoints


def _simplify(pts: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Collapse straight runs, keeping only turning points."""
    if len(pts) <= 2:
        return list(pts)
    out = [pts[0]]
    prev_dir = None
    for i in range(1, len(pts) - 1):
        d = (pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        if prev_dir is None or abs(d[0] - prev_dir[0]) > 1e-9 or abs(d[1] - prev_dir[1]) > 1e-9:
            out.append(pts[i])
        prev_dir = d
    out.append(pts[-1])
    return out
