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
- [x] Missile shot animation (colour-coded by engagement type)
- [x] Event log feed (engagements + kills)
- [x] `./start.sh` one-shot launcher with stale-process cleanup
- [x] Baltic flashpoint scenario (10 units, 9 objectives, NATO vs Russia/Estonia)
- [x] Git repo initialised
- [x] Naval terrain masking — Gulf of Finland corridor waypoint; route_clips_land check
- [x] Objective capture — controlling side flips when ground unit holds objective uncontested; capture event in log
- [x] Scenario builder — place units on map per coalition, save/load/play
- [x] Theater base maps — Baltic Sea template (29 objectives); `/theaters` endpoints; map flies to theater on select

---

### Phase 2 — Autonomous Commander AI 🔄

- [x] **Air unit initial state** — `airborne` field on Unit; ground units wait for tasking; airborne units auto-fly a holding orbit when no mission active; builder snaps ground units to nearest airfield/base; builder toggle per unit
- [x] **Area patrol (non-targeted)** — `area_patrol` mission type with embedded `patrol_lat/lon`; no objective link; for CAP, naval patrol lanes, ASW sweeps; UnitPanel supports it
- [x] **Magazine depth + loadouts** — per-unit ammo pools (aa/ag/as rounds); loadout presets per type in unit_types.json (e.g. "Air Superiority", "Strike", "Anti-Ship"); builder loadout picker; ammo consumed per engagement tick; `out_of_ammo` events in log; weapon_km_override per preset (ATACMS 165 km, anti-ship missiles)
- [x] **Fuel model** — `fuel_pct` on all units; per-tick burn rates (moving vs idle) per unit type; `low_fuel` events in log; fuel bar in UnitPanel
- [x] **RTB / Rearm mission** — `rtb` mission type; air and naval route to home base then rearm+refuel; ground and emplaced SAMs rearm in place; `rearming` state suppresses combat; `rtb_complete` event; RTB button in UnitPanel; air units set `airborne=False` on landing, `True` on new mission takeoff; home_base set per unit in scenario JSON and builder
- [ ] Side-level goals in scenario JSON (`"goals": [{"type": "hold", "objective": "amari_ab"}]`)
- [ ] Commander AI per side: re-evaluates every N ticks and assigns/reassigns unit missions
- [ ] Player interface: assign goals to a side, not missions to individual units
- [ ] Reactive rules: fighters scramble when enemy air detected within sensor range
- [ ] Basic threat priority: commander weighs which objectives are under pressure
- [ ] Dispersed basing — aircraft capable of operating from roads/unprepared strips (Su-34, A-10) can be placed freely; requires `dispersed_basing: true` in unit_types.json
- [ ] Multi-unit selection — select several units at once and assign them a shared goal; units coordinate to accomplish it as a group
- [ ] **Altitude-dependent detection** — current sensor_km values are "maximum bubble" (best-case, optimal-altitude target). Real detection is horizon-limited for surface radars looking at low-flying targets: a ship radar at 30m height can only see a sea-skimming missile at ~25km despite having a 320km bubble for high-altitude aircraft. Model as `sensor_km_hi` (aircraft) vs `sensor_km_lo` (cruise missiles, surface ships) per unit type; unit altitude feeds the correct range. Relevant pairs: ship vs cruise missile (lo), ship vs fighter (hi), ground radar vs MLRS (ballistic arc = hi briefly then lo). This also means a Slava at 250km bubble can't see an F-35 at 60m ingress altitude until ~30km.

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
