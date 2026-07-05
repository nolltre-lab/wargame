export type Side = 'blue' | 'red';
export type UnitClass = 'air' | 'ground' | 'naval';
export type MissionType = 'secure' | 'defend' | 'patrol' | 'area_patrol' | 'intercept' | 'rtb' | 'escort';
export type MissionStatus = 'en_route' | 'on_station';
export type ObjectiveType = 'airfield' | 'port' | 'city' | 'bridge' | 'maritime' | 'base';

export interface Mission {
  type: MissionType;
  objective_id: string | null;
  patrol_lat: number | null;
  patrol_lon: number | null;
  target_unit_id: string | null;
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
  // Logistics
  loadout: string;
  magazines: Record<string, number>;  // {"aa": 8, "ag": 4, "as": 0}
  fuel_pct: number;
  home_base_lat: number | null;
  home_base_lon: number | null;
  rearming: boolean;
  rearm_ticks_left: number;
  awaiting_loadout: boolean;
  loadout_selection_ticks_left: number;
  pending_loadout: string | null;
  loadout_presets: string[];
  data_link: boolean;
  is_surveillance: boolean;
  sensor_arc_deg: number | null;
  sensor_bi_cone: boolean;
  altitude_m: number;
  rcs: number;
  emcon: boolean;
}

export interface Objective {
  id: string;
  name: string;
  lat: number;
  lon: number;
  type: ObjectiveType;
  controlling_side: Side | null;
  country: string | null;
}

export type Coalitions = { blue: string[]; red: string[] };

export interface SimMissile {
  id: string;
  firer_id: string;
  firer_name: string;
  target_id: string;
  side: Side;
  ammo_type: string;  // "aa" | "ag" | "as"
  lat: number;
  lon: number;
  origin_lat: number;
  origin_lon: number;
  target_lat: number;
  target_lon: number;
  heading: number;
  speed_kmh: number;
  altitude_m: number;
  rcs: number;
  ticks_remaining: number;
  total_ticks: number;
}

export interface CombatEvent {
  type: 'engagement' | 'destroyed' | 'captured' | 'out_of_ammo' | 'low_fuel' | 'rtb_complete' | 'bingo_fuel' | 'winchester' | 'commander_assign' | 'missile_intercept';
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
  objective?: string;
  ammo_type?: string;
  mission?: string;
  goal_type?: string;
  side?: Side;
  tick?: number;
  // missile_intercept fields
  interceptor_id?: string;
  interceptor_name?: string;
  firer_name?: string;
  missile_type?: string;
}

export interface SideGoal {
  type: 'hold' | 'capture' | 'intercept' | 'patrol' | 'strike';
  priority: number;
  objective_id: string | null;
  area_lat: number | null;
  area_lon: number | null;
  ground_count: number;
  air_count: number;
  naval_count: number;
}

export interface SimState {
  sim_time: string;
  tick: number;
  running: boolean;
  units: Unit[];
  objectives: Objective[];
  events: CombatEvent[];
  blue_detected: string[];
  red_detected: string[];
  goals: { blue: SideGoal[]; red: SideGoal[] };
  missiles: SimMissile[];
  blue_detected_missiles: string[];
  red_detected_missiles: string[];
}

export type WsOutMessage =
  | { type: 'assign_mission'; unit_id: string; mission_type: MissionType; objective_id?: string; patrol_lat?: number; patrol_lon?: number; target_unit_id?: string }
  | { type: 'clear_mission'; unit_id: string };

export interface LoadoutPreset {
  label: string;
  magazines: { aa?: number; ag?: number; as?: number };
  weapon_km?: number;
}

export interface RingToggles {
  sensor: boolean;
  airWeapon: boolean;
  surfaceWeapon: boolean;
  territory: boolean;
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
  rearm_ticks?: number;
  data_link?: boolean;
  altitude_m?: number;
  rcs?: number;
  is_surveillance?: boolean;
  sensor_arc_deg?: number;
  sensor_bi_cone?: boolean;
  loadout_presets?: Record<string, LoadoutPreset>;
}

export interface TheaterInfo {
  id: string;
  name: string;
  description: string;
  default_coalitions?: Coalitions;
}

export interface BuilderMission {
  type: MissionType;
  objective_id: string | null;
  patrol_lat: number | null;
  patrol_lon: number | null;
  target_unit_id: string | null;
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
  loadout: string;
  home_base_lat: number | null;
  home_base_lon: number | null;
  initial_mission: BuilderMission | null;
}
