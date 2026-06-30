from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
import asyncio
import json
import re

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from engine.simulation import SimulationEngine

SCENARIOS_DIR = Path(__file__).parent.parent / "scenarios"
THEATERS_DIR = SCENARIOS_DIR / "theaters"
DEFAULT_SCENARIO = SCENARIOS_DIR / "baltic_flashpoint.json"
UNIT_TYPES_FILE = Path(__file__).parent / "data" / "unit_types.json"

sim = SimulationEngine()
clients: List[WebSocket] = []
sim_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    sim.load_scenario(str(DEFAULT_SCENARIO))
    yield


app = FastAPI(title="Wargame Sim Engine", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def broadcast(data: dict) -> None:
    msg = json.dumps(data)
    dead = []
    for ws in clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.remove(ws)


async def _run_sim() -> None:
    while sim.running:
        sim.tick()
        await broadcast(sim.get_state())
        interval = sim.tick_duration / sim.speed_multiplier
        await asyncio.sleep(max(0.05, interval))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    clients.append(websocket)
    await websocket.send_text(json.dumps(sim.get_state()))
    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            action = msg.get("type")

            if action == "assign_mission":
                sim.assign_mission(
                    msg["unit_id"],
                    msg["mission_type"],
                    msg.get("objective_id"),
                    msg.get("patrol_lat"),
                    msg.get("patrol_lon"),
                    msg.get("target_unit_id"),
                )
                await broadcast(sim.get_state())

            elif action == "clear_mission":
                sim.clear_mission(msg["unit_id"])
                await broadcast(sim.get_state())

    except WebSocketDisconnect:
        if websocket in clients:
            clients.remove(websocket)


class SimControl(BaseModel):
    speed: Optional[float] = None


@app.post("/sim/start")
async def start_sim(control: SimControl = SimControl()) -> dict:
    global sim_task
    if control.speed is not None:
        sim.speed_multiplier = control.speed
    if not sim.running:
        sim.running = True
        sim_task = asyncio.create_task(_run_sim())
    return {"status": "running", "speed": sim.speed_multiplier}


@app.post("/sim/pause")
async def pause_sim() -> dict:
    sim.running = False
    if sim_task:
        sim_task.cancel()
    await broadcast(sim.get_state())
    return {"status": "paused"}


@app.post("/sim/speed/{multiplier}")
async def set_speed(multiplier: float) -> dict:
    sim.speed_multiplier = max(1.0, multiplier)
    return {"speed": sim.speed_multiplier}


@app.get("/state")
async def get_state() -> dict:
    return sim.get_state()


# ── Scenario builder endpoints ────────────────────────────────────────────────

@app.get("/unit-types")
async def get_unit_types() -> dict:
    """Return the full unit type capability library (omits the _comment entry)."""
    with open(UNIT_TYPES_FILE) as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


@app.get("/scenarios")
async def list_scenarios() -> list:
    """List all scenario JSON files available on disk."""
    return sorted(p.name for p in SCENARIOS_DIR.glob("*.json"))


@app.get("/scenarios/{name}")
async def get_scenario(name: str) -> dict:
    """Load and return a scenario file by name."""
    safe = re.sub(r"[^a-zA-Z0-9_\-.]", "_", name)
    path = SCENARIOS_DIR / safe
    if not path.exists() or path.suffix != ".json":
        raise HTTPException(status_code=404, detail="Scenario not found")
    with open(path) as f:
        return json.load(f)


@app.post("/scenarios/{name}")
async def save_scenario(name: str, data: dict) -> dict:
    """Save a scenario dict to disk. Filename is sanitized."""
    # Allow dots so that a name already ending in .json is preserved correctly
    safe = re.sub(r"[^a-zA-Z0-9_\-.]", "_", name)
    if not safe.endswith(".json"):
        safe += ".json"
    with open(SCENARIOS_DIR / safe, "w") as f:
        json.dump(data, f, indent=2)
    return {"saved": safe}


# ── Theater template endpoints ────────────────────────────────────────────────

@app.get("/theaters")
async def list_theaters() -> list:
    """Return metadata (id, name, description) for all available theater templates."""
    result = []
    for p in sorted(THEATERS_DIR.glob("*.json")):
        with open(p) as f:
            data = json.load(f)
        result.append({
            "id": data.get("id", p.stem),
            "name": data.get("name", p.stem),
            "description": data.get("description", ""),
        })
    return result


@app.get("/theaters/{theater_id}")
async def get_theater(theater_id: str) -> dict:
    """Return a full theater template including objectives."""
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", theater_id)
    path = THEATERS_DIR / f"{safe}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Theater not found")
    with open(path) as f:
        return json.load(f)


# ── Commander goals endpoints ─────────────────────────────────────────────────

@app.get("/goals")
async def get_goals() -> dict:
    """Return current commander goals for both sides."""
    return {
        side: [g.to_dict() for g in cmd.goals]
        for side, cmd in sim.commanders.items()
    }


class GoalsRequest(BaseModel):
    goals: list  # list of SideGoal dicts


@app.post("/goals/{side}")
async def set_goals(side: str, req: GoalsRequest) -> dict:
    """Replace all goals for a side and broadcast updated state."""
    if side not in ("blue", "red"):
        raise HTTPException(status_code=400, detail="side must be 'blue' or 'red'")
    sim.update_goals(side, req.goals)
    await broadcast(sim.get_state())
    return {"side": side, "goals": [g.to_dict() for g in sim.commanders[side].goals]}


class LoadRequest(BaseModel):
    scenario: str


@app.post("/sim/load")
async def load_scenario(req: LoadRequest) -> dict:
    """Stop the sim and reload it with a different scenario file."""
    global sim_task
    sim.running = False
    if sim_task:
        sim_task.cancel()
        sim_task = None
    safe = re.sub(r"[^a-zA-Z0-9_\-.]", "_", req.scenario)
    path = SCENARIOS_DIR / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scenario not found")
    sim.load_scenario(str(path))
    await broadcast(sim.get_state())
    return {"loaded": safe}
