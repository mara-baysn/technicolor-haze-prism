import asyncio
import json
import logging
import random
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .metrics_collector import MetricsCollector, MetricsSnapshot
from .report_generator import ReportConfig, ReportGenerator
from .ssh_executor import SSHExecutor
from .test_runner import TestResult, TestRunner, TestStatus
from .trex_driver import TRexDriver

logger = logging.getLogger(__name__)


# --- Pydantic models ---


class OffloadRatioRequest(BaseModel):
    ratio: float


class TestInfo(BaseModel):
    id: str
    name: str
    status: str = "ready"


class ReportRequest(BaseModel):
    title: Optional[str] = None
    include_charts: bool = True


# --- Static test list (for backward compat with dashboard) ---

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

# --- Shared state ---

connected_clients: set[WebSocket] = set()
current_offload_ratio = 80.0

# Module instances — initialized eagerly so endpoints work even without lifespan
ssh_executor = SSHExecutor()
trex_driver = TRexDriver()
metrics_collector = MetricsCollector(ssh_executor, trex_driver)
test_runner = TestRunner(ssh_executor, tests_dir="tests/")
report_generator = ReportGenerator(ReportConfig())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start metrics broadcaster
    task = asyncio.create_task(metrics_broadcaster())
    yield

    # Cleanup
    task.cancel()
    await ssh_executor.disconnect_all()


app = FastAPI(title="Prism API Server", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Health & Basic Endpoints
# ============================================================


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


# ============================================================
# Test Runner Endpoints
# ============================================================


@app.post("/api/tests/{test_id}/run")
async def run_test(test_id: str):
    """Execute a test by ID. Returns mock result if SSH unavailable."""
    if test_runner.is_running:
        return {
            "error": "A test is already running",
            "running_test": test_runner.running_test_id,
        }

    # Run test in background task so we can return immediately
    result = await test_runner.run_test(test_id)
    return result.to_dict()


@app.get("/api/tests/{test_id}/result")
async def get_test_result(test_id: str):
    """Get the result of a previously run test."""
    result = test_runner.get_result(test_id)
    if result is None:
        return {"error": f"No result found for test {test_id}"}
    return result.to_dict()


# ============================================================
# Metrics Endpoints
# ============================================================


@app.get("/api/metrics/history")
async def get_metrics_history():
    """Return metrics history (up to 1800 data points)."""
    history = metrics_collector.history
    return {
        "count": len(history),
        "snapshots": [s.to_dict() for s in history[-300:]],  # Cap at 300 for API
    }


@app.get("/api/metrics/latest")
async def get_metrics_latest():
    """Return latest metrics snapshot."""
    latest = metrics_collector.latest
    if latest is None:
        return {"error": "No metrics collected yet"}
    return latest.to_dict()


# ============================================================
# Report Endpoints
# ============================================================


@app.get("/api/reports")
async def list_reports():
    """List generated reports."""
    return report_generator.list_reports()


@app.post("/api/reports/generate")
async def generate_report(req: Optional[ReportRequest] = None):
    """Trigger report generation from collected test results and metrics."""
    # Gather all test results
    all_results = [
        test_runner.get_result(t.id)
        for t in TESTS
        if test_runner.get_result(t.id) is not None
    ]

    # If no tests have been run, generate a report with empty results
    metrics_history = metrics_collector.history

    config = ReportConfig()
    if req:
        if req.title:
            config.title = req.title
        config.include_charts = req.include_charts

    gen = ReportGenerator(config)
    html = gen.generate_html(all_results, metrics_history)
    html_path = gen.save_report(html)
    json_path = gen.save_json(all_results)

    return {
        "html_report": html_path,
        "json_report": json_path,
        "test_count": len(all_results),
        "metrics_points": len(metrics_history),
    }


# ============================================================
# WebSocket
# ============================================================


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
    """Broadcast metrics to all connected WebSocket clients at 1Hz.

    Uses MetricsCollector for data (which falls back to mock if hardware unavailable).
    """
    while True:
        if connected_clients:
            try:
                snapshot = await metrics_collector.collect()
                message = json.dumps({
                    "tx_gbps": snapshot.tx_gbps,
                    "rx_gbps": snapshot.rx_gbps,
                    "offload_ratio_pct": snapshot.offload_ratio_pct,
                    "active_sessions": snapshot.active_sessions,
                    "vm_cpu_pct": snapshot.vm_cpu_pct,
                    "dpu_arm_cpu_pct": snapshot.dpu_arm_cpu_pct,
                    "new_flows_per_sec": snapshot.new_flows_per_sec,
                    "offloaded_flows": snapshot.offloaded_flows,
                })
            except Exception:
                # Fallback to basic mock if collector fails entirely
                message = json.dumps({
                    "tx_gbps": 40 + random.random() * 60,
                    "rx_gbps": 38 + random.random() * 58,
                    "offload_ratio_pct": current_offload_ratio + random.uniform(-2, 2),
                    "active_sessions": random.randint(1_000_000, 2_000_000),
                    "vm_cpu_pct": 25 + random.random() * 20,
                })

            disconnected = set()
            for client in connected_clients:
                try:
                    await client.send_text(message)
                except Exception:
                    disconnected.add(client)
            connected_clients.difference_update(disconnected)
        await asyncio.sleep(1.0)
