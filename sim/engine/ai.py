from typing import Dict, List, Tuple, Optional
from .unit import Unit, Mission, MissionType, MissionStatus, UnitClass
from .objective import Objective
from .geo import haversine, bearing, destination
from . import terrain
from .combat import weapon_range, valid_targets as unit_valid_targets, UNIT_TYPE_LIB, sensor_range, unit_detection_range

# Radius within which a unit is considered "on station" at an objective
ON_STATION_RADIUS_KM: Dict[str, float] = {
    UnitClass.AIR: 3.0,
    UnitClass.GROUND: 2.0,
    UnitClass.NAVAL: 5.0,
}

# Radius of the patrol circuit around an objective or area patrol center
PATROL_RADIUS_KM: Dict[str, float] = {
    UnitClass.AIR: 40.0,
    UnitClass.GROUND: 6.0,
    UnitClass.NAVAL: 25.0,
}

# Tight holding orbit for airborne units with no active mission
HOLDING_RADIUS_KM = 10.0

PATROL_POINTS = 4

# Ticks to fully rearm/refuel at home base when not in unit_types.json
_REARM_TICKS_DEFAULT: Dict[str, int] = {"air": 8, "ground": 5, "naval": 12}

# Ticks in the loadout-selection window after arriving at base, before rearm begins.
# Commander AI and player can both change loadout during this window; if nothing
# changes the unit re-arms with its existing loadout.
LOADOUT_SELECTION_TICKS = 2

# Minimum fuel safety buffer added on top of the calculated transit cost (%)
BINGO_SAFETY_BUFFER_PCT = 15.0

# Neutral airspace routing adds detour distance.  Straight-line haversine
# underestimates the actual routed path; this factor corrects for that.
# Kaliningrad→Gulf-of-Finland via Latvia is ~30% longer than direct.
ROUTE_COST_FACTOR_AIR = 1.3

# Default cruise speed as a fraction of max_speed when not in unit_types.json
_CRUISE_SPEED_FRACTION = {"air": 0.70, "naval": 0.60}
# Default fuel burn multiplier at cruise speed
_CRUISE_FUEL_FACTOR    = {"air": 0.35, "naval": 0.40}

# Tick duration in sim-seconds (used to convert speed → km/tick for fuel estimation)
_TICK_DURATION_S = 60.0

_FUEL_BURN_MOVING = {"air": 1.5, "ground": 0.1, "naval": 0.2}

# Objective types each unit class can use as a base, unless overridden by
# "valid_base_types" in unit_types.json.  Add that key to restrict a type
# further (e.g. heavy transports that need major runways only → ["airfield"]).
_DEFAULT_BASE_TYPES: Dict[str, set] = {
    "air":    {"airfield"},
    "naval":  {"port", "base"},
    "ground": {"base"},
}


def _valid_base_types(unit: Unit) -> set:
    lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
    if "valid_base_types" in lib:
        return set(lib["valid_base_types"])
    return _DEFAULT_BASE_TYPES.get(unit.unit_class.value, {"base"})


def _nearest_friendly_base(unit: Unit, objectives: Dict[str, "Objective"]) -> "Optional[Objective]":
    """
    Nearest objective of a type this unit can use that is controlled by the
    unit's own side.  Used for planned RTB / rearm — friendly bases only.
    """
    valid = _valid_base_types(unit)
    candidates = [
        o for o in objectives.values()
        if o.controlling_side == unit.side.value and o.type.value in valid
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda o: haversine(unit.lat, unit.lon, o.lat, o.lon))


def _nearest_emergency_base(unit: Unit, objectives: Dict[str, "Objective"]) -> "Optional[Objective]":
    """
    Last-resort landing: nearest non-enemy base (own or neutral) when no
    friendly base exists and fuel is critically low.  Unit refuels only — no
    magazine reload (controlled by unit.refuel_only flag).
    """
    enemy_side = "red" if unit.side.value == "blue" else "blue"
    valid = _valid_base_types(unit)
    candidates = [
        o for o in objectives.values()
        if o.controlling_side != enemy_side and o.type.value in valid
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda o: haversine(unit.lat, unit.lon, o.lat, o.lon))


# Below this fuel level, divert to any non-enemy base even if no friendly one exists
EMERGENCY_FUEL_PCT = 5.0


def _bingo_threshold(unit: Unit, objectives: Dict[str, "Objective"]) -> float:
    """
    Dynamic bingo fuel level: % needed to reach the nearest usable base at
    full speed plus BINGO_SAFETY_BUFFER_PCT.  Returns 0.0 (never trigger) if
    no base is reachable anywhere — no point RTBing to nowhere.

    Priority: nearest non-enemy objective of valid type → home_base_lat/lon fallback.
    """
    base = _nearest_friendly_base(unit, objectives)
    if base is not None:
        dest_lat, dest_lon = base.lat, base.lon
    elif unit.home_base_lat is not None and unit.home_base_lon is not None:
        dest_lat, dest_lon = unit.home_base_lat, unit.home_base_lon
    else:
        return 0.0

    dist_km = haversine(unit.lat, unit.lon, dest_lat, dest_lon)
    if unit.unit_class == UnitClass.AIR:
        dist_km *= ROUTE_COST_FACTOR_AIR

    # Use cruise speed/burn for RTB estimate — units throttle back to save fuel
    speed_kmh = _transit_speed(unit) if unit.unit_class != UnitClass.GROUND else (unit.max_speed or 500.0)
    if speed_kmh <= 0:
        speed_kmh = 500.0
    km_per_tick = speed_kmh * (_TICK_DURATION_S / 3600.0)

    lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
    base_burn = lib.get("fuel_burn_per_tick", _FUEL_BURN_MOVING.get(unit.unit_class.value, 1.5))
    cruise_factor = lib.get("cruise_fuel_factor", _CRUISE_FUEL_FACTOR.get(unit.unit_class.value, 1.0))
    burn = base_burn * cruise_factor

    ticks_needed = dist_km / km_per_tick if km_per_tick > 0 else 0.0
    return ticks_needed * burn + BINGO_SAFETY_BUFFER_PCT

def fuel_roundtrip_pct(
    unit: Unit,
    dest_lat: float,
    dest_lon: float,
    objectives: Dict[str, "Objective"],
) -> float:
    """
    Estimate the fuel % needed for unit to fly to (dest_lat, dest_lon) and
    then back to its nearest friendly base.  Includes BINGO_SAFETY_BUFFER_PCT
    and the air-unit route cost factor.  Used by the commander to reject
    assignments a unit cannot afford.
    """
    lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
    base_burn = lib.get("fuel_burn_per_tick", _FUEL_BURN_MOVING.get(unit.unit_class.value, 1.5))
    cruise_factor = lib.get("cruise_fuel_factor", _CRUISE_FUEL_FACTOR.get(unit.unit_class.value, 1.0))
    burn = base_burn * cruise_factor

    speed_kmh = _transit_speed(unit) if unit.unit_class != UnitClass.GROUND else (unit.max_speed or 500.0)
    if speed_kmh <= 0:
        speed_kmh = 500.0
    km_per_tick = speed_kmh * (_TICK_DURATION_S / 3600.0)

    dist_to_dest = haversine(unit.lat, unit.lon, dest_lat, dest_lon)
    base = _nearest_friendly_base(unit, objectives)
    dist_home = haversine(dest_lat, dest_lon, base.lat, base.lon) if base else dist_to_dest

    total_km = dist_to_dest + dist_home
    if unit.unit_class == UnitClass.AIR:
        total_km *= ROUTE_COST_FACTOR_AIR

    ticks = total_km / km_per_tick if km_per_tick > 0 else 0.0
    return ticks * burn + BINGO_SAFETY_BUFFER_PCT


# Distance at which surveillance units (AWACS/MPA) detect and flee from enemy air threats
SURVEILLANCE_FLEE_RANGE_KM = 200.0

# Offset distances for escort formation
ESCORT_OFFSET_AIR_KM = 25.0    # air unit escorts: lateral separation from charge
ESCORT_OFFSET_NAVAL_KM = 10.0  # naval unit escorts: lateral separation from charge


def _patrol_circuit(center_lat: float, center_lon: float, radius_km: float) -> List[Tuple[float, float]]:
    return [
        destination(center_lat, center_lon, (360.0 / PATROL_POINTS) * i, radius_km)
        for i in range(PATROL_POINTS)
    ]


def _territory_edge(
    center_lat: float, center_lon: float,
    hdg: float, radius_km: float,
    neutral: frozenset,
) -> Optional[Tuple[float, float]]:
    """
    Binary-search along the bearing `hdg` from center outward to radius_km.
    Returns the farthest point that is NOT in neutral territory (land + 12nm sea/air),
    or None if even the center is inside neutral territory (authorised mission).
    """
    if terrain.is_in_neutral_territory(center_lat, center_lon, neutral):
        return None
    lo, hi = 0.0, radius_km
    for _ in range(15):
        mid = (lo + hi) / 2.0
        lat, lon = destination(center_lat, center_lon, hdg, mid)
        if terrain.is_in_neutral_territory(lat, lon, neutral):
            hi = mid
        else:
            lo = mid
        if hi - lo < 1.0:
            break
    return destination(center_lat, center_lon, hdg, lo)


def _routed_patrol_circuit(
    unit: Unit,
    center_lat: float,
    center_lon: float,
    radius_km: float,
    neutral_countries: Optional[set],
) -> List[Tuple[float, float]]:
    """
    Generate a patrol circuit respecting neutral territorial boundaries.
    - AIR units respect 12nm territorial airspace (find_route_air).
    - NAVAL units respect 12nm territorial waters (find_route_naval).
    - GROUND units get the raw circuit unchanged.
    Waypoints inside neutral territory are pulled back to the boundary edge.
    If the patrol centre itself is inside neutral territory, it is relocated
    to the nearest safe point on the line from the unit's current position.
    """
    raw_pts = _patrol_circuit(center_lat, center_lon, radius_km)
    if not neutral_countries or unit.unit_class == UnitClass.GROUND:
        return raw_pts

    nc = frozenset(neutral_countries)

    # If the patrol centre is in neutral territory, relocate it to the nearest
    # safe point on the bearing from the unit's current position to the centre.
    eff_lat, eff_lon = center_lat, center_lon
    if terrain.is_in_neutral_territory(center_lat, center_lon, nc):
        dist_to_ctr = haversine(unit.lat, unit.lon, center_lat, center_lon)
        if dist_to_ctr > 0:
            hdg_to_ctr = bearing(unit.lat, unit.lon, center_lat, center_lon)
            edge = _territory_edge(unit.lat, unit.lon, hdg_to_ctr, dist_to_ctr, nc)
            if edge is not None:
                eff_lat, eff_lon = edge
            # else: unit is also in neutral → authorised overflight; keep original centre
        raw_pts = _patrol_circuit(eff_lat, eff_lon, radius_km)

    # Adjust each circuit waypoint: if it lands in neutral territory, pull it
    # back to the farthest safe point along that bearing from the circuit centre.
    adjusted: List[Tuple[float, float]] = []
    for lat, lon in raw_pts:
        if terrain.is_in_neutral_territory(lat, lon, nc):
            hdg = bearing(eff_lat, eff_lon, lat, lon)
            edge = _territory_edge(eff_lat, eff_lon, hdg, radius_km, nc)
            if edge is not None:
                adjusted.append(edge)
            # None means effective centre is in neutral → authorised; skip waypoint
        else:
            adjusted.append((lat, lon))

    pts = adjusted if adjusted else raw_pts

    # Route each leg avoiding neutral territory.
    wps: List[Tuple[float, float]] = []
    prev = (unit.lat, unit.lon)
    if unit.unit_class == UnitClass.AIR:
        for pt in pts:
            wps.extend(terrain.find_route_air(prev[0], prev[1], pt[0], pt[1], nc))
            prev = pt
    else:  # NAVAL
        for pt in pts:
            wps.extend(terrain.find_route_naval(prev[0], prev[1], pt[0], pt[1], nc))
            prev = pt
    return wps or pts


def _is_surveillance(unit: Unit) -> bool:
    """True for AWACS/MPA types — they flee air threats rather than engaging."""
    return bool(UNIT_TYPE_LIB.get(unit.unit_type, {}).get("is_surveillance", False))


def _flee_waypoints(unit: Unit, units: Dict[str, Unit]) -> List[Tuple[float, float]]:
    """
    If an airborne surveillance unit has enemy air within SURVEILLANCE_FLEE_RANGE_KM,
    return a single waypoint 400 km away in the opposite direction.
    Returns an empty list if no threat.
    """
    enemy_side = "red" if unit.side.value == "blue" else "blue"
    threats = [
        u for u in units.values()
        if not u.destroyed
        and u.side.value == enemy_side
        and u.unit_class == UnitClass.AIR
        and haversine(unit.lat, unit.lon, u.lat, u.lon) <= SURVEILLANCE_FLEE_RANGE_KM
    ]
    if not threats:
        return []
    clat = sum(t.lat for t in threats) / len(threats)
    clon = sum(t.lon for t in threats) / len(threats)
    flee_hdg = (bearing(unit.lat, unit.lon, clat, clon) + 180.0) % 360.0
    flee_lat, flee_lon = destination(unit.lat, unit.lon, flee_hdg, 400.0)
    return [(flee_lat, flee_lon)]


_MAG_TO_TARGET_CLASS: Dict[str, str] = {"aa": "air", "ag": "ground", "as": "naval"}


def _loadout_primary_target_class(unit: Unit) -> Optional[str]:
    """
    Return the enemy unit-class this loadout is optimised to attack.
    Anti-ship loadout (most rounds = 'as') → 'naval'.
    Air superiority (most rounds = 'aa') → 'air'.
    Strike (most rounds = 'ag') → 'ground'.
    Returns None when loadout is unset or has no magazine data.
    """
    if not unit.loadout:
        return None
    lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
    preset_mags = lib.get("loadout_presets", {}).get(unit.loadout, {}).get("magazines", {})
    if not preset_mags:
        return None
    primary = max(preset_mags, key=lambda k: preset_mags.get(k, 0))
    return _MAG_TO_TARGET_CLASS.get(primary)


def _nearest_valid_intercept_target(
    unit: Unit,
    all_units: Dict[str, Unit],
    network_ids: Optional[set] = None,
    gci_ids: Optional[set] = None,
) -> Optional[Unit]:
    """
    Nearest enemy this unit should navigate to intercept, filtered by what it
    can actually know about:

      own sensor  → detected by this unit's radar/ESM (can close and engage)
      data-link   → in the shared fire-control network (can engage directly)
      GCI cue     → surveillance unit (AWACS/MPA) sees it and passes bearing/range
                    via voice/radio — unit navigates toward it but still needs own
                    sensor lock to engage (the combat gate enforces this)

    Target selection is loadout-aware: an anti-ship F-35 prefers naval targets,
    an air-superiority fighter prefers air targets.  Falls back to nearest of any
    valid class if no preferred-class targets are visible.

    Naval units only chase naval targets to prevent ships routing overland.
    Returns None if the target is unknown through all available channels.
    """
    enemy_side = "red" if unit.side.value == "blue" else "blue"
    allowed_classes = unit_valid_targets(unit)
    if unit.unit_class == UnitClass.NAVAL:
        allowed_classes = [c for c in allowed_classes if c == "naval"]

    candidates = [
        u for u in all_units.values()
        if u.side.value == enemy_side
        and not u.destroyed
        and u.unit_class.value in allowed_classes
    ]
    if not candidates:
        return None

    # Build the set of target IDs reachable via any channel
    known_ids: set = set()
    for c in candidates:
        dist = haversine(unit.lat, unit.lon, c.lat, c.lon)
        if dist <= unit_detection_range(unit, c):   # own sensor (radar + ESM)
            known_ids.add(c.id)
    if network_ids:
        known_ids |= network_ids          # full data-link — fire control quality
    if gci_ids:
        known_ids |= gci_ids              # GCI voice cue — navigation quality only

    visible = [c for c in candidates if c.id in known_ids]
    if not visible:
        return None

    # Prefer targets matching the loadout's primary ammo class.
    # An anti-ship F-35 hunts ships; an air-superiority fighter hunts aircraft.
    preferred_class = _loadout_primary_target_class(unit)
    if preferred_class:
        preferred = [t for t in visible if t.unit_class.value == preferred_class]
        if preferred:
            return min(preferred, key=lambda u: haversine(unit.lat, unit.lon, u.lat, u.lon))

    # No preferred-class targets visible — fall back to nearest of any valid class
    return min(visible, key=lambda u: haversine(unit.lat, unit.lon, u.lat, u.lon))


def _route_to(
    unit: Unit,
    dest_lat: float,
    dest_lon: float,
    neutral_countries: Optional[set] = None,
) -> List[Tuple[float, float]]:
    """
    Return waypoints from the unit's current position to dest.
    Ground units route around water; naval around land (coastline A*).
    Air units fly direct by default, but route around neutral country airspace
    when neutral_countries is non-empty.
    """
    if unit.unit_class == UnitClass.GROUND:
        return terrain.find_route(unit.lat, unit.lon, dest_lat, dest_lon, domain="land")
    if unit.unit_class == UnitClass.NAVAL:
        if neutral_countries:
            return terrain.find_route_naval(unit.lat, unit.lon, dest_lat, dest_lon, neutral_countries)
        return terrain.find_route(unit.lat, unit.lon, dest_lat, dest_lon, domain="water")
    if neutral_countries:
        return terrain.find_route_air(unit.lat, unit.lon, dest_lat, dest_lon, neutral_countries)
    return [(dest_lat, dest_lon)]


def _set_altitude_phase(unit: Unit, phase: str) -> None:
    """
    Update unit.altitude_m and unit.emcon based on mission phase.

    'ingress': heading toward enemy ground/naval target — use low altitude to exploit
               the radar horizon limit on surface and ground radars. Stealth aircraft
               (stealth_emcon=true in unit_types) also go radar-silent so ESM can't
               detect their emissions; they rely on data-link for targeting.

    'cruise':  everything else (CAP, patrol, RTB, evasion, holding orbit) — use the
               library cruise altitude and keep radar on.

    NOTE: altitude also affects fuel efficiency (high altitude = thinner air = less burn).
    That tradeoff is deferred to the fuel-optimization feature which will use per-leg
    speed/burn rates and make altitude a conscious tactical choice.
    """
    if unit.unit_class != UnitClass.AIR or not unit.airborne:
        return
    lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
    if phase == "ingress" and "altitude_ingress_m" in lib:
        unit.altitude_m = float(lib["altitude_ingress_m"])
        if lib.get("stealth_emcon", False):
            unit.emcon = False
    else:
        unit.altitude_m = float(lib.get("altitude_m", unit.altitude_m))
        unit.emcon = True


def _transit_speed(unit: Unit) -> float:
    """
    Speed to use for non-combat transit (en route, RTB, patrol circuits).
    Reads cruise_speed_kmh from the type library; falls back to a class-based
    fraction of max_speed.  Ground units always use max_speed (distances are
    short and fuel consumption is negligible).
    """
    if unit.unit_class == UnitClass.GROUND:
        return unit.max_speed
    lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
    if "cruise_speed_kmh" in lib:
        return float(lib["cruise_speed_kmh"])
    frac = _CRUISE_SPEED_FRACTION.get(unit.unit_class.value, 1.0)
    return unit.max_speed * frac


def _is_winchester(unit: Unit) -> bool:
    """True when all magazine categories are depleted (and the unit has a loadout at all)."""
    return bool(unit.magazines) and all(v == 0 for v in unit.magazines.values())


def _primary_ammo_depleted(unit: Unit) -> bool:
    """
    True when the loadout's *primary* ammo type is exhausted, even if secondary
    ammo types remain.  Prevents e.g. an anti-ship F-35 from orbiting with its
    last AA missile after all AS rounds are gone — the unit RTBs to rearm.

    Primary = the ammo category with the most rounds in the active loadout preset.
    Only applies to units that have an explicit loadout set.
    """
    if not unit.magazines or not unit.loadout:
        return False
    lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
    preset = lib.get("loadout_presets", {}).get(unit.loadout, {})
    preset_mags = preset.get("magazines", {})
    if not preset_mags:
        return False
    primary = max(preset_mags, key=lambda k: preset_mags.get(k, 0))
    return unit.magazines.get(primary, 0) == 0


def _should_auto_rtb(unit: Unit, objectives: Dict[str, "Objective"]) -> Optional[str]:
    """
    Return the reason string if the unit should automatically RTB, else None.
    Only air and naval units auto-RTB — ground units restock in place.

    Normal bingo: fuel needed to reach nearest FRIENDLY base + buffer.
    Emergency bingo: critically low (EMERGENCY_FUEL_PCT) and any non-enemy
    base exists (neutral ok) — routes there with refuel_only=True.
    """
    if unit.unit_class not in (UnitClass.AIR, UnitClass.NAVAL):
        return None
    if _is_winchester(unit):
        return "winchester"
    if _primary_ammo_depleted(unit):
        return "winchester"
    if unit.fuel_pct <= 0:
        return None
    if unit.fuel_pct <= _bingo_threshold(unit, objectives):
        return "bingo"
    # Emergency divert: no friendly base but critically low, any non-enemy base will do
    if (unit.fuel_pct <= EMERGENCY_FUEL_PCT
            and _nearest_friendly_base(unit, objectives) is None
            and _nearest_emergency_base(unit, objectives) is not None):
        return "bingo"
    return None


# Evasion maneuver distances (km) per technique
_NOTCH_KM = 80.0   # perpendicular jink to defeat pulse-Doppler radar
_CRANK_KM = 100.0  # angled separation to create range in BVR
_FLEE_KM  = 150.0  # direct retreat for mixed / ground threats

# Notch is held for this many ticks before switching sides (creates realistic sustained jink)
_NOTCH_JINK_PERIOD = 3

# Standoff fire: hold at this fraction of weapon range (just inside own envelope)
STANDOFF_FACTOR = 0.88
# Approach angles evaluated when selecting the safest standoff point around a target
_STANDOFF_N_ANGLES = 8
# Distance to egress after expending all strike ammo (air units vs ground/naval targets)
EGRESS_KM = 150.0


def _standoff_approach_point(
    unit: Unit,
    target_lat: float,
    target_lon: float,
    standoff_dist: float,
    all_units: Dict[str, Unit],
) -> Tuple[float, float]:
    """
    Return the safest point at standoff_dist from (target_lat, target_lon).

    Tests _STANDOFF_N_ANGLES evenly-spaced bearings and scores each by total
    threat exposure: sum of max(0, enemy_weapon_range - dist_to_threat)² for
    all live enemy units.  The attacker picks the angle that minimises how deeply
    any standoff point penetrates enemy weapon envelopes.  When all angles are
    equally dangerous (e.g. a surrounded unit) it still picks the least bad one.

    By routing to this point rather than to target.lat/target.lon, units
    naturally hold at weapon range — waypoints are consumed when the standoff
    point is reached, triggering the on-station orbit on the following tick.
    """
    enemy_side = "red" if unit.side.value == "blue" else "blue"
    threats = [u for u in all_units.values() if not u.destroyed and u.side.value == enemy_side]

    best_pt: Optional[Tuple[float, float]] = None
    best_cost = float("inf")

    for i in range(_STANDOFF_N_ANGLES):
        angle = (360.0 / _STANDOFF_N_ANGLES) * i
        lat, lon = destination(target_lat, target_lon, angle, standoff_dist)

        cost = 0.0
        for threat in threats:
            d = haversine(lat, lon, threat.lat, threat.lon)
            threat_w = weapon_range(threat, unit.unit_class.value)
            if d < threat_w:
                penetration = threat_w - d
                cost += penetration * penetration

        if cost < best_cost:
            best_cost = cost
            best_pt = (lat, lon)

    if best_pt is None:
        # Fallback: direct approach bearing (no threats or zero-distance edge case)
        dist_to_target = haversine(unit.lat, unit.lon, target_lat, target_lon)
        hdg = bearing(target_lat, target_lon, unit.lat, unit.lon) if dist_to_target > 0.1 else 0.0
        best_pt = destination(target_lat, target_lon, hdg, standoff_dist)

    return best_pt


def _unit_seed(unit: Unit) -> int:
    """Stable per-unit integer seed (deterministic, not Python's session-random hash)."""
    return sum(ord(c) for c in unit.id)


def _evasion_waypoints(
    unit: Unit, attacker_ids: List[str], units: Dict[str, Unit], tick: int = 0
) -> List[Tuple[float, float]]:
    """
    Return evasion waypoints appropriate to the threat type:
      - Ground/naval SAMs  → notch (perpendicular to radar LOS), jinking L/R every
        NOTCH_JINK_PERIOD ticks, biased toward home base when one side is better.
      - Air-to-air threats → crank (≈50° off flee bearing) to create range while
        keeping the threat in own radar cone; alternates sides per unit + tick.
      - Mixed threats      → direct flee away from attacker centroid.
    """
    live = [units[aid] for aid in attacker_ids if aid in units and not units[aid].destroyed]
    if not live:
        return []

    avg_lat = sum(a.lat for a in live) / len(live)
    avg_lon = sum(a.lon for a in live) / len(live)
    flee_hdg = bearing(avg_lat, avg_lon, unit.lat, unit.lon)   # direct away from threats

    srf_threats = [a for a in live if a.unit_class.value in ("ground", "naval")]
    air_threats  = [a for a in live if a.unit_class.value == "air"]

    if srf_threats and not air_threats:
        # ── Notch: fly perpendicular to the radar LOS ────────────────────────
        notch_r = (flee_hdg + 90.0) % 360.0
        notch_l = (flee_hdg - 90.0) % 360.0

        # Prefer the side that points more toward home base (drift to safety while notching)
        if unit.home_base_lat is not None and unit.home_base_lon is not None:
            base_hdg = bearing(unit.lat, unit.lon, unit.home_base_lat, unit.home_base_lon)
            diff_r = abs((notch_r - base_hdg + 180) % 360 - 180)
            diff_l = abs((notch_l - base_hdg + 180) % 360 - 180)
            preferred, other = (notch_r, notch_l) if diff_r < diff_l else (notch_l, notch_r)
        else:
            preferred, other = notch_r, notch_l

        # Jink: alternate every NOTCH_JINK_PERIOD ticks (phase offset per unit avoids lock-step)
        jink_phase = (tick // _NOTCH_JINK_PERIOD + _unit_seed(unit)) % 2
        hdg = preferred if jink_phase == 0 else other
        return [destination(unit.lat, unit.lon, hdg, _NOTCH_KM)]

    elif air_threats and not srf_threats:
        # ── Crank: ~50° off the flee bearing, alternating sides ──────────────
        side = 1 if (tick // 2 + _unit_seed(unit)) % 2 == 0 else -1
        crank_hdg = (flee_hdg + 50.0 * side) % 360.0
        return [destination(unit.lat, unit.lon, crank_hdg, _CRANK_KM)]

    else:
        # ── Mixed or unknown: direct flee from centroid ───────────────────────
        return [destination(unit.lat, unit.lon, flee_hdg, _FLEE_KM)]


def resolve_missions(
    units: Dict[str, Unit],
    objectives: Dict[str, Objective],
    under_fire: Dict[str, List[str]] | None = None,
    tick: int = 0,
    neutral_countries: Optional[set] = None,
) -> List[dict]:
    if under_fire is None:
        under_fire = {}

    # ── Data-link network picture ─────────────────────────────────────────────
    # Units with data_link=True share fire-control-quality tracks with each other.
    # Any data-linked unit can engage a target that any friendly data-linked unit sees.
    e_side_of = {"blue": "red", "red": "blue"}
    network_ids: Dict[str, set] = {"blue": set(), "red": set()}
    for scanner in units.values():
        if scanner.destroyed or not UNIT_TYPE_LIB.get(scanner.unit_type, {}).get("data_link", False):
            continue
        for target in units.values():
            if target.destroyed or target.side.value != e_side_of[scanner.side.value]:
                continue
            if haversine(scanner.lat, scanner.lon, target.lat, target.lon) <= unit_detection_range(scanner, target):
                network_ids[scanner.side.value].add(target.id)

    # ── GCI picture (surveillance units only) ─────────────────────────────────
    # AWACS/MPA pass target bearings/ranges via voice/radio to non-data-linked units.
    # This enables navigation toward contacts but NOT a fire-control solution —
    # the receiving unit still needs its own radar lock to engage.
    gci_ids: Dict[str, set] = {"blue": set(), "red": set()}
    for scanner in units.values():
        if scanner.destroyed or not _is_surveillance(scanner) or not scanner.airborne:
            continue
        for target in units.values():
            if target.destroyed or target.side.value != e_side_of[scanner.side.value]:
                continue
            if haversine(scanner.lat, scanner.lon, target.lat, target.lon) <= unit_detection_range(scanner, target):
                gci_ids[scanner.side.value].add(target.id)

    events: List[dict] = []
    for unit in units.values():
        if unit.destroyed:
            continue
        if unit.rearming:
            continue  # being serviced at base — simulation._burn_resources handles tick-down
        if unit.awaiting_loadout:
            continue  # in loadout-selection window — simulation._burn_resources handles tick-down

        # Default to cruise altitude/EMCON at the top of each tick.
        # INTERCEPT targeting ground/naval will override to ingress below.
        _set_altitude_phase(unit, "cruise")

        # ── Ground auto-restock (winchester → enter loadout-selection window) ────
        if unit.unit_class == UnitClass.GROUND and _is_winchester(unit):
            unit.awaiting_loadout = True
            unit.loadout_selection_ticks_left = LOADOUT_SELECTION_TICKS
            events.append({
                "type": "winchester",
                "unit_id": unit.id,
                "unit_name": unit.name,
                "side": unit.side.value,
                "tick": None,
            })
            continue  # _burn_resources handles window tick-down → rearm transition

        # ── Air / naval auto-RTB (bingo fuel or winchester) ───────────────────
        m = unit.mission
        already_rtb = m is not None and m.type == MissionType.RTB
        if not already_rtb:
            reason = _should_auto_rtb(unit, objectives)
            if reason is not None:
                unit.previous_mission = unit.mission  # saved for restore after rearm
                unit.waypoints = []
                unit.speed = 0.0
                unit.mission = Mission(type=MissionType.RTB, status=MissionStatus.EN_ROUTE)
                m = unit.mission
                events.append({
                    "type": "bingo_fuel" if reason == "bingo" else "winchester",
                    "unit_id": unit.id,
                    "unit_name": unit.name,
                    "side": unit.side.value,
                    "tick": None,
                })

        # Surveillance threat avoidance: AWACS/MPA flee enemy air before doing anything else
        if _is_surveillance(unit) and unit.airborne and unit.fuel_pct > 0.0:
            flee_wps = _flee_waypoints(unit, units)
            if flee_wps:
                unit.waypoints = flee_wps
                unit.speed = unit.max_speed
                continue  # skip normal mission logic this tick

        # ── Evasion: non-surveillance air/naval units dodge active fire ───────────
        # Excluded: RTB (already heading home) and INTERCEPT (deliberate close-in combat).
        # Ground units are at battalion scale — they hold/suppress rather than flee.
        cur_m = unit.mission
        if (unit.id in under_fire
                and unit.unit_class in (UnitClass.AIR, UnitClass.NAVAL)
                and not _is_surveillance(unit)
                and (cur_m is None or cur_m.type not in (MissionType.RTB, MissionType.INTERCEPT))
                and (unit.unit_class != UnitClass.AIR or unit.airborne)
                and unit.fuel_pct > 0.0):
            evade_wps = _evasion_waypoints(unit, under_fire[unit.id], units, tick)
            if evade_wps:
                unit.waypoints = evade_wps
                unit.speed = unit.max_speed
                continue  # skip normal mission waypoint logic this tick

        m = unit.mission
        if m is None:
            # Airborne air units with no mission fly a tight holding orbit (if they have fuel)
            if (unit.unit_class == UnitClass.AIR
                    and unit.airborne
                    and not unit.waypoints
                    and unit.fuel_pct > 0.0):
                unit.waypoints = _patrol_circuit(unit.lat, unit.lon, HOLDING_RADIUS_KM)
                unit.speed = _transit_speed(unit)
            continue

        if m.type in (MissionType.SECURE, MissionType.DEFEND):
            obj = objectives.get(m.objective_id or "")
            if obj is None:
                continue
            dist = haversine(unit.lat, unit.lon, obj.lat, obj.lon)
            radius = ON_STATION_RADIUS_KM[unit.unit_class.value]

            if dist <= radius:
                m.status = MissionStatus.ON_STATION
                unit.speed = 0.0
                unit.waypoints = []
            elif not unit.waypoints:
                unit.waypoints = _route_to(unit, obj.lat, obj.lon, neutral_countries)
                unit.speed = _transit_speed(unit)
                m.status = MissionStatus.EN_ROUTE

        elif m.type == MissionType.PATROL:
            obj = objectives.get(m.objective_id or "")
            if obj is None:
                continue
            radius = PATROL_RADIUS_KM[unit.unit_class.value]

            if not unit.waypoints:
                unit.waypoints = _routed_patrol_circuit(
                    unit, obj.lat, obj.lon, radius, neutral_countries
                )
                unit.speed = _transit_speed(unit)
                m.status = MissionStatus.ON_STATION

        elif m.type == MissionType.AREA_PATROL:
            if m.patrol_lat is None or m.patrol_lon is None:
                continue  # mission not fully specified
            radius = PATROL_RADIUS_KM[unit.unit_class.value]
            if not unit.waypoints:
                unit.waypoints = _routed_patrol_circuit(
                    unit, m.patrol_lat, m.patrol_lon, radius, neutral_countries
                )
                unit.speed = _transit_speed(unit)
                m.status = MissionStatus.ON_STATION

        elif m.type == MissionType.INTERCEPT:
            target = _nearest_valid_intercept_target(
                unit, units,
                network_ids=network_ids.get(unit.side.value),
                gci_ids=gci_ids.get(unit.side.value),
            )
            if target is None:
                # No enemy in sensor picture yet — hold orbit and wait for detection.
                if unit.unit_class == UnitClass.AIR and unit.airborne:
                    if not unit.waypoints:
                        unit.waypoints = _patrol_circuit(unit.lat, unit.lon, HOLDING_RADIUS_KM)
                    unit.speed = _transit_speed(unit)
                continue

            if target.unit_class.value in ("ground", "naval"):
                _set_altitude_phase(unit, "ingress")

            dist = haversine(unit.lat, unit.lon, target.lat, target.lon)
            w_range = weapon_range(unit, target.unit_class.value)

            if dist <= w_range:
                m.status = MissionStatus.ON_STATION
                if unit.unit_class == UnitClass.AIR and unit.airborne:
                    if target.unit_class.value in ("ground", "naval"):
                        # Strike platform on station: egress immediately once strike ammo is gone.
                        # While ammo remains, orbit at current standoff position and keep firing.
                        relevant_ammo = unit.magazines.get("ag", 0) + unit.magazines.get("as", 0)
                        if unit.magazines and relevant_ammo == 0:
                            egress_hdg = bearing(target.lat, target.lon, unit.lat, unit.lon)
                            egress_lat, egress_lon = destination(unit.lat, unit.lon, egress_hdg, EGRESS_KM)
                            unit.waypoints = _route_to(unit, egress_lat, egress_lon, neutral_countries)
                            unit.speed = unit.max_speed
                        elif not unit.waypoints:
                            unit.waypoints = _patrol_circuit(unit.lat, unit.lon, HOLDING_RADIUS_KM)
                            unit.speed = _transit_speed(unit)
                    else:
                        # Air-to-air: orbit for continuous BVR engagement
                        if not unit.waypoints:
                            unit.waypoints = _patrol_circuit(unit.lat, unit.lon, HOLDING_RADIUS_KM)
                            unit.speed = _transit_speed(unit)
                else:
                    # Ground/naval: stop at standoff position and fire from there
                    unit.waypoints = []
                    unit.speed = 0.0
            else:
                # Not yet in weapon range.
                # Route to the threat-scored standoff point rather than the target's exact
                # position.  This ensures the unit stops firing from weapon range — the
                # waypoint is naturally consumed on arrival so the on-station orbit triggers
                # cleanly on the following tick without the "stuck en-route" bug.
                standoff_pt = _standoff_approach_point(
                    unit, target.lat, target.lon, w_range * STANDOFF_FACTOR, units
                )
                unit.waypoints = _route_to(unit, standoff_pt[0], standoff_pt[1], neutral_countries)
                # Sprint to close on enemy air; cruise on strike ingress to conserve fuel
                unit.speed = unit.max_speed if target.unit_class == UnitClass.AIR else _transit_speed(unit)
                m.status = MissionStatus.EN_ROUTE

        elif m.type == MissionType.ESCORT:
            charge = units.get(m.target_unit_id or "")
            if charge is None or charge.destroyed:
                # Escort target gone — hold position
                m.status = MissionStatus.ON_STATION
                unit.speed = 0.0
                unit.waypoints = []
            else:
                # Position on the left flank of the charge at offset_km separation
                offset_km = ESCORT_OFFSET_AIR_KM if unit.unit_class == UnitClass.AIR else ESCORT_OFFSET_NAVAL_KM
                flank_bearing = (charge.heading + 270.0) % 360.0  # left-flank offset
                ideal_lat, ideal_lon = destination(charge.lat, charge.lon, flank_bearing, offset_km)
                dist_to_ideal = haversine(unit.lat, unit.lon, ideal_lat, ideal_lon)
                on_station_r = ON_STATION_RADIUS_KM[unit.unit_class.value] * 2.0

                if dist_to_ideal <= on_station_r:
                    m.status = MissionStatus.ON_STATION
                    if unit.unit_class == UnitClass.AIR:
                        # Orbit in a tight CAP pattern centred on the escort position
                        if not unit.waypoints:
                            unit.waypoints = _patrol_circuit(ideal_lat, ideal_lon, HOLDING_RADIUS_KM)
                            unit.speed = _transit_speed(unit)
                    else:
                        # Naval escort: maintain position matching the charge
                        unit.waypoints = [(ideal_lat, ideal_lon)]
                        unit.speed = min(_transit_speed(unit), max(charge.speed, _transit_speed(unit) * 0.3))
                else:
                    m.status = MissionStatus.EN_ROUTE
                    unit.waypoints = _route_to(unit, ideal_lat, ideal_lon, neutral_countries)
                    unit.speed = _transit_speed(unit)

        elif m.type == MissionType.RTB:
            lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
            rearm_ticks = lib.get("rearm_ticks",
                                  _REARM_TICKS_DEFAULT.get(unit.unit_class.value, 8))

            # Ground units and emplaced SAMs (max_speed == 0) rearm in place
            if unit.unit_class == UnitClass.GROUND or unit.max_speed == 0:
                m.status = MissionStatus.ON_STATION
                unit.speed = 0.0
                unit.waypoints = []
                unit.awaiting_loadout = True
                unit.loadout_selection_ticks_left = LOADOUT_SELECTION_TICKS
                continue

            # Air and naval: route to nearest usable base.
            # Priority 1 → nearest friendly-controlled base (full rearm + refuel).
            # Priority 2 → nearest neutral (non-enemy) base (refuel only, no rearm).
            # Priority 3 → static home_base coords if set (e.g. non-objective strips).
            # Captured airfields drop off automatically when controlling_side flips.
            dest_base = _nearest_friendly_base(unit, objectives)
            if dest_base is not None:
                unit.refuel_only = False
                rtb_lat, rtb_lon = dest_base.lat, dest_base.lon
            else:
                emerg = _nearest_emergency_base(unit, objectives)
                if emerg is not None:
                    unit.refuel_only = True   # neutral: refuel only, no magazine reload
                    rtb_lat, rtb_lon = emerg.lat, emerg.lon
                elif unit.home_base_lat is not None and unit.home_base_lon is not None:
                    unit.refuel_only = False  # assume home base is friendly
                    rtb_lat, rtb_lon = unit.home_base_lat, unit.home_base_lon
                else:
                    # Nowhere to go — hold position
                    unit.mission = None
                    unit.speed = 0.0
                    unit.waypoints = []
                    continue
            dist = haversine(unit.lat, unit.lon, rtb_lat, rtb_lon)
            arrive_radius = ON_STATION_RADIUS_KM[unit.unit_class.value] * 2

            if dist <= arrive_radius:
                m.status = MissionStatus.ON_STATION
                unit.speed = 0.0
                unit.waypoints = []
                if unit.unit_class == UnitClass.AIR:
                    unit.airborne = False  # landed
                unit.awaiting_loadout = True
                unit.loadout_selection_ticks_left = LOADOUT_SELECTION_TICKS
                # rearm_ticks applied in _burn_resources when the window closes
            else:
                unit.waypoints = _route_to(unit, rtb_lat, rtb_lon, neutral_countries)
                unit.speed = _transit_speed(unit)
                m.status = MissionStatus.EN_ROUTE

        # Safety net: an airborne air unit must never be stationary.
        # Any mission path that exits without setting speed gets caught here.
        if (unit.unit_class == UnitClass.AIR
                and unit.airborne
                and unit.fuel_pct > 0.0
                and unit.speed == 0.0):
            if not unit.waypoints:
                unit.waypoints = _patrol_circuit(unit.lat, unit.lon, HOLDING_RADIUS_KM)
            unit.speed = _transit_speed(unit)

    return events
