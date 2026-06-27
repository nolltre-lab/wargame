from typing import Dict, List, Tuple, Optional
from .unit import Unit, Mission, MissionType, MissionStatus, UnitClass
from .objective import Objective
from .geo import haversine, destination, naval_waypoints as _naval_wps
from .combat import weapon_range, valid_targets as unit_valid_targets, UNIT_TYPE_LIB

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

# Fuel level at which air/naval units automatically RTB (enough reserve to reach base)
BINGO_FUEL_PCT = 25.0


def _patrol_circuit(center_lat: float, center_lon: float, radius_km: float) -> List[Tuple[float, float]]:
    return [
        destination(center_lat, center_lon, (360.0 / PATROL_POINTS) * i, radius_km)
        for i in range(PATROL_POINTS)
    ]


def _nearest_valid_intercept_target(unit: Unit, all_units: Dict[str, Unit]) -> Optional[Unit]:
    """Nearest enemy that this unit class can legitimately move to engage.

    Naval units only chase naval targets — they engage air/ground at range
    without repositioning (prevents ships routing overland after aircraft).
    Air and ground units chase any valid target.
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
    return min(candidates, key=lambda u: haversine(unit.lat, unit.lon, u.lat, u.lon))


def _route_to(
    unit: Unit,
    dest_lat: float,
    dest_lon: float,
    corridors: List[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    """Return waypoints from the unit's current position to dest, avoiding land for naval units."""
    if unit.unit_class == UnitClass.NAVAL:
        return _naval_wps(unit.lat, unit.lon, dest_lat, dest_lon, corridors)
    return [(dest_lat, dest_lon)]


def _is_winchester(unit: Unit) -> bool:
    """True when all magazine categories are depleted (and the unit has a loadout at all)."""
    return bool(unit.magazines) and all(v == 0 for v in unit.magazines.values())


def _should_auto_rtb(unit: Unit) -> Optional[str]:
    """
    Return the reason string if the unit should automatically RTB, else None.
    Only air and naval units auto-RTB — ground units restock in place on player command.
    """
    if unit.unit_class not in (UnitClass.AIR, UnitClass.NAVAL):
        return None
    if 0 < unit.fuel_pct <= BINGO_FUEL_PCT:
        return "bingo"
    if _is_winchester(unit):
        return "winchester"
    return None


def resolve_missions(
    units: Dict[str, Unit],
    objectives: Dict[str, Objective],
    corridors: List[Tuple[float, float]],
) -> List[dict]:
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
                unit.waypoints = _route_to(unit, obj.lat, obj.lon, corridors)
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
            target = _nearest_valid_intercept_target(unit, units)
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
                unit.waypoints = _route_to(unit, target.lat, target.lon, corridors)
                unit.speed = unit.max_speed
                m.status = MissionStatus.EN_ROUTE

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
                unit.waypoints = _route_to(unit, unit.home_base_lat, unit.home_base_lon, corridors)
                unit.speed = unit.max_speed
                m.status = MissionStatus.EN_ROUTE

    return events
