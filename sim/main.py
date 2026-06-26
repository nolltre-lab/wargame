from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
import asyncio
import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from engine.simulation import SimulationEngine

DEFAULT_SCENARIO = Path(__file__).parent.parent / "scenarios" / "baltic_flashpoint.json"

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
