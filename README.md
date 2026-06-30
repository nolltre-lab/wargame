# Wargame

Autonomous military simulation where sides pursue high-level goals without player micro-management. The player defines a scenario and assigns side-level goals (e.g. "hold this airfield," "capture this city"); a Commander AI decomposes those goals into unit missions and handles all tactical execution. The focus is on emergent outcomes, not RTS-style unit control.

A backend tick engine simulates detection, combat, movement, and terrain-aware routing across an arbitrary theater, and a React/MapLibre frontend renders the battle live over real-world coastline data with NATO (APP-6/MIL-STD-2525) unit symbology.

## Architecture

```
wargame/
├── sim/                   # Python FastAPI backend
│   ├── main.py             # WebSocket server + REST endpoints
│   ├── requirements.txt
│   ├── data/
│   │   ├── unit_types.json     # Unit library: sensors, weapons, loadouts, fuel/speed
│   │   └── coastline/          # Natural Earth land polygons (terrain routing)
│   └── engine/
│       ├── simulation.py   # Tick engine (60s ticks, adjustable speed)
│       ├── unit.py         # Unit model (hp, mission, waypoints, fuel, ammo)
│       ├── combat.py       # Detection + engagement resolution
│       ├── ai.py           # Mission execution AI (waypoint/patrol/intercept logic)
│       ├── commander.py    # Side-level Commander AI (goal -> mission assignment)
│       ├── terrain.py      # Land/water model + A* routing around coastlines
│       ├── objective.py    # Objective model
│       └── geo.py          # Haversine, bearing, destination
├── scenarios/
│   ├── baltic_flashpoint.json   # NATO vs Russia/Estonia scenario
│   └── theaters/                 # Reusable theater base maps (objectives only)
└── ui/                     # React + Vite + TypeScript frontend
    └── src/
        ├── App.tsx
        ├── components/
        │   ├── MapView.tsx          # MapLibre GL + milsymbol NATO markers
        │   ├── ScenarioBuilder.tsx  # Place units/objectives, save/load scenarios
        │   ├── GoalsPanel.tsx       # Assign side-level goals to the Commander AI
        │   ├── UnitPanel.tsx        # Selected unit details + manual mission override
        │   └── EventLog.tsx         # Combat/capture/event feed
        ├── hooks/useSimSocket.ts
        ├── store/simStore.ts        # Zustand state store
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

Or use the one-shot launcher from the repo root: `./start.sh`

## Design Principles

- **Battalion minimum** — ground forces are never controlled below battalion level
- **Goal-oriented** — units receive goals ("protect X," "capture Y"), not move orders
- **No micro-management** — the player sets scenario goals; the Commander AI decomposes and executes them
- **Theater-agnostic** — terrain-aware routing is built on real coastline data (Natural Earth), so it works on any map, not just the Baltic
- **Learning (planned)** — units and teams will accumulate experience across runs of the same scenario

## Status

Core combat, detection, and an autonomous per-side Commander AI are in place: goal-oriented mission assignment, RTB/rearm cycles, fuel and magazine modeling, networked (data-link) detection, and a scenario builder. See `CLAUDE.md` for the detailed development roadmap.

## Target Hardware

Developed and tuned for Apple Silicon (M1 Max). Pure Python/asyncio handles the current scale comfortably; planned learning phases will use `numpy`/`scipy` and PyTorch's `mps` backend.
