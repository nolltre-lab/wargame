from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from .unit import Unit

_LIB_PATH = Path(__file__).parent.parent / "data" / "unit_types.json"

# Strip the _comment key so lookups are clean
_raw = json.loads(_LIB_PATH.read_text())
UNIT_TYPE_LIB: Dict[str, dict] = {k: v for k, v in _raw.items() if not k.startswith("_")}

# Fallback capabilities when unit_type is unknown or absent
_CLASS_DEFAULTS: Dict[str, dict] = {
    "air":    {"sensor_km": 100.0, "weapon_km": 60.0,  "attack_per_tick": 25.0, "default_hp": 80.0},
    "ground": {"sensor_km": 8.0,   "weapon_km": 3.0,   "attack_per_tick": 15.0, "default_hp": 100.0},
    "naval":  {"sensor_km": 50.0,  "weapon_km": 30.0,  "attack_per_tick": 25.0, "default_hp": 150.0},
}

# Which unit classes can engage which
_VALID_TARGETS: Dict[str, List[str]] = {
    "air":    ["air", "ground", "naval"],
    "ground": ["ground", "naval"],
    "naval":  ["naval", "ground", "air"],
}


def caps(unit: Unit) -> dict:
    """Return capability dict for a unit, preferring library lookup over class defaults."""
    lib = UNIT_TYPE_LIB.get(unit.unit_type)
    if lib:
        return lib
    return _CLASS_DEFAULTS.get(unit.unit_class.value, _CLASS_DEFAULTS["ground"])


def default_hp(unit: Unit) -> float:
    return caps(unit)["default_hp"]


def sensor_range(unit: Unit) -> float:
    return caps(unit)["sensor_km"]


def weapon_range(unit: Unit) -> float:
    return caps(unit)["weapon_km"]


def valid_targets(unit: Unit) -> List[str]:
    """Unit classes this unit can engage. Library entry overrides class default."""
    c = caps(unit)
    if "valid_targets" in c:
        return c["valid_targets"]
    return _VALID_TARGETS.get(unit.unit_class.value, [])


def resolve_combat(units: Dict[str, Unit]) -> List[dict]:
    """
    Two-phase combat resolution for one tick:
      1. Collect all (attacker, target, damage) pairs simultaneously.
      2. Apply all damage at once to avoid iteration-order bias.
    Returns a list of event dicts broadcast to clients.
    """
    active = [u for u in units.values() if not u.destroyed]
    enemy_side: Dict[str, str] = {"blue": "red", "red": "blue"}

    # Phase 1 — find engagements
    attacks: List[Tuple[Unit, Unit, float]] = []
    for attacker in active:
        target_classes = valid_targets(attacker)
        w_range = weapon_range(attacker)
        e_side = enemy_side[attacker.side.value]

        candidates: List[Tuple[float, Unit]] = []
        for other in active:
            if other.side.value != e_side:
                continue
            if other.unit_class.value not in target_classes:
                continue
            from .geo import haversine
            dist = haversine(attacker.lat, attacker.lon, other.lat, other.lon)
            if dist <= w_range:
                candidates.append((dist, other))

        if candidates:
            candidates.sort(key=lambda x: x[0])
            nearest = candidates[0][1]
            damage = float(caps(attacker)["attack_per_tick"])
            attacks.append((attacker, nearest, damage))

    # Phase 2 — apply damage
    events: List[dict] = []
    for attacker, target, damage in attacks:
        if target.destroyed:
            continue
        target.hp = max(0.0, target.hp - damage)
        events.append({
            "type": "engagement",
            "attacker_id": attacker.id,
            "attacker_name": attacker.name,
            "target_id": target.id,
            "target_name": target.name,
            "damage": damage,
            "target_hp": target.hp,
            "target_max_hp": target.max_hp,
        })
        if target.hp <= 0.0:
            target.destroyed = True
            target.speed = 0.0
            target.waypoints = []
            target.mission = None
            events.append({
                "type": "destroyed",
                "unit_id": target.id,
                "unit_name": target.name,
                "side": target.side.value,
                "tick": None,  # filled in by SimulationEngine
            })

    return events
