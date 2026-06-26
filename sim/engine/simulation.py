from datetime import datetime, timedelta
from typing import Dict, Any, List
import json
from pathlib import Path

from .unit import Unit, Mission, MissionType, MissionStatus
from .objective import Objective
from .geo import haversine, bearing, destination
from .ai import resolve_missions
from .combat import resolve_combat, default_hp, sensor_range, weapon_range, valid_targets as unit_vtargets


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
                from .combat import UNIT_TYPE_LIB
                lib = UNIT_TYPE_LIB.get(unit.unit_type)
                if lib and "max_speed_kmh" in lib:
                    unit.max_speed = lib["max_speed_kmh"]
            units[unit.id] = unit
        self.units = units
        self._recent_events = []
        self.tick_count = 0
        self.running = False

    def tick(self) -> None:
        events = resolve_combat(self.units)
        for e in events:
            if e["type"] == "destroyed":
                e["tick"] = self.tick_count
        self._recent_events = events

        resolve_missions(self.units, self.objectives)

        for unit in self.units.values():
            if not unit.destroyed and unit.waypoints and unit.speed > 0:
                self._advance_unit(unit)

        self.sim_time += timedelta(seconds=self.tick_duration)
        self.tick_count += 1

    def _advance_unit(self, unit: Unit) -> None:
        if not unit.waypoints:
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

    def assign_mission(self, unit_id: str, mission_type: str, objective_id: str | None) -> bool:
        unit = self.units.get(unit_id)
        if unit is None or unit.destroyed:
            return False
        unit.waypoints = []
        unit.speed = 0.0
        unit.mission = Mission(
            type=MissionType(mission_type),
            objective_id=objective_id,
            status=MissionStatus.EN_ROUTE,
        )
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
