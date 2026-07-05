from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from typing import Dict, List, Set, Tuple

from .unit import Unit
from .missile import Missile
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

# Missile flight parameters by ammo type.
# unit_types.json "missile_params" entries override per type (e.g. JSM, P-1000 Vulkan).
_MISSILE_DEFAULTS: Dict[str, dict] = {
    "aa": {"speed_kmh": 3600, "altitude_m": 9000, "rcs": 0.02},   # BVR AAM (AMRAAM/R-77 class)
    "ag": {"speed_kmh": 800,  "altitude_m": 100,  "rcs": 0.08},   # Land-attack cruise / glide bomb
    "as": {"speed_kmh": 860,  "altitude_m": 10,   "rcs": 0.05},   # Anti-ship cruise missile
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


_MAG_TO_CLASS: Dict[str, str] = {"aa": "air", "ag": "ground", "as": "naval"}

def weapon_range(unit: Unit, target_class: str = "") -> float:
    """
    Effective weapon range against a target of the given class.

    weapon_km_override is set by loadout presets for a specific weapon type
    (e.g. JSM 280km on anti-ship preset, ATACMS 1500km on strike preset).
    The override only applies when target_class matches the loadout's primary
    ammo type — the one with the most rounds.  For other target classes the
    base weapon_km from the library is used instead, preventing an anti-ship
    F-35 from treating a Flanker as "in range" at 280km.
    """
    if unit.weapon_km_override is not None:
        if target_class and unit.magazines:
            primary_mag = max(unit.magazines, key=lambda k: unit.magazines.get(k, 0))
            if _MAG_TO_CLASS.get(primary_mag) != target_class:
                return caps(unit)["weapon_km"]
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


def missile_detection_range(scanner: Unit, missile_alt_m: float, missile_rcs: float) -> float:
    """
    Radar detection range for a missile-like object.
    Missiles don't emit (no ESM boost), but they may be very low-altitude and low-RCS.
    Notch effect is not modelled — missile seekers aren't pulse-Doppler notch-able.
    """
    base = sensor_range(scanner)
    horizon = radar_horizon_km(_effective_altitude(scanner), missile_alt_m)
    rcs_scale = min(1.0, (max(1e-4, missile_rcs) / REFERENCE_RCS) ** 0.25)
    return min(base, horizon) * rcs_scale


def _make_missile(attacker: Unit, target: Unit, damage: float) -> Missile:
    """Create a Missile entity for an air/naval firing event."""
    ammo_type = _MAG_KEY.get(target.unit_class.value, "ag")
    lib = UNIT_TYPE_LIB.get(attacker.unit_type, {})
    params = {**_MISSILE_DEFAULTS.get(ammo_type, _MISSILE_DEFAULTS["ag"])}
    params.update(lib.get("missile_params", {}).get(ammo_type, {}))
    weapon_label = lib.get("loadout_presets", {}).get(attacker.loadout, {}).get("label", "")
    speed = float(params["speed_kmh"])
    dist = haversine(attacker.lat, attacker.lon, target.lat, target.lon)
    km_per_tick = speed / 60.0  # at 60s per tick
    total_ticks = max(2, math.ceil(dist / km_per_tick))  # min 2 so UI sees it for ≥1 frame
    return Missile(
        id=f"m_{uuid.uuid4().hex[:8]}",
        firer_id=attacker.id,
        firer_name=attacker.name,
        target_id=target.id,
        target_name=target.name,
        side=attacker.side.value,
        weapon_label=weapon_label,
        ammo_type=ammo_type,
        lat=attacker.lat,
        lon=attacker.lon,
        origin_lat=attacker.lat,
        origin_lon=attacker.lon,
        target_lat=target.lat,
        target_lon=target.lon,
        heading=geo_bearing(attacker.lat, attacker.lon, target.lat, target.lon),
        speed_kmh=speed,
        altitude_m=float(params["altitude_m"]),
        rcs=float(params["rcs"]),
        damage=damage,
        ticks_remaining=total_ticks,
        total_ticks=total_ticks,
    )


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


def resolve_combat(
    units: Dict[str, Unit],
) -> Tuple[List[dict], Dict[str, List[str]], List[Missile]]:
    """
    Two-phase combat resolution for one tick.

    Air and naval units launch Missile entities (entities fly to target over
    subsequent ticks, damage applied on arrival).  Ground units apply damage
    immediately as before.

    Returns (events, under_fire, new_missiles).
    under_fire is built from ground engagements only — simulation.py augments
    it with in-flight missiles before passing to resolve_missions.
    """
    from .geo import haversine
    active = [u for u in units.values() if not u.destroyed and not u.rearming and not u.awaiting_loadout]
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

    # Phase 1 — find engagements; split into missiles (air/naval) or direct fire (ground)
    events: List[dict] = []
    attacks: List[Tuple[Unit, Unit, float]] = []   # ground direct-fire
    new_missiles: List[Missile] = []               # air/naval — fly to target

    for attacker in active:
        target_classes = valid_targets(attacker)
        e_side = enemy_side[attacker.side.value]
        has_data_link = UNIT_TYPE_LIB.get(attacker.unit_type, {}).get("data_link", False)

        candidates: List[Tuple[float, Unit]] = []
        for other in active:
            if other.side.value != e_side:
                continue
            if other.unit_class.value not in target_classes:
                continue
            dist = haversine(attacker.lat, attacker.lon, other.lat, other.lon)

            # Gate 1 — situational awareness (ESM-augmented, data-link extended)
            in_own_sensor = dist <= unit_detection_range(attacker, other)
            in_network = has_data_link and other.id in network_detected[attacker.side.value]
            if not (in_own_sensor or in_network):
                continue

            # Gate 2 — weapon guidance (Doppler notch defeats radar lock on airborne targets)
            needs_radar_lock = other.unit_class.value == "air" and other.airborne
            if needs_radar_lock and dist > unit_engagement_range(attacker, other):
                continue

            if dist <= weapon_range(attacker, other.unit_class.value):
                candidates.append((dist, other))

        if not candidates:
            continue
        candidates.sort(key=lambda x: x[0])
        nearest = candidates[0][1]

        mag_key = _MAG_KEY.get(nearest.unit_class.value, "ag")
        if attacker.magazines and attacker.magazines.get(mag_key, -1) == 0:
            continue

        damage = float(caps(attacker)["attack_per_tick"])

        # Air and naval always use missile entities.
        # Ground units with intercept_capable=true also launch visible SAM missiles
        # (so SAM shots appear on the map), but only when engaging air targets.
        uses_missile = attacker.unit_class.value in ("air", "naval") or (
            attacker.unit_class.value == "ground"
            and UNIT_TYPE_LIB.get(attacker.unit_type, {}).get("intercept_capable", False)
            and nearest.unit_class.value == "air"
        )

        if uses_missile:
            # Create missile entity and consume ammo at launch
            new_missiles.append(_make_missile(attacker, nearest, damage))
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
                        "tick": None,
                    })
        else:
            # Ground units engaging ground/naval targets use direct fire
            attacks.append((attacker, nearest, damage))

    # under_fire from ground direct fire (simulation.py adds missile under-fire separately)
    under_fire: Dict[str, List[str]] = {}
    for attacker, target, _ in attacks:
        under_fire.setdefault(target.id, []).append(attacker.id)

    # Phase 2 — apply ground direct damage
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
                "tick": None,
            })
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
                    "tick": None,
                })

    return events, under_fire, new_missiles
