from datetime import datetime, timedelta
from typing import Dict, Any, List
import json
from pathlib import Path

from .unit import Unit, Mission, MissionType, MissionStatus
from .objective import Objective
from .geo import haversine, bearing, destination, BALTIC_NAVAL_CORRIDORS
from .ai import resolve_missions
from .combat import resolve_combat, default_hp, sensor_range, weapon_range, valid_targets as unit_vtargets, UNIT_TYPE_LIB


class SimulationEngine:
    def __init__(self) -> None:
        self.units: Dict[str, Unit] = {}
        self.objectives: Dict[str, Objective] = {}
        self.sim_time: datetime = datetime.utcnow()
        self.tick_duration: float = 60.0
        self.speed_multiplier: float = 1.0
        self.running: bool = False
        self.tick_count: int = 0
        self._recent_events: List[dict] = []
        self.maritime_corridors: List[tuple] = list(BALTIC_NAVAL_CORRIDORS)

    def load_scenario(self, path: str) -> None:
        data = json.loads(Path(path).read_text())
        self.sim_time = datetime.fromisoformat(data["start_time"].replace("Z", "+00:00"))
        self.tick_duration = float(data.get("tick_duration_seconds", 60.0))
        self.objectives = {o["id"]: Objective(**o) for o in data.get("objectives", [])}
        raw_corridors = data.get("maritime_corridors", [])
        self.maritime_corridors = [tuple(c) for c in raw_corridors] if raw_corridors else list(BALTIC_NAVAL_CORRIDORS)

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

            # Stamp data_link from library (can't be set in scenario JSON)
            if unit.unit_type:
                unit.data_link = bool(UNIT_TYPE_LIB.get(unit.unit_type, {}).get("data_link", False))

            # Always start at full fuel, not rearming
            unit.fuel_pct = 100.0
            unit.rearming = False
            unit.rearm_ticks_left = 0

            units[unit.id] = unit
        self.units = units
        self._recent_events = []
        self.tick_count = 0
        self.running = False

    # Fuel burn defaults (% per tick) when not specified in unit_types.json
    _FUEL_MOVING = {"air": 1.5, "ground": 0.1, "naval": 0.2}
    _FUEL_IDLE   = {"air": 0.2, "ground": 0.02, "naval": 0.05}
    _REARM_TICKS = {"air": 8,   "ground": 5,    "naval": 12}

    def tick(self) -> None:
        events = resolve_combat(self.units)
        resource_events = self._burn_resources()
        for e in events + resource_events:
            if e["type"] in ("destroyed", "out_of_ammo", "low_fuel", "rtb_complete"):
                e["tick"] = self.tick_count
        self._recent_events = events + resource_events

        mission_events = resolve_missions(self.units, self.objectives, self.maritime_corridors)
        for e in mission_events:
            e["tick"] = self.tick_count
        self._recent_events.extend(mission_events)

        capture_events = self._resolve_objective_control()
        self._recent_events.extend(capture_events)

        for unit in self.units.values():
            if not unit.destroyed and unit.waypoints and unit.speed > 0:
                self._advance_unit(unit)

        self.sim_time += timedelta(seconds=self.tick_duration)
        self.tick_count += 1

    def _burn_resources(self) -> List[dict]:
        events: List[dict] = []
        for unit in self.units.values():
            if unit.destroyed:
                continue

            if unit.rearming:
                unit.rearm_ticks_left = max(0, unit.rearm_ticks_left - 1)
                if unit.rearm_ticks_left == 0:
                    unit.rearming = False
                    events.append(self._complete_rearm(unit))
                continue  # no fuel burn while being serviced

            lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
            was_above_20 = unit.fuel_pct > 20.0
            if unit.speed > 0:
                burn = lib.get("fuel_burn_per_tick",
                               self._FUEL_MOVING.get(unit.unit_class.value, 0.5))
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
        lib = UNIT_TYPE_LIB.get(unit.unit_type, {})
        preset = lib.get("loadout_presets", {}).get(unit.loadout, {})
        mags = preset.get("magazines", {})
        if mags:
            unit.magazines = dict(mags)

        # Restore previous mission so the unit resumes without player action.
        # Ground units never change their mission during rearm, so leave it as-is.
        if unit.unit_class != UnitClass.GROUND:
            unit.mission = unit.previous_mission  # None is fine — air enters holding orbit
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
            unit_states.append(d)
        return {
            "sim_time": self.sim_time.isoformat(),
            "tick": self.tick_count,
            "running": self.running,
            "units": unit_states,
            "objectives": [o.model_dump() for o in self.objectives.values()],
            "events": self._recent_events,
        }
