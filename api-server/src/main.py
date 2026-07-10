import asyncio
import json
import random
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


class OffloadRatioRequest(BaseModel):
    ratio: float


class TestInfo(BaseModel):
    id: str
    name: str
    status: str = "ready"


TESTS = [
    TestInfo(id="T1", name="DPDK Baseline"),
    TestInfo(id="T2", name="Single Session Offload"),
    TestInfo(id="T3", name="Offload Ratio Sweep"),
    TestInfo(id="T4", name="Connection Storm"),
    TestInfo(id="T5", name="Mixed Workload"),
    TestInfo(id="T6", name="RSS Validation"),
    TestInfo(id="T7", name="Bidirectional"),
    TestInfo(id="T8", name="Offload Latency"),
    TestInfo(id="T9", name="Session Eviction"),
    TestInfo(id="T10", name="30-min Sustained"),
]

connected_clients: set[WebSocket] = set()
current_offload_ratio = 80.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(metrics_broadcaster())
    yield
    task.cancel()


app = FastAPI(title="Prism API Server", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/tests")
async def list_tests() -> list[TestInfo]:
    return TESTS


@app.post("/api/tests/{test_id}/start")
async def start_test(test_id: str):
    return {"test_id": test_id, "status": "started"}


@app.post("/api/controls/offload-ratio")
async def set_offload_ratio(req: OffloadRatioRequest):
    global current_offload_ratio
    current_offload_ratio = req.ratio
    return {"offload_ratio": current_offload_ratio}


@app.websocket("/ws/metrics")
async def metrics_websocket(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connected_clients.discard(websocket)


async def metrics_broadcaster():
    """Broadcast mock metrics to all connected WebSocket clients at 1Hz."""
    while True:
        if connected_clients:
            metrics = {
                "tx_gbps": 40 + random.random() * 60,
                "rx_gbps": 38 + random.random() * 58,
                "offload_ratio_pct": current_offload_ratio + random.uniform(-2, 2),
                "active_sessions": random.randint(1_000_000, 2_000_000),
                "vm_cpu_pct": 25 + random.random() * 20,
            }
            message = json.dumps(metrics)
            disconnected = set()
            for client in connected_clients:
                try:
                    await client.send_text(message)
                except Exception:
                    disconnected.add(client)
            connected_clients.difference_update(disconnected)
        await asyncio.sleep(1.0)
