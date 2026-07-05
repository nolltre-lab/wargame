from datetime import datetime, timedelta
from typing import Dict, Any, List
import json
import math
import random
from pathlib import Path

from .unit import Unit, Mission, MissionType, MissionStatus
from .missile import Missile
from .objective import Objective
from .geo import haversine, bearing, destination
from .ai import resolve_missions
from .combat import (
    resolve_combat, build_detection_picture, default_hp, sensor_range,
    weapon_range, valid_targets as unit_vtargets, UNIT_TYPE_LIB,
    missile_detection_range,
)
from .commander import Commander
from . import terrain


class SimulationEngine:
    def __init__(self) -> None:
        self.units: Dict[str, Unit] = {}
        self.objectives: Dict[str, Objective] = {}
        self.missiles: Dict[str, Missile] = {}
        self.sim_time: datetime = datetime.utcnow()
        self.tick_duration: float = 60.0
        self.speed_multiplier: float = 1.0
        self.running: bool = False
        self.tick_count: int = 0
        self._recent_events: List[dict] = []
        self._detection_picture: Dict[str, set] = {"blue": set(), "red": set()}
        self._gci_picture: Dict[str, set] = {"blue": set(), "red": set()}
        self._missile_detection: Dict[str, set] = {"blue": set(), "red": set()}
        self.commanders: Dict[str, Commander] = {
            "blue": Commander("blue"),
            "red":  Commander("red"),
        }
        # Countries not in any coalition — air units must route around their airspace
        self.neutral_countries: set = set()

    def load_scenario(self, path: str) -> None:
        data = json.loads(Path(path).read_text())
        self.sim_time = datetime.fromisoformat(data["start_time"].replace("Z", "+00:00"))
        self.tick_duration = float(data.get("tick_duration_seconds", 60.0))
        self.objectives = {o["id"]: Objective(**o) for o in data.get("objectives", [])}

        units: Dict[str, Unit] = {}
        for u in data["units"]:
            unit = Unit(**u)
            # Set HP from unit type library if not explicitly provided in scenario
            if "hp" not in u or "max_hp" not in u:
                hp = default_hp(unit)
                unit.hp = u.get("hp", hp)
                unit.max_hp = u.get("max_hp", hp)
            # Set max_speed from library if not explicitly provided
            if "max_speed" not in u and unit.unit_type:
                lib = UNIT_TYPE_LIB.get(unit.unit_type)
                if lib and "max_speed_kmh" in lib:
                    unit.max_speed = lib["max_speed_kmh"]
            # Initialize magazines from loadout preset
            # Auto-assign first preset if loadout not set (covers old/incomplete scenarios)
            if unit.unit_type:
                lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
                presets = lib.get("loadout_presets", {})
                if presets and not unit.loadout:
                    unit.loadout = next(iter(presets))
                if unit.loadout and unit.loadout in presets:
                    preset = presets[unit.loadout]
                    if preset.get("magazines"):
                        unit.magazines = dict(preset["magazines"])
                    if "weapon_km" in preset:
                        unit.weapon_km_override = float(preset["weapon_km"])

            # Stamp data_link, altitude_m, rcs from library (not settable in scenario JSON)
            if unit.unit_type:
                lib_entry = UNIT_TYPE_LIB.get(unit.unit_type, {})
                unit.data_link = bool(lib_entry.get("data_link", False))
                unit.is_surveillance = bool(lib_entry.get("is_surveillance", False))
                arc = lib_entry.get("sensor_arc_deg")
                unit.sensor_arc_deg = int(arc) if arc is not None else None
                unit.sensor_bi_cone = bool(lib_entry.get("sensor_bi_cone", False))
                unit.altitude_m = float(lib_entry.get("altitude_m", 100.0))
                unit.rcs = float(lib_entry.get("rcs", 5.0))
            # Loaded missions always start EN_ROUTE so the unit begins executing immediately
            if unit.mission:
                unit.mission.status = MissionStatus.EN_ROUTE
            unit.emcon = True  # all units actively emitting; AI will manage EMCON later

            # Always start at full fuel, not rearming
            unit.fuel_pct = 100.0
            unit.rearming = False
            unit.rearm_ticks_left = 0

            units[unit.id] = unit
        self.units = units

        # Apply coalition assignments: objectives whose controlling_side is still
        # null get a side from the scenario's coalitions map (country → side).
        # Explicit controlling_side values in the JSON are never overridden.
        coalitions = data.get("coalitions", {})
        country_to_side: dict = {}
        for side, countries in coalitions.items():
            for c in countries:
                country_to_side[c.lower()] = side
        for obj in self.objectives.values():
            if obj.controlling_side is None and obj.country:
                obj.controlling_side = country_to_side.get(obj.country.lower())

        # Neutral countries = have objectives with a country tag but are in no coalition
        all_obj_countries = {
            obj.country.lower() for obj in self.objectives.values() if obj.country
        }
        self.neutral_countries = all_obj_countries - set(country_to_side.keys())

        # Load side goals and initialise commanders
        goals_data = data.get("goals", {})
        for side in ("blue", "red"):
            self.commanders[side].set_goals(goals_data.get(side, []))

        self.missiles = {}
        self._recent_events = []
        self.tick_count = 0
        self.running = False

    # Fuel burn defaults (% per tick) when not specified in unit_types.json
    _FUEL_MOVING = {"air": 1.5, "ground": 0.1, "naval": 0.2}
    _FUEL_IDLE   = {"air": 0.2, "ground": 0.02, "naval": 0.05}
    _REARM_TICKS = {"air": 8,   "ground": 5,    "naval": 12}

    def tick(self) -> None:
        self._detection_picture = build_detection_picture(self.units)

        events, ground_under_fire, new_missiles = resolve_combat(self.units)
        resource_events = self._burn_resources()
        for e in events + resource_events:
            if e["type"] in ("destroyed", "out_of_ammo", "low_fuel", "rtb_complete"):
                e["tick"] = self.tick_count
        self._recent_events = events + resource_events

        # Register new missiles; route AS missiles around land (sea-skimming path)
        for m in new_missiles:
            self.missiles[m.id] = m
            if m.ammo_type == "as":
                try:
                    wps = terrain.find_route(
                        m.origin_lat, m.origin_lon,
                        m.target_lat, m.target_lon,
                        "water",
                    )
                    if len(wps) > 1:
                        m.waypoints = list(wps)
                        pts = [(m.origin_lat, m.origin_lon)] + list(wps)
                        route_dist = sum(
                            haversine(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
                            for i in range(len(pts) - 1)
                        )
                        km_per_tick = m.speed_kmh / 60.0
                        m.total_ticks = max(2, math.ceil(route_dist / km_per_tick))
                        m.ticks_remaining = m.total_ticks
                except Exception:
                    pass  # fall back to direct flight if routing fails

        # Augment under_fire with units targeted by any in-flight missile
        under_fire = dict(ground_under_fire)
        for m in self.missiles.values():
            if not m.intercepted:
                under_fire.setdefault(m.target_id, []).append(m.firer_id)

        mission_events = resolve_missions(
            self.units, self.objectives, under_fire, self.tick_count,
            self.neutral_countries,
        )
        for e in mission_events:
            e["tick"] = self.tick_count
        self._recent_events.extend(mission_events)

        # Commander AI: re-evaluate goals and assign missions to available units
        for commander in self.commanders.values():
            cmd_events = commander.evaluate(self.units, self.objectives, self.tick_count)
            for e in cmd_events:
                e["tick"] = self.tick_count
            self._recent_events.extend(cmd_events)

        capture_events = self._resolve_objective_control()
        self._recent_events.extend(capture_events)

        for unit in self.units.values():
            if not unit.destroyed and unit.waypoints and unit.speed > 0:
                self._advance_unit(unit)

        # Process in-flight missiles (advance → intercept check → impact)
        self._advance_missiles()
        intercept_events = self._process_missile_intercepts()
        impact_events = self._apply_missile_impacts()
        for e in intercept_events + impact_events:
            e["tick"] = self.tick_count
        self._recent_events.extend(intercept_events + impact_events)

        # Update missile visibility picture for FOW
        self._missile_detection = self._build_missile_detection()

        self.sim_time += timedelta(seconds=self.tick_duration)
        self.tick_count += 1

    def _burn_resources(self) -> List[dict]:
        events: List[dict] = []
        for unit in self.units.values():
            if unit.destroyed:
                continue

            if unit.awaiting_loadout:
                unit.loadout_selection_ticks_left = max(0, unit.loadout_selection_ticks_left - 1)
                if unit.loadout_selection_ticks_left == 0:
                    unit.awaiting_loadout = False
                    lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
                    unit.rearm_ticks_left = lib.get(
                        "rearm_ticks", self._REARM_TICKS.get(unit.unit_class.value, 8)
                    )
                    unit.rearming = True
                continue  # no fuel burn during selection window

            if unit.rearming:
                unit.rearm_ticks_left = max(0, unit.rearm_ticks_left - 1)
                if unit.rearm_ticks_left == 0:
                    unit.rearming = False
                    events.append(self._complete_rearm(unit))
                continue  # no fuel burn while being serviced

            lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
            was_above_20 = unit.fuel_pct > 20.0
            if unit.speed > 0:
                base_burn = lib.get("fuel_burn_per_tick",
                                    self._FUEL_MOVING.get(unit.unit_class.value, 0.5))
                # Apply cruise factor when not at max speed (transit, patrol, RTB).
                # Threshold: 95% of max so rounding/float drift doesn't suppress it.
                if unit.max_speed > 0 and unit.speed < unit.max_speed * 0.95:
                    cruise_factor = lib.get(
                        "cruise_fuel_factor",
                        0.35 if unit.unit_class.value == "air" else
                        0.40 if unit.unit_class.value == "naval" else 1.0
                    )
                    burn = base_burn * cruise_factor
                else:
                    burn = base_burn
            else:
                burn = lib.get("fuel_idle_per_tick",
                               self._FUEL_IDLE.get(unit.unit_class.value, 0.1))
            unit.fuel_pct = max(0.0, unit.fuel_pct - burn)

            if was_above_20 and unit.fuel_pct <= 20.0:
                events.append({
                    "type": "low_fuel",
                    "unit_id": unit.id,
                    "unit_name": unit.name,
                    "side": unit.side.value,
                    "tick": None,
                })
        return events

    def _complete_rearm(self, unit: Unit) -> dict:
        from .unit import UnitClass
        unit.fuel_pct = 100.0
        if not unit.refuel_only:
            lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
            presets = lib.get("loadout_presets", {})
            # Apply pending loadout change if the player or commander requested one
            if unit.pending_loadout and unit.pending_loadout in presets:
                unit.loadout = unit.pending_loadout
                # Also update weapon_km_override from the new preset
                new_preset = presets[unit.loadout]
                if "weapon_km" in new_preset:
                    unit.weapon_km_override = float(new_preset["weapon_km"])
                else:
                    unit.weapon_km_override = None
            unit.pending_loadout = None  # always clear regardless
            preset = presets.get(unit.loadout, {})
            mags = preset.get("magazines", {})
            if mags:
                unit.magazines = dict(mags)
        else:
            unit.pending_loadout = None  # clear even on refuel-only stops
        unit.refuel_only = False  # clear flag regardless

        # Restore previous mission so the unit resumes without player action.
        # Ground units never change their mission during rearm, so leave it as-is.
        if unit.unit_class != UnitClass.GROUND:
            prev = unit.previous_mission
            if prev is not None:
                prev = prev.model_copy()  # don't mutate the stored object
                prev.status = MissionStatus.EN_ROUTE  # always restart fresh — avoids stale ON_STATION
            unit.mission = prev
            if unit.unit_class == UnitClass.AIR and unit.mission is not None:
                unit.airborne = True  # take off
        unit.previous_mission = None
        unit.waypoints = []
        unit.speed = 0.0
        return {
            "type": "rtb_complete",
            "unit_id": unit.id,
            "unit_name": unit.name,
            "side": unit.side.value,
            "tick": None,
        }

    def _resolve_objective_control(self) -> List[dict]:
        """Flip objective controlling_side when a side holds it uncontested."""
        from .unit import UnitClass

        CAPTURE_RADIUS_KM = 5.0
        events: List[dict] = []

        for obj in self.objectives.values():
            if obj.type.value == "maritime":
                continue  # maritime objectives are not captured by ground forces

            blue_present = any(
                u for u in self.units.values()
                if not u.destroyed
                and u.side.value == "blue"
                and u.unit_class == UnitClass.GROUND
                and haversine(u.lat, u.lon, obj.lat, obj.lon) <= CAPTURE_RADIUS_KM
            )
            red_present = any(
                u for u in self.units.values()
                if not u.destroyed
                and u.side.value == "red"
                and u.unit_class == UnitClass.GROUND
                and haversine(u.lat, u.lon, obj.lat, obj.lon) <= CAPTURE_RADIUS_KM
            )

            new_side: str | None
            if blue_present and not red_present:
                new_side = "blue"
            elif red_present and not blue_present:
                new_side = "red"
            else:
                continue  # contested or nobody present — no change

            if new_side != obj.controlling_side:
                obj.controlling_side = new_side
                events.append({
                    "type": "captured",
                    "objective_id": obj.id,
                    "objective_name": obj.name,
                    "side": new_side,
                    "tick": self.tick_count,
                })

        return events

    def _advance_missiles(self) -> None:
        """Move each in-flight missile one tick toward its target."""
        for missile in self.missiles.values():
            if missile.intercepted or missile.ticks_remaining <= 0:
                continue
            dist_this_tick = missile.speed_kmh * (self.tick_duration / 3600.0)

            if missile.waypoints:
                # Waypoint-guided (AS missiles routed around land)
                remaining = dist_this_tick
                while remaining > 0 and missile.waypoints:
                    wp_lat, wp_lon = missile.waypoints[0]
                    d = haversine(missile.lat, missile.lon, wp_lat, wp_lon)
                    if remaining >= d:
                        missile.lat, missile.lon = wp_lat, wp_lon
                        missile.waypoints.pop(0)
                        remaining -= d
                    else:
                        hdg = bearing(missile.lat, missile.lon, wp_lat, wp_lon)
                        missile.heading = hdg
                        missile.lat, missile.lon = destination(missile.lat, missile.lon, hdg, remaining)
                        remaining = 0
                missile.ticks_remaining = max(0, missile.ticks_remaining - 1)
                if not missile.waypoints:
                    missile.ticks_remaining = 0  # last waypoint was the target
            else:
                # Direct flight (AA, AG, ground direct fire)
                dist_to_target = haversine(missile.lat, missile.lon, missile.target_lat, missile.target_lon)
                if dist_this_tick >= dist_to_target:
                    missile.lat = missile.target_lat
                    missile.lon = missile.target_lon
                    missile.ticks_remaining = 0
                else:
                    hdg = bearing(missile.lat, missile.lon, missile.target_lat, missile.target_lon)
                    missile.heading = hdg
                    missile.lat, missile.lon = destination(missile.lat, missile.lon, hdg, dist_this_tick)
                    missile.ticks_remaining -= 1

    def _process_missile_intercepts(self) -> List[dict]:
        """
        SAM units and intercept-capable ships/fighters attempt to shoot down
        in-flight enemy missiles they can detect within weapon range.
        """
        events: List[dict] = []
        enemy_of: Dict[str, str] = {"blue": "red", "red": "blue"}

        for missile in list(self.missiles.values()):
            if missile.intercepted or missile.ticks_remaining <= 0:
                continue
            interceptor_side = enemy_of[missile.side]

            for unit in self.units.values():
                if unit.destroyed or unit.rearming:
                    continue
                if unit.side.value != interceptor_side:
                    continue
                lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
                if not lib.get("intercept_capable", False):
                    continue
                # Interceptor must have AA rounds
                if unit.magazines and unit.magazines.get("aa", -1) == 0:
                    continue
                # Check if interceptor can detect the missile
                det_range = missile_detection_range(unit, missile.altitude_m, missile.rcs)
                dist = haversine(unit.lat, unit.lon, missile.lat, missile.lon)
                if dist > det_range:
                    continue
                # Check weapon range against airborne targets
                w_range = weapon_range(unit, "air")
                if dist > w_range:
                    continue
                # Probability of kill
                base_pk: float = lib.get("intercept_pk", 0.5)
                rcs_mod = min(1.0, (missile.rcs / 0.05) ** 0.25)  # harder to kill low-RCS
                speed_mod = max(0.3, 1.0 - (missile.speed_kmh - 800) / 5000)  # harder vs fast
                final_pk = base_pk * rcs_mod * speed_mod
                if random.random() < final_pk:
                    missile.intercepted = True
                    if unit.magazines and "aa" in unit.magazines:
                        unit.magazines["aa"] = max(0, unit.magazines["aa"] - 1)
                    events.append({
                        "type": "missile_intercept",
                        "interceptor_id": unit.id,
                        "interceptor_name": unit.name,
                        "firer_id": missile.firer_id,
                        "firer_name": missile.firer_name,
                        "missile_type": missile.ammo_type,
                        "side": unit.side.value,
                        "tick": None,
                    })
                    break  # missile destroyed
        return events

    def _apply_missile_impacts(self) -> List[dict]:
        """Apply damage for missiles that have reached their targets; remove completed missiles."""
        events: List[dict] = []
        for mid, missile in list(self.missiles.items()):
            if missile.intercepted:
                del self.missiles[mid]
                continue
            if missile.ticks_remaining > 0:
                continue
            # Impact
            target = self.units.get(missile.target_id)
            if target and not target.destroyed:
                target.hp = max(0.0, target.hp - missile.damage)
                events.append({
                    "type": "engagement",
                    "attacker_id": missile.firer_id,
                    "attacker_name": missile.firer_name,
                    "target_id": target.id,
                    "target_name": target.name,
                    "damage": missile.damage,
                    "target_hp": target.hp,
                    "target_max_hp": target.max_hp,
                    "tick": None,
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
            del self.missiles[mid]
        return events

    def _build_missile_detection(self) -> Dict[str, set]:
        """
        For each side, the set of missile IDs they can see:
        - the firing side always tracks their own missiles
        - the opposing side can see a missile if any of their units can detect it
        """
        detected: Dict[str, set] = {"blue": set(), "red": set()}
        enemy_of: Dict[str, str] = {"blue": "red", "red": "blue"}
        active = [u for u in self.units.values() if not u.destroyed]

        for missile in self.missiles.values():
            if missile.intercepted:
                continue
            # Firing side always knows where their own missiles are
            detected[missile.side].add(missile.id)
            # Enemy side must detect via radar
            e_side = enemy_of[missile.side]
            for scanner in active:
                if scanner.side.value != e_side:
                    continue
                det_range = missile_detection_range(scanner, missile.altitude_m, missile.rcs)
                if haversine(scanner.lat, scanner.lon, missile.lat, missile.lon) <= det_range:
                    detected[e_side].add(missile.id)
                    break
        return detected

    def _advance_unit(self, unit: Unit) -> None:
        if not unit.waypoints:
            return
        if unit.fuel_pct <= 0.0:
            unit.speed = 0.0
            unit.waypoints = []
            return
        wp_lat, wp_lon = unit.waypoints[0]
        dist_to_wp = haversine(unit.lat, unit.lon, wp_lat, wp_lon)
        dist_this_tick = unit.speed * (self.tick_duration / 3600.0)

        if dist_this_tick >= dist_to_wp:
            unit.lat, unit.lon = wp_lat, wp_lon
            unit.waypoints.pop(0)
        else:
            hdg = bearing(unit.lat, unit.lon, wp_lat, wp_lon)
            unit.heading = hdg
            unit.lat, unit.lon = destination(unit.lat, unit.lon, hdg, dist_this_tick)

    def assign_mission(
        self,
        unit_id: str,
        mission_type: str,
        objective_id: str | None,
        patrol_lat: float | None = None,
        patrol_lon: float | None = None,
        target_unit_id: str | None = None,
    ) -> bool:
        from .unit import UnitClass
        unit = self.units.get(unit_id)
        if unit is None or unit.destroyed:
            return False
        unit.waypoints = []
        unit.speed = 0.0
        unit.rearming = False  # cancel any in-progress rearm if re-tasked
        new_mission = Mission(
            type=MissionType(mission_type),
            objective_id=objective_id,
            patrol_lat=patrol_lat,
            patrol_lon=patrol_lon,
            target_unit_id=target_unit_id,
            status=MissionStatus.EN_ROUTE,
        )
        if MissionType(mission_type) == MissionType.RTB:
            unit.previous_mission = unit.mission  # will be restored after rearm
        else:
            unit.previous_mission = None  # explicit new mission clears the saved one
        unit.mission = new_mission
        # Air units taking off for any non-RTB mission become airborne
        if unit.unit_class == UnitClass.AIR and mission_type != MissionType.RTB:
            unit.airborne = True
        return True

    def update_goals(self, side: str, goals: list) -> bool:
        if side not in self.commanders:
            return False
        self.commanders[side].set_goals(goals)
        return True

    def clear_mission(self, unit_id: str) -> bool:
        unit = self.units.get(unit_id)
        if unit is None:
            return False
        unit.mission = None
        unit.waypoints = []
        unit.speed = 0.0
        return True

    def get_state(self) -> Dict[str, Any]:
        unit_states = []
        for u in self.units.values():
            d = u.model_dump()
            d["sensor_km"] = sensor_range(u)
            d["weapon_km"] = weapon_range(u)
            d["valid_targets"] = unit_vtargets(u)
            d["loadout_presets"] = list(UNIT_TYPE_LIB.get(u.unit_type, {}).get("loadout_presets", {}).keys())
            unit_states.append(d)
        return {
            "sim_time": self.sim_time.isoformat(),
            "tick": self.tick_count,
            "running": self.running,
            "units": unit_states,
            "objectives": [o.model_dump() for o in self.objectives.values()],
            "events": self._recent_events,
            "blue_detected": list(self._detection_picture["blue"]),
            "red_detected": list(self._detection_picture["red"]),
            "goals": {
                side: [g.to_dict() for g in cmd.goals]
                for side, cmd in self.commanders.items()
            },
            "missiles": [m.model_dump() for m in self.missiles.values() if not m.intercepted],
            "blue_detected_missiles": list(self._missile_detection["blue"]),
            "red_detected_missiles": list(self._missile_detection["red"]),
            "tick_duration_s": self.tick_duration,
            "speed_multiplier": self.speed_multiplier,
        }
