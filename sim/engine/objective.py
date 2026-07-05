from pydantic import BaseModel
from typing import Optional
from enum import Enum


class ObjectiveType(str, Enum):
    AIRFIELD = "airfield"
    PORT = "port"
    CITY = "city"
    BRIDGE = "bridge"
    MARITIME = "maritime"
    BASE = "base"


class Objective(BaseModel):
    id: str
    name: str
    lat: float
    lon: float
    type: ObjectiveType
    controlling_side: Optional[str] = None  # "blue" | "red" | None (neutral/contested)
    country: Optional[str] = None           # e.g. "estonia", "russia" — drives coalition assignment
