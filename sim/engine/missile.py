from __future__ import annotations
from pydantic import BaseModel


class Missile(BaseModel):
    id: str
    firer_id: str
    firer_name: str
    target_id: str
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
