from pydantic import BaseModel, Field
from typing import List, Tuple, Optional
from enum import Enum


class Side(str, Enum):
    BLUE = "blue"
    RED = "red"


class UnitClass(str, Enum):
    AIR = "air"
    GROUND = "ground"
    NAVAL = "naval"


class MissionType(str, Enum):
    SECURE = "secure"
    DEFEND = "defend"
    PATROL = "patrol"
    INTERCEPT = "intercept"


class MissionStatus(str, Enum):
    EN_ROUTE = "en_route"
    ON_STATION = "on_station"


class Mission(BaseModel):
    type: MissionType
    objective_id: Optional[str] = None
    status: MissionStatus = MissionStatus.EN_ROUTE


class Unit(BaseModel):
    id: str
    name: str
    side: Side
    sidc: str
    lat: float
    lon: float
    heading: float = 0.0
    speed: float = 0.0
    altitude: float = 0.0
    unit_class: UnitClass
    unit_type: str = ""           # e.g. "f16c", "challenger2" — looked up in unit_types.json
    max_speed: float = 30.0
    hp: float = 100.0
    max_hp: float = 100.0
    destroyed: bool = False
    mission: Optional[Mission] = None
    waypoints: List[Tuple[float, float]] = Field(default_factory=list)
