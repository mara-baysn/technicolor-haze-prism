"""Prism API orchestrator — proxies to the live DPU firewall, traffic generator, and receiver.

Provides a unified API surface for the React dashboard:
- /api/firewall/* -> DPU tc-flower daemon at 192.168.0.38:8443
- /api/generator/* -> traffic-gen at 192.168.9.23:5001
- /api/receiver/* -> receiver at 192.168.9.23:5002
- /ws/metrics -> polls all 3 services every 1s, pushes unified snapshot
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# --- Service URLs ---

FIREWALL_URL = os.environ.get("PRISM_FIREWALL_URL", "http://192.168.0.38:8443")
GENERATOR_URL = os.environ.get("PRISM_GENERATOR_URL", "http://192.168.9.23:5001")
RECEIVER_URL = os.environ.get("PRISM_RECEIVER_URL", "http://192.168.9.23:5002")


# --- Pydantic models ---

class FirewallRuleRequest(BaseModel):
    dst_port: int | None = None
    src_port: int | None = None
    dst_ip: str | None = None
    src_ip: str | None = None
    protocol: str = "tcp"
    action: str = "DENY"
    priority: int = 10


class GeneratorStartRequest(BaseModel):
    profile: str | None = None
    rate: int | None = None


# --- Shared state ---

connected_clients: set[WebSocket] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the metrics broadcaster on app startup."""
    task = asyncio.create_task(metrics_broadcaster())
    yield
    task.cancel()


app = FastAPI(title="Prism API Orchestrator", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Health
# ============================================================


@app.get("/health")
async def health():
    """Health check for the orchestrator itself."""
    return {"status": "ok", "service": "prism-orchestrator"}


# ============================================================
# Firewall Proxy (DPU at 192.168.0.38:8443)
# ============================================================


@app.get("/api/firewall/rules")
async def get_firewall_rules():
    """List active tc-flower rules from the DPU."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{FIREWALL_URL}/rules")
        return resp.json()


@app.post("/api/firewall/rules")
async def add_firewall_rule(rule: FirewallRuleRequest):
    """Add a tc-flower rule on the DPU."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{FIREWALL_URL}/rules",
            json=rule.model_dump(exclude_none=True),
        )
        return resp.json()


@app.delete("/api/firewall/rules/{rule_id}")
async def delete_firewall_rule(rule_id: str):
    """Delete a tc-flower rule from the DPU."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.delete(f"{FIREWALL_URL}/rules/{rule_id}")
        return resp.json()


@app.get("/api/firewall/metrics")
async def get_firewall_metrics():
    """Get tc-flower counters from the DPU."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{FIREWALL_URL}/metrics")
        return resp.json()


@app.get("/api/firewall/health")
async def get_firewall_health():
    """Check DPU firewall daemon health."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{FIREWALL_URL}/health")
        return resp.json()


# ============================================================
# Traffic Generator Proxy (HPE ns-inet at 192.168.9.23:5001)
# ============================================================


@app.get("/api/generator/stats")
async def get_generator_stats():
    """Get traffic generator statistics."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{GENERATOR_URL}/api/stats")
            if resp.status_code != 200:
                return {"error": f"generator returned {resp.status_code}", "body": resp.text}
            return resp.json()
    except httpx.TimeoutException:
        return {"error": "generator timeout", "url": f"{GENERATOR_URL}/api/stats"}
    except httpx.ConnectError as e:
        return {"error": "generator connection refused", "detail": str(e)}
    except Exception as e:
        return {"error": "generator proxy error", "detail": str(e)}


@app.post("/api/generator/start")
async def start_generator(req: GeneratorStartRequest | None = None):
    """Start the traffic generator."""
    body = {}
    if req:
        if req.profile:
            body["profile"] = req.profile
        if req.rate:
            body["rate"] = req.rate
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{GENERATOR_URL}/api/start", json=body)
            if resp.status_code != 200:
                return {"error": f"generator returned {resp.status_code}", "body": resp.text}
            return resp.json()
    except httpx.TimeoutException:
        return {"error": "generator timeout", "url": f"{GENERATOR_URL}/api/start"}
    except httpx.ConnectError as e:
        return {"error": "generator connection refused", "detail": str(e)}
    except Exception as e:
        return {"error": "generator proxy error", "detail": str(e)}


@app.post("/api/generator/stop")
async def stop_generator():
    """Stop the traffic generator."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{GENERATOR_URL}/api/stop")
            if resp.status_code != 200:
                return {"error": f"generator returned {resp.status_code}", "body": resp.text}
            return resp.json()
    except httpx.TimeoutException:
        return {"error": "generator timeout", "url": f"{GENERATOR_URL}/api/stop"}
    except httpx.ConnectError as e:
        return {"error": "generator connection refused", "detail": str(e)}
    except Exception as e:
        return {"error": "generator proxy error", "detail": str(e)}


# ============================================================
# Receiver Proxy (HPE ns-client at 192.168.9.23:5002)
# ============================================================


@app.get("/api/receiver/stats")
async def get_receiver_stats():
    """Get receiver statistics."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{RECEIVER_URL}/api/stats")
            if resp.status_code != 200:
                return {"error": f"receiver returned {resp.status_code}", "body": resp.text}
            return resp.json()
    except httpx.TimeoutException:
        return {"error": "receiver timeout", "url": f"{RECEIVER_URL}/api/stats"}
    except httpx.ConnectError as e:
        return {"error": "receiver connection refused", "detail": str(e)}
    except Exception as e:
        return {"error": "receiver proxy error", "detail": str(e)}


@app.get("/api/receiver/health")
async def get_receiver_health():
    """Check receiver health."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{RECEIVER_URL}/health")
            if resp.status_code != 200:
                return {"error": f"receiver returned {resp.status_code}", "body": resp.text}
            return resp.json()
    except httpx.TimeoutException:
        return {"error": "receiver timeout", "url": f"{RECEIVER_URL}/health"}
    except httpx.ConnectError as e:
        return {"error": "receiver connection refused", "detail": str(e)}
    except Exception as e:
        return {"error": "receiver proxy error", "detail": str(e)}


@app.get("/api/generator/health")
async def get_generator_health():
    """Check generator health."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{GENERATOR_URL}/health")
            if resp.status_code != 200:
                return {"error": f"generator returned {resp.status_code}", "body": resp.text}
            return resp.json()
    except httpx.TimeoutException:
        return {"error": "generator timeout", "url": f"{GENERATOR_URL}/health"}
    except httpx.ConnectError as e:
        return {"error": "generator connection refused", "detail": str(e)}
    except Exception as e:
        return {"error": "generator proxy error", "detail": str(e)}


# ============================================================
# WebSocket — Aggregated Metrics Stream
# ============================================================


@app.websocket("/ws/metrics")
async def metrics_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time aggregated metrics."""
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connected_clients.discard(websocket)


async def _fetch_service(client: httpx.AsyncClient, url: str) -> dict | None:
    """Fetch JSON from a service, return None on error."""
    try:
        resp = await client.get(url, timeout=2.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


async def metrics_broadcaster():
    """Poll all 3 services every 1 second and push unified snapshot to WebSocket clients."""
    while True:
        if connected_clients:
            async with httpx.AsyncClient() as client:
                # Fetch all three in parallel
                firewall_task = _fetch_service(client, f"{FIREWALL_URL}/metrics")
                generator_task = _fetch_service(client, f"{GENERATOR_URL}/api/stats")
                receiver_task = _fetch_service(client, f"{RECEIVER_URL}/api/stats")

                firewall_data, generator_data, receiver_data = await asyncio.gather(
                    firewall_task, generator_task, receiver_task
                )

            snapshot = {
                "firewall": firewall_data or {},
                "generator": generator_data or {},
                "receiver": receiver_data or {},
                "timestamp": asyncio.get_event_loop().time(),
            }

            message = json.dumps(snapshot)

            disconnected = set()
            for ws in connected_clients:
                try:
                    await ws.send_text(message)
                except Exception:
                    disconnected.add(ws)
            connected_clients.difference_update(disconnected)

        await asyncio.sleep(1.0)


# ============================================================
# Static UI Serving (must be last — catch-all for SPA routing)
# ============================================================

_ui_dir = os.environ.get("PRISM_UI_DIR", "")
if _ui_dir:
    _ui_path = Path(_ui_dir).expanduser().resolve()
    if _ui_path.is_dir():
        # Serve static assets (JS, CSS, images)
        _assets_path = _ui_path / "assets"
        if _assets_path.is_dir():
            app.mount("/assets", StaticFiles(directory=str(_assets_path)), name="ui-assets")

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            """Serve the SPA index.html for any non-API route."""
            file_path = _ui_path / full_path
            if file_path.is_file():
                return FileResponse(str(file_path))
            return FileResponse(str(_ui_path / "index.html"))
