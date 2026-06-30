from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Set, Tuple

from .unit import Unit
from .geo import haversine, bearing as geo_bearing

_LIB_PATH = Path(__file__).parent.parent / "data" / "unit_types.json"

# Strip the _comment key so lookups are clean
_raw = json.loads(_LIB_PATH.read_text())
UNIT_TYPE_LIB: Dict[str, dict] = {k: v for k, v in _raw.items() if not k.startswith("_")}

# Detection model constants
ESM_FACTOR = 2.5      # emitting unit visible to ESM at this multiple of its own radar range
REFERENCE_RCS = 5.0   # m² — sensor_km values in library are calibrated vs this target

# Pulse-Doppler notch parameters per attacker class.
# When a target flies perpendicular to the radar LOS, its Doppler shift ≈ 0 and
# the receiver filters it as ground clutter.  "depth" = fraction of normal range
# retained at perfect notch; "half_band_deg" = half-width of the notch band.
_NOTCH_PARAMS: Dict[str, dict] = {
    "air":    {"depth": 0.15, "half_band_deg": 12},  # modern look-down/shoot-down radar
    "naval":  {"depth": 0.22, "half_band_deg": 10},  # phased-array ship SAM radar
    "ground": {"depth": 0.25, "half_band_deg": 10},  # SAM battery (S-300/400, Patriot)
}

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


def radar_horizon_km(h_sensor_m: float, h_target_m: float) -> float:
    """Radar line-of-sight limit (km) via 4/3-earth model."""
    return 4.12 * (max(0.0, h_sensor_m) ** 0.5 + max(0.0, h_target_m) ** 0.5)


def notch_factor(attacker: Unit, target: Unit) -> float:
    """
    Pulse-Doppler notch degradation: returns a detection-range multiplier in [depth..1.0].

    Physics: when a target flies perpendicular to the radar line-of-sight its radial
    velocity ≈ 0.  Pulse-Doppler receivers reject near-zero Doppler returns as ground
    clutter, so the target effectively disappears from the radar picture.

    Only affects moving airborne targets (ground/naval targets have no meaningful Doppler
    notch benefit).  ESM detection is *not* affected — notching defeats reflected radar
    energy but the target's own emissions are still visible.
    """
    if target.unit_class.value != "air" or not target.airborne or target.speed < 50.0:
        return 1.0
    params = _NOTCH_PARAMS.get(attacker.unit_class.value)
    if params is None:
        return 1.0

    # Angle between target heading and the radar LOS (attacker → target)
    los_brng = geo_bearing(attacker.lat, attacker.lon, target.lat, target.lon)
    doppler_angle = abs((target.heading - los_brng + 180) % 360 - 180)
    # 0° = closing head-on, 90° = perpendicular (notch), 180° = fleeing directly away
    notch_dev = abs(doppler_angle - 90.0)   # 0 = perfect notch

    if notch_dev <= params["half_band_deg"]:
        t = notch_dev / params["half_band_deg"]     # 0 at centre, 1 at band edge
        return params["depth"] + (1.0 - params["depth"]) * t
    return 1.0


def _effective_altitude(unit: Unit) -> float:
    """Altitude for horizon calc. Air units on the ground use ~5m (airfield)."""
    if unit.unit_class.value == "air" and not unit.airborne:
        return 5.0
    return unit.altitude_m


def _radar_range(scanner: Unit, target: Unit) -> float:
    """
    Pure radar detection range — affected by horizon, RCS, and the Doppler notch.
    This is also the weapon-guidance range: radar-guided missiles need this lock.
    """
    base = sensor_range(scanner)
    horizon = radar_horizon_km(_effective_altitude(scanner), _effective_altitude(target))
    rcs_scale = min(1.0, (max(1e-4, target.rcs) / REFERENCE_RCS) ** 0.25)
    return min(base, horizon) * rcs_scale * notch_factor(scanner, target)


def unit_detection_range(scanner: Unit, target: Unit) -> float:
    """
    Situational-awareness range: the farther of radar or passive ESM detection.
    Used for FOW picture and knowing a target exists/where it is.
    ESM is NOT affected by the target's notch maneuver — it detects the target's
    own radar emissions, not reflected energy.
    """
    radar_rng = _radar_range(scanner, target)
    esm_rng = min(sensor_range(scanner), sensor_range(target) * ESM_FACTOR) if target.emcon else 0.0
    return max(radar_rng, esm_rng)


def unit_engagement_range(scanner: Unit, target: Unit) -> float:
    """
    Weapon-guidance range: the radar must hold lock for the missile to guide.
    A target in the Doppler notch is detectable via ESM (unit_detection_range)
    but cannot be engaged — the seeker filters it as ground clutter.
    """
    return _radar_range(scanner, target)


def valid_targets(unit: Unit) -> List[str]:
    """Unit classes this unit can engage. Library entry overrides class default."""
    c = caps(unit)
    if "valid_targets" in c:
        return c["valid_targets"]
    return _VALID_TARGETS.get(unit.unit_class.value, [])


# Maps target class to the magazine key consumed when engaging it
_MAG_KEY: Dict[str, str] = {"air": "aa", "ground": "ag", "naval": "as"}


def build_detection_picture(units: Dict[str, Unit]) -> Dict[str, Set[str]]:
    """
    Fog-of-war picture: for each side, the set of enemy unit IDs detected by
    ANY friendly unit (regardless of data-link status). Used for UI perspective
    filtering; separate from the combat detection gate.
    """
    active = [u for u in units.values() if not u.destroyed]
    enemy_side: Dict[str, str] = {"blue": "red", "red": "blue"}
    detected: Dict[str, Set[str]] = {"blue": set(), "red": set()}
    for scanner in active:
        e_side = enemy_side[scanner.side.value]
        for target in active:
            if target.side.value != e_side:
                continue
            if haversine(scanner.lat, scanner.lon, target.lat, target.lon) <= unit_detection_range(scanner, target):
                detected[scanner.side.value].add(target.id)
    return detected


def resolve_combat(units: Dict[str, Unit]) -> Tuple[List[dict], Dict[str, List[str]]]:
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
        e_side = enemy_side[scanner.side.value]
        for target in active:
            if target.side.value != e_side:
                continue
            if haversine(scanner.lat, scanner.lon, target.lat, target.lon) <= unit_detection_range(scanner, target):
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

            # Gate 1 — situational awareness: does the attacker know the target exists?
            # ESM-augmented; data-link extends the pool.  Notch does not defeat this.
            in_own_sensor = dist <= unit_detection_range(attacker, other)
            in_network = has_data_link and other.id in network_detected[attacker.side.value]
            if not (in_own_sensor or in_network):
                continue  # completely unaware — can't engage

            # Gate 2 — weapon guidance: can the radar hold lock to guide the weapon?
            # The Doppler notch defeats pulse-Doppler seekers even if position is known.
            # Non-guided weapons (artillery, guns) fire within weapon range regardless —
            # they use GPS/ballistic fire-for-effect, not continuous radar guidance.
            needs_radar_lock = other.unit_class.value == "air" and other.airborne
            if needs_radar_lock and dist > unit_engagement_range(attacker, other):
                continue  # target in notch — seeker can't lock, no firing solution

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

    # Build under-fire map: victim_id → [attacker_ids] (from phase 1 results)
    under_fire: Dict[str, List[str]] = {}
    for attacker, target, _ in attacks:
        under_fire.setdefault(target.id, []).append(attacker.id)

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

    return events, under_fire
