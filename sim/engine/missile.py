from __future__ import annotations
from typing import List, Tuple
from pydantic import BaseModel, Field


class Missile(BaseModel):
    id: str
    firer_id: str
    firer_name: str
    target_id: str
    target_name: str = ""
    side: str           # "blue" | "red"
    ammo_type: str      # "aa" | "ag" | "as"
    lat: float
    lon: float
    origin_lat: float   # launch position (for trail rendering)
    origin_lon: float
    target_lat: float
    target_lon: float
    heading: float
    speed_kmh: float
    altitude_m: float
    rcs: float
    damage: float
    ticks_remaining: int
    total_ticks: int
    intercepted: bool = False
    waypoints: List[Tuple[float, float]] = Field(default_factory=list)
