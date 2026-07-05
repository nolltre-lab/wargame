# Wargame Simulation

Autonomous military simulation where sides pursue high-level goals without player micro-management. Player defines scenarios and goals; AI handles all tactical execution. Focus is on emergent outcomes, not RTS-style unit control.

## Architecture

```
wargame/
├── sim/                   # Python FastAPI backend
│   ├── main.py            # WebSocket server + REST endpoints
│   ├── requirements.txt
│   └── engine/
│       ├── simulation.py  # Tick engine (60s ticks, adjustable speed)
│       ├── unit.py        # Unit model (hp, mission, waypoints, destroyed)
│       ├── combat.py      # Detection + engagement resolution
│       ├── ai.py          # Mission execution AI (waypoint/patrol logic)
│       ├── objective.py   # Objective model
│       └── geo.py         # Haversine, bearing, destination
├── scenarios/
│   └── baltic_flashpoint.json   # NATO vs Russia / Estonia. 10 units, 9 objectives
└── ui/                    # React + Vite + TypeScript frontend
    └── src/
        ├── App.tsx
        ├── components/
        │   ├── MapView.tsx     # MapLibre GL + milsymbol NATO markers
        │   ├── UnitPanel.tsx   # Selected unit details + mission assignment
        │   └── EventLog.tsx    # Combat event feed
        ├── hooks/useSimSocket.ts
        ├── store/simStore.ts   # Zustand
        └── types/index.ts
```

## Running

```bash
# Backend (from sim/)
pip install -r requirements.txt
uvicorn main:app --reload

# Frontend (from ui/)
npm install
npm run dev
```

Backend: http://localhost:8000  
Frontend: http://localhost:5173

## Design Principles

- **Battalion minimum**: Ground forces are never controlled below battalion level
- **Goal-oriented**: Units receive goals ("protect X", "capture Y"), not move orders
- **No micro-management**: Player sets scenario goals; AI decomposes and executes
- **Learning**: Units and teams accumulate experience across runs of the same scenario

## Development Roadmap

Status markers: ✅ done · 🔄 in progress · ⬜ not started

---

### Phase 1 — Combat + Detection ✅

- [x] Unit type library (26 types, open-source data: sensor_km, weapon_km, attack_per_tick, max_speed_kmh)
- [x] HP, damage, destruction — units persist as `destroyed: true` for UI and future learning
- [x] Two-phase combat resolution per tick (collect attacks → apply simultaneously, no iteration bias)
- [x] Goal-oriented unit AI: secure, defend, patrol, intercept missions
- [x] WebSocket tick broadcast; Zustand store on frontend
- [x] MapLibre GL map with milsymbol NATO icons as GeoJSON symbol layers (pixel-exact positioning)
- [x] Toggleable sensor rings (air: forward cone; ground/naval: circle) and weapon rings
- [x] Missile shot animation (colour-coded by engagement type) → replaced by entity-based missile rendering
- [x] Event log feed (engagements + kills)
- [x] `./start.sh` one-shot launcher with stale-process cleanup
- [x] Baltic flashpoint scenario (10 units, 9 objectives, NATO vs Russia/Estonia)
- [x] Git repo initialised
- [x] Terrain-aware routing — global land/water model from real coastline data (Natural Earth 10m); ground units route around water, naval units route around land, via grid A* fallback when the direct line crosses the wrong domain; works on any theater, not just the Baltic
- [x] Objective capture — controlling side flips when ground unit holds objective uncontested; capture event in log
- [x] Scenario builder — place units on map per coalition, save/load/play
- [x] Theater base maps — Baltic Sea template (29 objectives); `/theaters` endpoints; map flies to theater on select

---

### Phase 2 — Autonomous Commander AI 🔄

- [x] **Air unit initial state** — `airborne` field on Unit; ground units wait for tasking; airborne units auto-fly a holding orbit when no mission active; builder snaps ground units to nearest airfield/base; builder toggle per unit
- [x] **Area patrol (non-targeted)** — `area_patrol` mission type with embedded `patrol_lat/lon`; no objective link; for CAP, naval patrol lanes, ASW sweeps; UnitPanel supports it
- [x] **Magazine depth + loadouts** — per-unit ammo pools (aa/ag/as rounds); loadout presets per type in unit_types.json (e.g. "Air Superiority", "Strike", "Anti-Ship"); builder loadout picker; ammo consumed per engagement tick; `out_of_ammo` events in log; weapon_km_override per preset (ATACMS 165 km, anti-ship missiles)
- [x] **Fuel model** — `fuel_pct` on all units; per-tick burn rates (moving vs idle) per unit type; `low_fuel` events in log; fuel bar in UnitPanel
- [x] **RTB / Rearm mission** — `rtb` mission type; air and naval route to nearest valid friendly base (dynamic: captured bases drop off the list automatically); ground and emplaced SAMs rearm in place; `rearming` state suppresses combat; `rtb_complete` event; RTB button in UnitPanel; air units set `airborne=False` on landing, `True` on new mission takeoff; `valid_base_types` in unit_types.json controls which objective types a unit class can use (air → airfield, naval → port/base)
- [x] Side-level goals in scenario JSON (`"goals": {"blue": [{"type": "hold", "objective_id": "amari_ab", "priority": 1}], "red": [...]}}`)
- [x] Commander AI per side: re-evaluates every 5 ticks (`REEVAL_INTERVAL`) and assigns/reassigns unit missions to fill goal gaps, never disrupting units already serving a goal or mid-RTB
- [x] Player interface: GoalsPanel UI — assign/reorder/remove goals per side via `/goals/{side}` REST endpoint, not missions to individual units
- [x] **Fuel optimization / transit speed** — units currently fly at `max_speed` at all times, burning full fuel on every leg; this dramatically reduces effective range and causes units to divert to neutral airbases. Fix: add a `cruise_speed_kmh` per unit type (~70% of max) and a `cruise_fuel_factor` (~0.5×); units fly at cruise speed during transit (en route to objective, RTB, ferry) and switch to max speed only for combat, evasion, or scramble. Bingo/roundtrip calculations must use routed distance (not straight-line) and the actual per-leg speed/burn rate. Pre-mission feasibility check should reject assignments when `fuel_roundtrip_pct > unit.fuel_pct`. Eliminates the Kaliningrad→Gulf-of-Finland Flanker divert problem and roughly doubles effective radius for transit-heavy sorties. **Altitude↔fuel tradeoff** (bundle here): high-altitude transit burns less (thinner air), low-level ingress burns more. Once per-leg fuel burn is implemented, altitude should be a factor — making the detection benefit of low-level flight a real cost in range. The `altitude_ingress_m` field and the phase-switching logic are already in place; only the fuel side is missing.
- [x] **Missile entities** — air/naval weapons create `Missile` objects that fly to target over multiple ticks (A-S: ~14 ticks at 860 km/h, A-A: 1–2 ticks); entity has altitude_m, RCS, speed for detection physics; damage applied on arrival; missiles broadcast in state and rendered as colored arrowhead icons on map with trail line; firing side always tracks own missiles; enemy side must detect via radar (altitude + RCS via existing `radar_horizon_km` formula — Kalibr at 10m vs frigate sensor at 35m: ~12km horizon-limited detection); ground direct fire still immediate
- [x] **Missile intercept / point defense** — SAMs and air-defense ships attempt to engage inbound missiles each tick: detection via `missile_detection_range()` (horizon + RCS), weapon range check, then PK roll modified by missile RCS and speed; `intercept_pk` per unit type (Patriot: 0.85, S-300V4: 0.80, NASAMS: 0.75, Arleigh Burke: 0.80, Buk-M3: 0.65, Pantsir-S1: 0.60, fighters: 0.35–0.45); JSM on F-35 uses ultra-low 0.01 m² RCS and 5m altitude; P-1000 Vulkan on Slava uses Mach 2.5 and 0.3 m² RCS; intercept events in log
- [x] **Missile FOW** — missiles only visible to opposing side if one of their radar units can detect it; firing side always knows own missile positions; `blue_detected_missiles`/`red_detected_missiles` in state; UI filters missile icons by perspective
- [x] **Missile non-direct routing** — AS cruise missiles route around land via terrain A* (`find_route("water")`); `waypoints` field on Missile entity; `_advance_missiles` follows waypoints; total_ticks recalculated from actual route length; AA and AG missiles still fly direct
- [x] **All missile types visible** — minimum 2 ticks enforced so fast AA missiles appear in at least one broadcast; SAM ground units with `intercept_capable=true` now create Missile entities when engaging air targets (so SAM launches are visible on map)
- [x] **Clickable missile stats panel** — click any missile icon to open MissilePanel showing ammo type, firer, target, altitude, speed, heading, RCS, ticks-to-impact; dashed line drawn from missile to its current target position; `selectedMissileId` in store; selecting a unit clears missile selection and vice versa
- [x] **Altitude-dependent detection** — already fully implemented via `radar_horizon_km()` in combat.py: detection range = `min(sensor_km, horizon) × rcs_scale`; horizon formula `4.12 × (√h_scanner + √h_target)` naturally limits surface radars to ~12 km for sea-skimming AS missiles (10 m altitude) and ~44 km for high-altitude F-35A (rcs=0.005); air units dynamically switch `altitude_m` between cruise and ingress values via `_set_altitude_phase()` in ai.py so low-level ingress actually reduces detectability in combat; ship radar heights in unit_types.json (30–35 m mast height) produce correct horizon limits
- [ ] Reactive rules: fighters scramble when enemy air detected within sensor range
- [ ] Basic threat priority: commander weighs which objectives are under pressure
- [ ] Dispersed basing — aircraft capable of operating from roads/unprepared strips (Su-34, A-10) can be placed freely; requires `dispersed_basing: true` in unit_types.json
- [ ] Multi-unit selection — select several units at once and assign them a shared goal; units coordinate to accomplish it as a group
- [ ] **Poseidon/Il-38 ASW attack** — P-8A and Il-38N already have `is_surveillance: true` and anti-ship capability; extend to engage submarine targets (Ula-class etc.) using torpedo loadout; submarines lack air-defence so MPA can safely engage if no escorting fighters present. Requires `submarine` as a valid target class in MPA valid_targets.
- [ ] **Baltic-theater unit types** — current naval roster is blue-water (Arleigh Burke DDG, HNLMS frigate, Slava CG); the Baltic Sea is a shallow, enclosed littoral where smaller platforms dominate. Priority additions: Visby-class corvette (Sweden, RCS ~150m² vs frigate's thousands — stealthy surface combatant), Karakurt-class corvette (Russia Project 22800, Kalibr-armed), Type 212A/Gotland-class AIP submarine (Germany/Sweden — ultra-quiet; needs submerged depth modeled as negative altitude_m + sonar sensor type), Hamina-class missile boat (Finland, fast attack), Bastion-P coastal missile battery (Russia, land-based Oniks anti-ship with 300km range), RBS-15 coastal battery (Sweden/Finland). Submarines require a new sensor modality (sonar vs radar) and minimal surface RCS; coastal batteries are ground-class units with `valid_targets: ["naval"]` only.
- [ ] **Carriers (CVN/CVBG)** — large naval unit with flight deck; can spawn/recover aircraft; acts as mobile airbase; very high HP and RCS; requires dedicated CVBG escort screen (Burke, frigate). High priority escort scenario target.
- [ ] **Escort AI improvements** — threat-axis positioning (escort interposes on bearing from nearest enemy); proactive threat intercept (escort breaks away to engage inbound missile/aircraft before it reaches the charge); multi-escort coordination (two fighters split CAP coverage around one AWACS).
- [ ] **EMCON AI** — units with `data_link: true` and low RCS (F-35) go passive (emcon=False) when friendly data-link picture covers their field of view; turn radar back on when blind spots appear; emplaced SAMs emit only when threat in engagement range.
- [ ] **Base inventory / supply chain** — each objective carries per-type stores: fuel tonnes, AA/AG/AS munition counts; air/naval units consume from that pool when rearming; a unit can refuel at a base that has fuel but is out of a specific weapon type and will rearm incompletely; supply convoys or airlifts replenish bases over time; captures transfer (partial) remaining stores to the capturing side; adds strategic layer where destroying depots degrades enemy sortie rate. The loadout selection window already exists in the sim — the stores constraint just limits which loadouts are available at each base (e.g. Finnish base may have AMRAAMs but not Meteors; Estonian coastal base may have no anti-ship stores at all).
- [ ] **Territorial boundaries + airspace / EEZ respecting** — each `country` in the theater defines a territory polygon (airspace + 12nm territorial sea + 200nm EEZ). In the builder and sim overlay, these are drawn as a toggleable layer alongside sensor/weapon rings. AI routing constraint: units do not enter neutral-country territory unless they have an active objective inside that territory; route planning steers around the border the same way it steers around coastlines. Naval units respect EEZ, air units respect airspace.
- [ ] **Neutrality-to-belligerent escalation** — each neutral country tracks an `outrage` score (0–100). Score increments: overflying their territory without objective (+5/incident), conducting combat in their airspace (+25), attacking their assets (+50), bombing their cities (+75). When `outrage >= 100` the country joins the coalition opposite the offending side; if both sides have offended, the country joins neither and activates its own SAMs/coast-defence. Requires: country definitions with territory polygons stored in theater JSON; `country_status` dict in SimulationEngine updated each tick; `coalition_change` event in log. Edge cases: attacked by two coalitions → remains non-aligned but hostile to both; scenario can mark countries as `forced_neutral: true` (Sweden/Finland if not at war) to prevent automatic joining.

---

### Phase 3 — Unit-level Learning ⬜

- [ ] After-action record saved per scenario run (unit survival, damage dealt/taken, mission outcomes)
- [ ] Experience weights stored per unit ID / scenario (JSON sidecar alongside scenario file)
- [ ] Weights bias tactical choices: standoff range, aggression, target priority
- [ ] Example: F-16 that died approaching a defended target learns to delay until SEAD complete
- [ ] Requires: `numpy` + `scipy` in sim/requirements.txt

---

### Phase 4 — Team-level Learning ⬜

- [ ] Side-level experience aggregated from all unit after-action records
- [ ] Commander AI reads team weights to adjust objective priority and unit assignment
- [ ] PyTorch MPS backend for weight updates (Apple Silicon GPU, never CUDA)
- [ ] Visible improvement: re-running the same scenario produces qualitatively different tactics
- [ ] Requires: `torch` (mps device) in sim/requirements.txt

## Unit Types and Combat Capabilities

| unit_type  | sensor_km | weapon_km | attack/tick | default_hp |
|------------|-----------|-----------|-------------|------------|
| fighter    | 150       | 80        | 30          | 60         |
| armor      | 8         | 3         | 20          | 120        |
| artillery  | 15        | 80        | 40          | 80         |
| infantry   | 3         | 1         | 10          | 100        |
| frigate    | 50        | 30        | 25          | 150        |
| cruiser    | 80        | 50        | 35          | 200        |

Valid target classes:
- air → air, ground, naval
- ground → ground, naval
- naval → naval, ground, air

## Scenario Format

```json
{
  "name": "...",
  "start_time": "2024-06-15T04:00:00Z",
  "tick_duration_seconds": 60,
  "objectives": [ { "id": "...", "lat": ..., "lon": ..., "type": "airfield|port|city|base|maritime", "controlling_side": "blue|red|null" } ],
  "units": [ { "id": "...", "side": "blue|red", "unit_class": "air|ground|naval", "unit_type": "fighter|armor|artillery|infantry|frigate|cruiser", "sidc": "...", "lat": ..., "lon": ... } ]
}
```

## Target Hardware

MacBook Pro M1 Max, 64 GB unified RAM. Optimization notes:
- Phase 1-2: pure Python + asyncio is fine for hundreds of units
- Phase 3-4 (learning): use `numpy`/`scipy` for batch computation, avoid pure-Python loops over large experience sets
- ML backend: PyTorch with `mps` device (Apple Silicon GPU) — never CUDA. Import pattern: `device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")`
- Prefer numpy Accelerate (ships with macOS) for linear algebra; add `numpy` and `scipy` to requirements when learning begins
- Sim can run 500+ units at 60s ticks in real-time on M1 Max before optimisation is needed

## Key Constraints

- Tick duration: 60 sim-seconds. Speed multiplier applied in real-time interval only.
- Units broadcast as full state each tick (no delta compression yet).
- Destroyed units remain in state with `destroyed: true` for UI and learning record.
- WebSocket at `ws://localhost:8000/ws`; REST at `http://localhost:8000`.
