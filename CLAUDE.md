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

### Phase 1 — Combat + Detection (current)
- Unit types with sensor/weapon ranges (fighter, armor, artillery, infantry, frigate, cruiser)
- HP, damage, destruction
- Two-phase combat resolution per tick (collect attacks → apply simultaneously)
- Events broadcast over WebSocket; UI shows HP bars and event log

### Phase 2 — Autonomous Commander AI
- Goals defined per-side in scenario JSON (e.g. `"goals": [{"type": "hold", "objective": "amari_ab"}]`)
- Commander AI per side decomposes goals into unit missions every N ticks
- Player assigns goals only; AI handles all mission assignment
- Units react to threat detections (e.g. fighters scramble when enemy air detected)

### Phase 3 — Unit-level Learning
- After-action record saved per scenario run (outcomes, unit survival, goal achievement)
- Units carry experience weights that bias tactical choices in subsequent runs
- Example: an F-16 that died approaching a defended target learns to delay until SEAD

### Phase 4 — Team-level Learning
- Side-level experience aggregated from unit records
- Commander AI adjusts objective priority and unit assignment across runs
- Visible improvement: re-running the same scenario yields qualitatively different tactics

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
