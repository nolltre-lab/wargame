from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Set, Tuple

from .unit import Unit
from .geo import haversine

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
    if unit.weapon_km_override is not None:
        return unit.weapon_km_override
    return caps(unit)["weapon_km"]


def valid_targets(unit: Unit) -> List[str]:
    """Unit classes this unit can engage. Library entry overrides class default."""
    c = caps(unit)
    if "valid_targets" in c:
        return c["valid_targets"]
    return _VALID_TARGETS.get(unit.unit_class.value, [])


# Maps target class to the magazine key consumed when engaging it
_MAG_KEY: Dict[str, str] = {"air": "aa", "ground": "ag", "naval": "as"}


def resolve_combat(units: Dict[str, Unit]) -> List[dict]:
    """
    Two-phase combat resolution for one tick:
      1. Collect all (attacker, target, damage) pairs simultaneously.
      2. Apply all damage at once to avoid iteration-order bias.
    Returns a list of event dicts broadcast to clients.
    Units that are rearming or have no ammo for the target class cannot fire.

    Networked detection: data-linked units share their sensor picture.
    Any data-linked unit may engage a target detected by ANY data-linked
    friendly, as long as the target is within the attacker's own weapon range.
    Non-data-linked units engage anything within weapon range (no detection gate).
    """
    from .geo import haversine
    active = [u for u in units.values() if not u.destroyed and not u.rearming]
    enemy_side: Dict[str, str] = {"blue": "red", "red": "blue"}

    # Build per-side network picture: set of enemy unit IDs detectable by
    # any data-linked friendly unit.
    network_detected: Dict[str, set] = {"blue": set(), "red": set()}
    for scanner in active:
        if not UNIT_TYPE_LIB.get(scanner.unit_type, {}).get("data_link", False):
            continue
        s_range = sensor_range(scanner)
        e_side = enemy_side[scanner.side.value]
        for target in active:
            if target.side.value != e_side:
                continue
            if haversine(scanner.lat, scanner.lon, target.lat, target.lon) <= s_range:
                network_detected[scanner.side.value].add(target.id)

    # Phase 1 — find engagements
    attacks: List[Tuple[Unit, Unit, float]] = []
    for attacker in active:
        target_classes = valid_targets(attacker)
        w_range = weapon_range(attacker)
        e_side = enemy_side[attacker.side.value]
        has_data_link = UNIT_TYPE_LIB.get(attacker.unit_type, {}).get("data_link", False)

        candidates: List[Tuple[float, Unit]] = []
        for other in active:
            if other.side.value != e_side:
                continue
            if other.unit_class.value not in target_classes:
                continue
            dist = haversine(attacker.lat, attacker.lon, other.lat, other.lon)

            # Detection gate for data-linked units: own sensor OR network picture.
            # Non-data-linked units fire on anything in weapon range (no detection gate).
            if has_data_link:
                in_own_sensor = dist <= sensor_range(attacker)
                in_network = other.id in network_detected[attacker.side.value]
                if not (in_own_sensor or in_network):
                    continue

            if dist <= w_range:
                candidates.append((dist, other))

        if not candidates:
            continue
        candidates.sort(key=lambda x: x[0])
        nearest = candidates[0][1]

        # Skip if out of ammo for this target class (empty dict = unlimited)
        mag_key = _MAG_KEY.get(nearest.unit_class.value, "ag")
        if attacker.magazines and attacker.magazines.get(mag_key, -1) == 0:
            continue

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

        # Consume one round of the appropriate magazine
        mag_key = _MAG_KEY.get(target.unit_class.value, "ag")
        if attacker.magazines and mag_key in attacker.magazines:
            before = attacker.magazines[mag_key]
            attacker.magazines[mag_key] = max(0, before - 1)
            if before > 0 and attacker.magazines[mag_key] == 0:
                events.append({
                    "type": "out_of_ammo",
                    "unit_id": attacker.id,
                    "unit_name": attacker.name,
                    "side": attacker.side.value,
                    "ammo_type": mag_key,
                    "tick": None,  # filled in by SimulationEngine
                })

    return events
