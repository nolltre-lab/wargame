"""
General-purpose land/water terrain queries, backed by a real global coastline
dataset (Natural Earth 10m land polygons) rather than hand-authored regional
heuristics. Works for any theater on the map, not just the Baltic.

Ground units must route over land; naval units must route over water. When a
straight-line route between two valid points crosses the wrong domain, we
fall back to grid-based A* pathfinding over a local patch of coastline to
find a route that stays in-domain.

Air units fly direct by default. When neutral country airspace must be
respected, find_route_air() uses a coarser A* grid that treats neutral
country cells as obstacles (backed by Natural Earth 110m country polygons).
"""
from __future__ import annotations

import heapq
import json
import os
from functools import lru_cache
from typing import FrozenSet, List, Optional, Set, Tuple

from shapely.geometry import Point, shape
from shapely.prepared import prep
from shapely.strtree import STRtree

_DATA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "coastline", "ne_10m_land.geojson"
)

_polygons: list = []
_prepared: list = []
_tree: STRtree | None = None

# ── Country polygon data (Natural Earth 110m) ─────────────────────────────────

_COUNTRIES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "countries", "ne_10m_countries_baltic.geojson"
)

_c_polys: list = []       # flat list of Shapely Polygons (land + sea, used for routing)
_c_preps: list = []       # parallel: prepared versions
_c_names: list = []       # parallel: lowercase country name for each polygon
_c_sea_polys: list = []   # sea-zone-only polygons (for /territories display API)
_c_tree: STRtree | None = None
_c_loaded = False


def _load_countries() -> None:
    """
    Build per-country territory polygons from Natural Earth 10m country data.

    Algorithm (simple):
      1. Clip each country's 10m land polygon to the theater box.
      2. Buffer by 12nm (0.22°) and subtract all land → raw sea zone.
      3. Sequential claiming (smallest country first) resolves overlaps in
         narrow straits (Öresund, Gulf of Riga, Gulf of Finland eastern end).
      4. Discard sub-pixel fragments (< _MIN_AREA_DEG2).

    The 10m data has correct island attribution (Åland is part of Finland,
    etc.) so no island-hunting heuristics are needed.  Russia and Norway's
    global polygons are clipped to the theater before buffering.
    """
    global _c_polys, _c_preps, _c_names, _c_sea_polys, _c_tree, _c_loaded
    if _c_loaded:
        return
    _c_loaded = True
    if not os.path.exists(_COUNTRIES_PATH):
        return

    from shapely.ops import unary_union
    from shapely.geometry import box as shapely_box, MultiPolygon as MP

    with open(_COUNTRIES_PATH) as f:
        data = json.load(f)

    by_name: dict = {}
    for feat in data["features"]:
        name = feat.get("properties", {}).get("name", "").lower().strip()
        if not name:
            continue
        geom = shape(feat["geometry"])
        parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        by_name.setdefault(name, []).extend(parts)

    raw: dict = {name: unary_union(polys) for name, polys in by_name.items()}

    # Theater box — clips all computation to the Baltic region.
    # Russia and Norway have global polygons (-180→180° / Antarctic); clip first.
    theater = shapely_box(7.0, 50.0, 37.0, 71.0)
    _TERRITORY_BUFFER_DEG = 0.22
    _MIN_AREA_DEG2 = 0.005  # discard sub-pixel sea zone fragments

    raw_theater: dict = {}
    for n, land in raw.items():
        clipped = land.intersection(theater)
        if not clipped.is_empty:
            raw_theater[n] = clipped

    # Global 10m land (theater-clipped) used to exclude land from sea zones and
    # to prevent buffers for Belarus/Poland bleeding into Ukraine etc.
    _load()
    theater_land_list = []
    for p in _polygons:
        if p.envelope.intersects(theater):
            clipped = p.intersection(theater)
            if not clipped.is_empty:
                theater_land_list.append(clipped)
    global_land = unary_union(theater_land_list) if theater_land_list else unary_union(list(raw_theater.values()))

    # Smallest countries first: they claim narrow straits (Öresund, Gulf of
    # Riga) before larger neighbours.
    sorted_names = sorted(raw_theater.keys(), key=lambda n: raw_theater[n].area)

    def _polys_only(geom):
        if geom is None or geom.is_empty:
            return geom
        if geom.geom_type in ("Polygon", "MultiPolygon"):
            return geom
        parts = [g for g in geom.geoms
                 if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty]
        if not parts:
            return geom.__class__()
        return unary_union(parts)

    def _filter_tiny(geom):
        """Remove sub-pixel fragments (area < _MIN_AREA_DEG2)."""
        if geom is None or geom.is_empty:
            return geom
        if geom.geom_type == "Polygon":
            return geom if geom.area >= _MIN_AREA_DEG2 else geom.__class__()
        if geom.geom_type == "MultiPolygon":
            kept = [p for p in geom.geoms if p.area >= _MIN_AREA_DEG2]
            if not kept:
                return MP()
            return kept[0] if len(kept) == 1 else MP(kept)
        return geom

    claimed_sea = None

    for name in sorted_names:
        land_theater = raw_theater[name]

        # Simple algorithm: 12nm buffer directly from this country's theater-
        # clipped land polygon (now 10m data with correct island attribution,
        # e.g. Åland is already part of Finland's polygon).  No island-hunting
        # needed — the data is authoritative.
        sea_zone = land_theater.buffer(_TERRITORY_BUFFER_DEG).difference(global_land).intersection(theater)

        # Sequential claiming: overlapping zones in straits (Gulf of Finland,
        # Öresund) go to the earlier/smaller country.
        if claimed_sea is not None and not sea_zone.is_empty:
            sea_zone = sea_zone.difference(claimed_sea)

        if not sea_zone.is_empty:
            sea_zone = _filter_tiny(
                _polys_only(sea_zone.simplify(0.01, preserve_topology=True))
            )
            if sea_zone is not None and not sea_zone.is_empty:
                claimed_sea = sea_zone if claimed_sea is None else claimed_sea.union(sea_zone)
            else:
                sea_zone = MP()
        sea_zone_simple = sea_zone if (sea_zone is not None and not sea_zone.is_empty) else MP()

        territory = land_theater.union(sea_zone_simple) if not sea_zone_simple.is_empty else land_theater
        territory = _polys_only(territory.simplify(0.01, preserve_topology=True))

        _c_polys.append(territory)
        _c_preps.append(prep(territory))
        _c_names.append(name)
        _c_sea_polys.append(sea_zone_simple)

    if _c_polys:
        _c_tree = STRtree(_c_polys)


def which_country(lat: float, lon: float) -> Optional[str]:
    """Return the lowercase country name for the point, or None."""
    _load_countries()
    if _c_tree is None:
        return None
    pt = Point(lon, lat)
    for idx in _c_tree.query(pt):
        if _c_preps[idx].contains(pt):
            return _c_names[idx]
    return None


def is_in_neutral_territory(lat: float, lon: float, neutral: FrozenSet[str]) -> bool:
    """True if the point lies inside any country's territory (land + 12nm sea/airspace)."""
    if not neutral:
        return False
    _load_countries()
    if _c_tree is None:
        return False
    pt = Point(lon, lat)
    for idx in _c_tree.query(pt):
        if _c_names[idx] in neutral and _c_preps[idx].contains(pt):
            return True
    return False


# Backward-compat alias used in a few places
is_in_neutral_airspace = is_in_neutral_territory


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


def find_route_naval(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    neutral_countries: Set[str],
) -> List[Tuple[float, float]]:
    """
    Route a naval unit avoiding land AND neutral territorial waters (12nm).
    Uses the same grid and A* as find_route('water') but adds territory obstacle check.
    Falls back to plain water route if neutral_countries is empty.
    """
    if not neutral_countries:
        return find_route(lat1, lon1, lat2, lon2, domain="water")

    nc: FrozenSet[str] = frozenset(neutral_countries)

    # If direct water route avoids both land and neutral territory, go direct
    if not route_crosses_land(lat1, lon1, lat2, lon2) and not _path_crosses_neutral(
        lat1, lon1, lat2, lon2, nc
    ):
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
        return is_water(clat, clon) and not is_in_neutral_territory(clat, clon, nc)

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
        return find_route(lat1, lon1, lat2, lon2, domain="water")  # fallback

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


# ── Air routing (neutral airspace avoidance) ──────────────────────────────────
#
# Uses a coarser grid than the land/water router — air routes span hundreds of km
# so 0.15° per cell (~16 km) is precise enough to route around country-level obstacles.

_AIR_GRID_DEG = 0.15
_AIR_PAD_DEG  = 2.0


def _path_crosses_neutral(lat1: float, lon1: float, lat2: float, lon2: float,
                           neutral: FrozenSet[str], samples: int = 20) -> bool:
    for i in range(1, samples):
        t = i / samples
        lat = lat1 + t * (lat2 - lat1)
        lon = lon1 + t * (lon2 - lon1)
        if is_in_neutral_airspace(lat, lon, neutral):
            return True
    return False


def find_route_air(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    neutral_countries: Set[str],
) -> List[Tuple[float, float]]:
    """
    Route an air unit from (lat1, lon1) to (lat2, lon2) while respecting
    neutral country airspace.  The destination country is always allowed
    (unit has a reason to be there).  Falls back to direct if no neutral
    country is on the path.
    """
    if not neutral_countries:
        return [(lat2, lon2)]

    # Destination country is permitted — unit is flying there intentionally
    dest_country = which_country(lat2, lon2)
    to_avoid: FrozenSet[str] = frozenset(
        c for c in neutral_countries if c != dest_country
    )

    if not to_avoid or not _path_crosses_neutral(lat1, lon1, lat2, lon2, to_avoid):
        return [(lat2, lon2)]

    # Build A* grid over the bounding box of the route
    lat_min = min(lat1, lat2) - _AIR_PAD_DEG
    lat_max = max(lat1, lat2) + _AIR_PAD_DEG
    lon_min = min(lon1, lon2) - _AIR_PAD_DEG
    lon_max = max(lon1, lon2) + _AIR_PAD_DEG

    nx = max(2, int((lon_max - lon_min) / _AIR_GRID_DEG))
    ny = max(2, int((lat_max - lat_min) / _AIR_GRID_DEG))

    def cell_of(lat: float, lon: float) -> Tuple[int, int]:
        cx = int((lon - lon_min) / _AIR_GRID_DEG)
        cy = int((lat - lat_min) / _AIR_GRID_DEG)
        return max(0, min(nx - 1, cx)), max(0, min(ny - 1, cy))

    def cell_center(c: Tuple[int, int]) -> Tuple[float, float]:
        cx, cy = c
        return lat_min + (cy + 0.5) * _AIR_GRID_DEG, lon_min + (cx + 0.5) * _AIR_GRID_DEG

    @lru_cache(maxsize=None)
    def passable(c: Tuple[int, int]) -> bool:
        clat, clon = cell_center(c)
        return not is_in_neutral_airspace(clat, clon, to_avoid)

    start = cell_of(lat1, lon1)
    goal  = cell_of(lat2, lon2)

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
        return [(lat2, lon2)]  # no path found — fly direct as fallback

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
