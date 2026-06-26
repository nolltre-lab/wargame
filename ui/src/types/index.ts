export type Side = 'blue' | 'red';
export type UnitClass = 'air' | 'ground' | 'naval';
export type MissionType = 'secure' | 'defend' | 'patrol' | 'area_patrol' | 'intercept';
export type MissionStatus = 'en_route' | 'on_station';
export type ObjectiveType = 'airfield' | 'port' | 'city' | 'bridge' | 'maritime' | 'base';

export interface Mission {
  type: MissionType;
  objective_id: string | null;
  patrol_lat: number | null;
  patrol_lon: number | null;
  status: MissionStatus;
}

export interface Unit {
  id: string;
  name: string;
  side: Side;
  sidc: string;
  lat: number;
  lon: number;
  heading: number;
  speed: number;
  altitude: number;
  unit_class: UnitClass;
  unit_type: string;
  max_speed: number;
  hp: number;
  max_hp: number;
  destroyed: boolean;
  airborne: boolean;
  sensor_km: number;
  weapon_km: number;
  valid_targets: UnitClass[];
  mission: Mission | null;
  waypoints: [number, number][];
}

export interface Objective {
  id: string;
  name: string;
  lat: number;
  lon: number;
  type: ObjectiveType;
  controlling_side: Side | null;
}

export interface CombatEvent {
  type: 'engagement' | 'destroyed' | 'captured';
  attacker_id?: string;
  attacker_name?: string;
  target_id?: string;
  target_name?: string;
  damage?: number;
  target_hp?: number;
  target_max_hp?: number;
  unit_id?: string;
  unit_name?: string;
  objective_id?: string;
  objective_name?: string;
  side?: Side;
  tick?: number;
}

export interface SimState {
  sim_time: string;
  tick: number;
  running: boolean;
  units: Unit[];
  objectives: Objective[];
  events: CombatEvent[];
}

export type WsOutMessage =
  | { type: 'assign_mission'; unit_id: string; mission_type: MissionType; objective_id?: string; patrol_lat?: number; patrol_lon?: number }
  | { type: 'clear_mission'; unit_id: string };

export interface RingToggles {
  sensor: boolean;
  airWeapon: boolean;
  surfaceWeapon: boolean;
}

export interface UnitTypeInfo {
  display_name: string;
  unit_class: UnitClass;
  sensor_km: number;
  weapon_km: number;
  attack_per_tick: number;
  default_hp: number;
  max_speed_kmh: number;
  valid_targets: UnitClass[];
  notes?: string;
}

export interface TheaterInfo {
  id: string;
  name: string;
  description: string;
}

export interface BuilderUnit {
  id: string;
  side: Side;
  unit_type: string;
  unit_class: UnitClass;
  sidc: string;
  lat: number;
  lon: number;
  name: string;
  airborne: boolean;
}
