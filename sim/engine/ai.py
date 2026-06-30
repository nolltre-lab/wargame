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

# Minimum fuel safety buffer added on top of the calculated transit cost (%)
BINGO_SAFETY_BUFFER_PCT = 12.0

# Tick duration in sim-seconds (used to convert speed → km/tick for fuel estimation)
_TICK_DURATION_S = 60.0

# Fallback static bingo threshold when no home base is recorded
_BINGO_FALLBACK_PCT = 25.0

_FUEL_BURN_MOVING = {"air": 1.5, "ground": 0.1, "naval": 0.2}


def _bingo_threshold(unit: Unit) -> float:
    """
    Dynamic bingo fuel level: fuel % needed to reach home base at full speed
    plus BINGO_SAFETY_BUFFER_PCT.  Falls back to _BINGO_FALLBACK_PCT when the
    unit has no home base or speed is unknown.
    """
    if unit.home_base_lat is None or unit.home_base_lon is None:
        return _BINGO_FALLBACK_PCT

    dist_km = haversine(unit.lat, unit.lon, unit.home_base_lat, unit.home_base_lon)
    speed_kmh = unit.max_speed if unit.max_speed > 0 else 500.0
    km_per_tick = speed_kmh * (_TICK_DURATION_S / 3600.0)

    lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
    burn = lib.get("fuel_burn_per_tick",
                   _FUEL_BURN_MOVING.get(unit.unit_class.value, 1.5))

    ticks_needed = dist_km / km_per_tick if km_per_tick > 0 else 0.0
    return ticks_needed * burn + BINGO_SAFETY_BUFFER_PCT

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
    return min(visible, key=lambda u: haversine(unit.lat, unit.lon, u.lat, u.lon))


def _route_to(
    unit: Unit,
    dest_lat: float,
    dest_lon: float,
) -> List[Tuple[float, float]]:
    """
    Return waypoints from the unit's current position to dest. Ground units
    route around water, naval units route around land — both via real
    coastline data (terrain.find_route), valid anywhere on the map. Air units
    fly direct, unconstrained by terrain.
    """
    if unit.unit_class == UnitClass.GROUND:
        return terrain.find_route(unit.lat, unit.lon, dest_lat, dest_lon, domain="land")
    if unit.unit_class == UnitClass.NAVAL:
        return terrain.find_route(unit.lat, unit.lon, dest_lat, dest_lon, domain="water")
    return [(dest_lat, dest_lon)]


def _is_winchester(unit: Unit) -> bool:
    """True when all magazine categories are depleted (and the unit has a loadout at all)."""
    return bool(unit.magazines) and all(v == 0 for v in unit.magazines.values())


def _should_auto_rtb(unit: Unit) -> Optional[str]:
    """
    Return the reason string if the unit should automatically RTB, else None.
    Only air and naval units auto-RTB — ground units restock in place on player command.
    Bingo threshold is dynamic: fuel needed to reach home base + safety buffer.
    """
    if unit.unit_class not in (UnitClass.AIR, UnitClass.NAVAL):
        return None
    if _is_winchester(unit):
        return "winchester"
    if unit.fuel_pct <= 0:
        return None
    if unit.fuel_pct <= _bingo_threshold(unit):
        return "bingo"
    return None


# Evasion maneuver distances (km) per technique
_NOTCH_KM = 80.0   # perpendicular jink to defeat pulse-Doppler radar
_CRANK_KM = 100.0  # angled separation to create range in BVR
_FLEE_KM  = 150.0  # direct retreat for mixed / ground threats

# Notch is held for this many ticks before switching sides (creates realistic sustained jink)
_NOTCH_JINK_PERIOD = 3


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

        # ── Ground auto-restock (winchester → rearm in place immediately) ───────
        if unit.unit_class == UnitClass.GROUND and _is_winchester(unit):
            lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
            rearm_ticks = lib.get("rearm_ticks", _REARM_TICKS_DEFAULT.get("ground", 5))
            unit.rearming = True
            unit.rearm_ticks_left = rearm_ticks
            events.append({
                "type": "winchester",
                "unit_id": unit.id,
                "unit_name": unit.name,
                "side": unit.side.value,
                "tick": None,
            })
            continue  # _burn_resources handles the tick-down

        # ── Air / naval auto-RTB (bingo fuel or winchester) ───────────────────
        m = unit.mission
        already_rtb = m is not None and m.type == MissionType.RTB
        if not already_rtb:
            reason = _should_auto_rtb(unit)
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
                unit.speed = unit.max_speed
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
                unit.waypoints = _route_to(unit, obj.lat, obj.lon)
                unit.speed = unit.max_speed
                m.status = MissionStatus.EN_ROUTE

        elif m.type == MissionType.PATROL:
            obj = objectives.get(m.objective_id or "")
            if obj is None:
                continue
            radius = PATROL_RADIUS_KM[unit.unit_class.value]

            if not unit.waypoints:
                # Regenerates circuit each time — creates continuous looping patrol
                unit.waypoints = _patrol_circuit(obj.lat, obj.lon, radius)
                unit.speed = unit.max_speed
                m.status = MissionStatus.ON_STATION

        elif m.type == MissionType.AREA_PATROL:
            if m.patrol_lat is None or m.patrol_lon is None:
                continue  # mission not fully specified
            radius = PATROL_RADIUS_KM[unit.unit_class.value]
            if not unit.waypoints:
                unit.waypoints = _patrol_circuit(m.patrol_lat, m.patrol_lon, radius)
                unit.speed = unit.max_speed
                m.status = MissionStatus.ON_STATION

        elif m.type == MissionType.INTERCEPT:
            target = _nearest_valid_intercept_target(
                unit, units,
                network_ids=network_ids.get(unit.side.value),
                gci_ids=gci_ids.get(unit.side.value),
            )
            if target is None:
                continue
            dist = haversine(unit.lat, unit.lon, target.lat, target.lon)
            w_range = weapon_range(unit)

            if dist <= w_range:
                # Already in weapon range — hold position, combat handles the rest
                unit.waypoints = []
                unit.speed = 0.0
                m.status = MissionStatus.ON_STATION
            else:
                unit.waypoints = _route_to(unit, target.lat, target.lon)
                unit.speed = unit.max_speed
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
                            unit.speed = unit.max_speed
                    else:
                        # Naval escort: maintain position at low speed matching the charge
                        unit.waypoints = [(ideal_lat, ideal_lon)]
                        unit.speed = min(unit.max_speed, max(charge.speed, unit.max_speed * 0.2))
                else:
                    m.status = MissionStatus.EN_ROUTE
                    unit.waypoints = _route_to(unit, ideal_lat, ideal_lon)
                    unit.speed = unit.max_speed

        elif m.type == MissionType.RTB:
            lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
            rearm_ticks = lib.get("rearm_ticks",
                                  _REARM_TICKS_DEFAULT.get(unit.unit_class.value, 8))

            # Ground units and emplaced SAMs (max_speed == 0) rearm in place
            if unit.unit_class == UnitClass.GROUND or unit.max_speed == 0:
                m.status = MissionStatus.ON_STATION
                unit.speed = 0.0
                unit.waypoints = []
                unit.rearming = True
                unit.rearm_ticks_left = rearm_ticks
                continue

            # Air and naval: fly/sail to home base
            if unit.home_base_lat is None or unit.home_base_lon is None:
                # No home base recorded — clear mission and sit still
                unit.mission = None
                unit.speed = 0.0
                unit.waypoints = []
                continue

            dist = haversine(unit.lat, unit.lon, unit.home_base_lat, unit.home_base_lon)
            arrive_radius = ON_STATION_RADIUS_KM[unit.unit_class.value] * 2

            if dist <= arrive_radius:
                m.status = MissionStatus.ON_STATION
                unit.speed = 0.0
                unit.waypoints = []
                if unit.unit_class == UnitClass.AIR:
                    unit.airborne = False  # landed
                unit.rearming = True
                unit.rearm_ticks_left = rearm_ticks
            else:
                unit.waypoints = _route_to(unit, unit.home_base_lat, unit.home_base_lon)
                unit.speed = unit.max_speed
                m.status = MissionStatus.EN_ROUTE

    return events
