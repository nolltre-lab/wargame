import math

EARTH_RADIUS_KM = 6371.0


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


# ── Naval terrain helpers ─────────────────────────────────────────────────────
#
# Full polygon terrain is Phase 2+. For Phase 1 we handle the one problematic
# case in the Baltic scenario: ships crossing Estonian/Latvian land by routing
# straight between the Gulf of Finland and the Baltic Sea.
#
# The Gulf of Finland mouth is at approximately 23°E. A route that crosses this
# longitude below ~60°N travels through Estonian territory. We detect that and
# inject a corridor waypoint that stays in open water.

GULF_MOUTH_LON: float = 23.0   # longitude of the Gulf / Baltic divide
GULF_SAFE_LAT: float = 60.0    # routes crossing GULF_MOUTH_LON above this are in open water

# Default naval corridor: a single open-water waypoint north of Tallinn.
# Ships routing between Gulf and Baltic will transit through here.
BALTIC_NAVAL_CORRIDORS: list[tuple[float, float]] = [
    (60.3, 22.0),
]


def route_clips_land(lat1: float, lon1: float, lat2: float, lon2: float) -> bool:
    """Return True if a straight route likely crosses Baltic land masses.

    Detects routes that cross the Gulf of Finland mouth (≈23 °E) below the
    safe navigation latitude, which would take a ship through Estonian territory.
    """
    # Same side of the divide → no land crossing
    if (lon1 < GULF_MOUTH_LON) == (lon2 < GULF_MOUTH_LON):
        return False
    # Find the latitude where the path crosses the divide
    dlon = lon2 - lon1
    if abs(dlon) < 1e-6:
        return False
    t = (GULF_MOUTH_LON - lon1) / dlon
    if not 0.0 < t < 1.0:
        return False
    return (lat1 + (lat2 - lat1) * t) < GULF_SAFE_LAT


def naval_waypoints(
    unit_lat: float,
    unit_lon: float,
    dest_lat: float,
    dest_lon: float,
    corridors: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Return waypoints for a naval route, inserting corridor waypoints to avoid land."""
    if not route_clips_land(unit_lat, unit_lon, dest_lat, dest_lon):
        return [(dest_lat, dest_lon)]
    for c_lat, c_lon in corridors:
        if not route_clips_land(unit_lat, unit_lon, c_lat, c_lon):
            return [(c_lat, c_lon), (dest_lat, dest_lon)]
    # No corridor helps (shouldn't happen in the Baltic) — fall back to direct
    return [(dest_lat, dest_lon)]


def destination(lat: float, lon: float, bearing_deg: float, distance_km: float) -> tuple[float, float]:
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    bearing_r = math.radians(bearing_deg)
    d = distance_km / EARTH_RADIUS_KM
    new_lat = math.asin(
        math.sin(lat_r) * math.cos(d)
        + math.cos(lat_r) * math.sin(d) * math.cos(bearing_r)
    )
    new_lon = lon_r + math.atan2(
        math.sin(bearing_r) * math.sin(d) * math.cos(lat_r),
        math.cos(d) - math.sin(lat_r) * math.sin(new_lat),
    )
    return math.degrees(new_lat), math.degrees(new_lon)
