from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .unit import Unit, Mission, MissionType, MissionStatus, UnitClass
from .objective import Objective
from .geo import haversine

REEVAL_INTERVAL = 5  # ticks between commander re-evaluations

# Default unit allocations when the goal spec omits a count.
_DEFAULTS: Dict[str, Dict[str, int]] = {
    "hold":      {"ground_count": 1, "air_count": 2, "naval_count": 0},
    "capture":   {"ground_count": 2, "air_count": 1, "naval_count": 0},
    "intercept": {"ground_count": 0, "air_count": 2, "naval_count": 0},
    "patrol":    {"ground_count": 0, "air_count": 2, "naval_count": 1},
    "strike":    {"ground_count": 0, "air_count": 2, "naval_count": 1},
}


@dataclass
class SideGoal:
    type: str                       # "hold" | "capture" | "intercept" | "patrol" | "strike"
    priority: int = 1
    objective_id: Optional[str] = None
    area_lat: Optional[float] = None
    area_lon: Optional[float] = None
    ground_count: Optional[int] = None  # None → use _DEFAULTS for this goal type
    air_count: Optional[int] = None
    naval_count: Optional[int] = None

    def effective_ground(self) -> int:
        return self.ground_count if self.ground_count is not None else _DEFAULTS.get(self.type, {}).get("ground_count", 0)

    def effective_air(self) -> int:
        return self.air_count if self.air_count is not None else _DEFAULTS.get(self.type, {}).get("air_count", 0)

    def effective_naval(self) -> int:
        return self.naval_count if self.naval_count is not None else _DEFAULTS.get(self.type, {}).get("naval_count", 0)

    def to_dict(self) -> dict:
        """Serialise with effective (default-resolved) counts for the frontend."""
        return {
            "type": self.type,
            "priority": self.priority,
            "objective_id": self.objective_id,
            "area_lat": self.area_lat,
            "area_lon": self.area_lon,
            "ground_count": self.effective_ground(),
            "air_count": self.effective_air(),
            "naval_count": self.effective_naval(),
        }


class Commander:
    def __init__(self, side: str) -> None:
        self.side = side
        self.goals: List[SideGoal] = []

    def set_goals(self, raw: List[dict]) -> None:
        self.goals = [SideGoal(**g) for g in raw]

    # ── Goal–mission matching ─────────────────────────────────────────────────

    def _serves_goal(self, unit: Unit, goal: SideGoal) -> bool:
        """True if the unit's current mission is already contributing to this goal."""
        m = unit.mission
        if m is None or m.type == MissionType.RTB:
            return False
        t = goal.type
        uc = unit.unit_class

        if t == "hold":
            if uc == UnitClass.GROUND:
                return m.type == MissionType.DEFEND and m.objective_id == goal.objective_id
            if uc == UnitClass.AIR:
                return m.type == MissionType.PATROL and m.objective_id == goal.objective_id
        elif t == "capture":
            if uc == UnitClass.GROUND:
                return m.type == MissionType.SECURE and m.objective_id == goal.objective_id
            if uc == UnitClass.AIR:
                return m.type == MissionType.PATROL and m.objective_id == goal.objective_id
        elif t == "intercept":
            if uc == UnitClass.AIR:
                return m.type == MissionType.INTERCEPT
        elif t == "patrol":
            return m.type == MissionType.AREA_PATROL
        elif t == "strike":
            if uc == UnitClass.AIR:
                return m.type == MissionType.INTERCEPT
            if uc == UnitClass.NAVAL:
                return m.type in (MissionType.PATROL, MissionType.INTERCEPT)
        return False

    def _serves_any_goal(self, unit: Unit) -> bool:
        return any(self._serves_goal(unit, g) for g in self.goals)

    def _is_reassignable(self, unit: Unit) -> bool:
        """Commander may redirect this unit (not in critical single-use state)."""
        if unit.destroyed or unit.rearming or unit.side.value != self.side:
            return False
        if unit.mission is not None and unit.mission.type == MissionType.RTB:
            return False
        return True

    # ── Mission factory ───────────────────────────────────────────────────────

    def _make_mission(
        self, unit: Unit, goal: SideGoal, objectives: Dict[str, Objective]
    ) -> Optional[Mission]:
        t = goal.type
        uc = unit.unit_class

        if t in ("hold", "capture"):
            if uc == UnitClass.GROUND:
                mtype = MissionType.DEFEND if t == "hold" else MissionType.SECURE
                return Mission(type=mtype, objective_id=goal.objective_id, status=MissionStatus.EN_ROUTE)
            if uc == UnitClass.AIR:
                # Air provides CAP/cover over the objective for both hold and capture
                if goal.objective_id:
                    return Mission(type=MissionType.PATROL, objective_id=goal.objective_id, status=MissionStatus.EN_ROUTE)
                if goal.area_lat is not None and goal.area_lon is not None:
                    return Mission(type=MissionType.AREA_PATROL, patrol_lat=goal.area_lat, patrol_lon=goal.area_lon, status=MissionStatus.EN_ROUTE)

        elif t == "intercept":
            if uc == UnitClass.AIR:
                return Mission(type=MissionType.INTERCEPT, status=MissionStatus.EN_ROUTE)

        elif t == "patrol":
            plat = goal.area_lat
            plon = goal.area_lon
            if plat is None and goal.objective_id:
                obj = objectives.get(goal.objective_id)
                if obj:
                    plat, plon = obj.lat, obj.lon
            if plat is not None and plon is not None:
                return Mission(type=MissionType.AREA_PATROL, patrol_lat=plat, patrol_lon=plon, status=MissionStatus.EN_ROUTE)

        elif t == "strike":
            if uc == UnitClass.AIR:
                return Mission(type=MissionType.INTERCEPT, status=MissionStatus.EN_ROUTE)
            if uc == UnitClass.NAVAL:
                return Mission(type=MissionType.PATROL, objective_id=goal.objective_id, status=MissionStatus.EN_ROUTE)

        return None

    # ── Main evaluation loop ──────────────────────────────────────────────────

    def evaluate(
        self,
        units: Dict[str, Unit],
        objectives: Dict[str, Objective],
        tick: int,
    ) -> List[dict]:
        if tick % REEVAL_INTERVAL != 0 or not self.goals:
            return []

        events: List[dict] = []
        friendly = [u for u in units.values() if u.side.value == self.side and not u.destroyed]

        for goal in sorted(self.goals, key=lambda g: g.priority):
            serving = [u for u in friendly if self._serves_goal(u, goal)]
            ground_gap = goal.effective_ground() - sum(1 for u in serving if u.unit_class == UnitClass.GROUND)
            air_gap    = goal.effective_air()    - sum(1 for u in serving if u.unit_class == UnitClass.AIR)
            naval_gap  = goal.effective_naval()  - sum(1 for u in serving if u.unit_class == UnitClass.NAVAL)

            if ground_gap <= 0 and air_gap <= 0 and naval_gap <= 0:
                continue

            # Distance reference for sorting candidates
            ref_lat: Optional[float] = goal.area_lat
            ref_lon: Optional[float] = goal.area_lon
            if ref_lat is None and goal.objective_id:
                obj = objectives.get(goal.objective_id)
                if obj:
                    ref_lat, ref_lon = obj.lat, obj.lon

            def dist_to_ref(u: Unit) -> float:
                if ref_lat is None:
                    return 0.0
                return haversine(u.lat, u.lon, ref_lat, ref_lon)

            # Units not already serving any goal — first pick goes to higher-priority goals
            available = [u for u in friendly if self._is_reassignable(u) and not self._serves_any_goal(u)]

            obj_name: Optional[str] = None
            if goal.objective_id:
                obj_r = objectives.get(goal.objective_id)
                if obj_r:
                    obj_name = obj_r.name

            def assign_class(uc: UnitClass, gap: int) -> None:
                nonlocal available
                if gap <= 0:
                    return
                candidates = sorted([u for u in available if u.unit_class == uc], key=dist_to_ref)
                if uc == UnitClass.AIR:
                    candidates = [u for u in candidates if u.airborne]
                for unit in candidates[:gap]:
                    new_m = self._make_mission(unit, goal, objectives)
                    if new_m is None:
                        continue
                    unit.mission = new_m
                    unit.waypoints = []
                    unit.speed = 0.0
                    available = [u for u in available if u.id != unit.id]
                    events.append({
                        "type": "commander_assign",
                        "unit_id": unit.id,
                        "unit_name": unit.name,
                        "mission": new_m.type.value,
                        "goal_type": goal.type,
                        "objective": obj_name or goal.type,
                        "side": self.side,
                        "tick": None,
                    })

            assign_class(UnitClass.GROUND, ground_gap)
            assign_class(UnitClass.AIR, air_gap)
            assign_class(UnitClass.NAVAL, naval_gap)

        return events
