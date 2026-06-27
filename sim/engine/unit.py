from pydantic import BaseModel, Field
from typing import Dict, List, Tuple, Optional
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
    AREA_PATROL = "area_patrol"
    INTERCEPT = "intercept"
    RTB = "rtb"


class MissionStatus(str, Enum):
    EN_ROUTE = "en_route"
    ON_STATION = "on_station"


class Mission(BaseModel):
    type: MissionType
    objective_id: Optional[str] = None
    patrol_lat: Optional[float] = None   # area_patrol center (no objective needed)
    patrol_lon: Optional[float] = None
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
    airborne: bool = True   # air units only: False = on ground, won't auto-orbit
    mission: Optional[Mission] = None
    waypoints: List[Tuple[float, float]] = Field(default_factory=list)
    # Loadout & logistics
    loadout: str = ""
    magazines: Dict[str, int] = Field(default_factory=dict)  # {"aa": 8, "ag": 4, "as": 0}
    fuel_pct: float = 100.0
    home_base_lat: Optional[float] = None
    home_base_lon: Optional[float] = None
    rearming: bool = False
    rearm_ticks_left: int = 0
    weapon_km_override: Optional[float] = None  # set by loadout preset (e.g. ATACMS)
