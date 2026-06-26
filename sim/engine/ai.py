from typing import Dict, List, Tuple, Optional
from .unit import Unit, MissionType, MissionStatus, UnitClass
from .objective import Objective
from .geo import haversine, destination
from .combat import weapon_range, valid_targets as unit_valid_targets

# Radius within which a unit is considered "on station" at an objective
ON_STATION_RADIUS_KM: Dict[str, float] = {
    UnitClass.AIR: 3.0,
    UnitClass.GROUND: 2.0,
    UnitClass.NAVAL: 5.0,
}

# Radius of the patrol circuit around an objective center
PATROL_RADIUS_KM: Dict[str, float] = {
    UnitClass.AIR: 40.0,
    UnitClass.GROUND: 6.0,
    UnitClass.NAVAL: 25.0,
}

PATROL_POINTS = 4


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

    # Naval units must not chase non-naval targets (no terrain masking yet)
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


def resolve_missions(units: Dict[str, Unit], objectives: Dict[str, Objective]) -> None:
    for unit in units.values():
        if unit.destroyed:
            continue
        m = unit.mission
        if m is None:
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
                unit.waypoints = [(obj.lat, obj.lon)]
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
                unit.waypoints = [(target.lat, target.lon)]
                unit.speed = unit.max_speed
                m.status = MissionStatus.EN_ROUTE
