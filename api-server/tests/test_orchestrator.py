"""Unit tests for the Prism API orchestrator proxy endpoints.

Tests all proxy routes with mocked external httpx calls so no real
DPU/generator/receiver services are required.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app, connected_clients


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Create a mock httpx.Response with .json() and .status_code."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = json.dumps(json_data)
    return resp


def _patch_httpx_client(mock_response):
    """Patch httpx.AsyncClient as a context manager returning a mock client."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.delete = AsyncMock(return_value=mock_response)

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    return mock_cm, mock_client


# ============================================================
# GET /health
# ============================================================


@pytest.mark.anyio
async def test_health_returns_ok(client):
    """GET /health returns 200 with status ok and service name."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "prism-orchestrator"


# ============================================================
# GET /api/firewall/rules
# ============================================================


@pytest.mark.anyio
async def test_get_firewall_rules(client):
    """GET /api/firewall/rules proxies to the DPU and returns rules list."""
    rules_data = [
        {"id": "rule-1", "dst_port": 80, "action": "DENY"},
        {"id": "rule-2", "src_ip": "10.0.0.5", "action": "DENY"},
    ]
    mock_resp = _mock_response(rules_data)
    mock_cm, mock_client = _patch_httpx_client(mock_resp)

    with patch("src.main.httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/firewall/rules")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["id"] == "rule-1"
    mock_client.get.assert_called_once()
    call_url = mock_client.get.call_args[0][0]
    assert "/rules" in call_url


# ============================================================
# POST /api/firewall/rules
# ============================================================


@pytest.mark.anyio
async def test_post_firewall_rule(client):
    """POST /api/firewall/rules proxies rule creation to the DPU."""
    created_rule = {"id": "rule-3", "dst_port": 443, "action": "DENY", "protocol": "tcp"}
    mock_resp = _mock_response(created_rule)
    mock_cm, mock_client = _patch_httpx_client(mock_resp)

    with patch("src.main.httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post(
            "/api/firewall/rules",
            json={"dst_port": 443, "protocol": "tcp", "action": "DENY", "priority": 10},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "rule-3"
    assert data["dst_port"] == 443
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert "/rules" in call_args[0][0]
    assert call_args[1]["json"]["dst_port"] == 443


@pytest.mark.anyio
async def test_post_firewall_rule_excludes_none_fields(client):
    """POST /api/firewall/rules excludes None fields from the proxied body."""
    mock_resp = _mock_response({"id": "rule-4", "action": "DENY"})
    mock_cm, mock_client = _patch_httpx_client(mock_resp)

    with patch("src.main.httpx.AsyncClient", return_value=mock_cm):
        resp = await client.post(
            "/api/firewall/rules",
            json={"action": "DENY", "priority": 5},
        )

    assert resp.status_code == 200
    call_body = mock_client.post.call_args[1]["json"]
    # dst_port, src_port, dst_ip, src_ip should not be present (they are None)
    assert "dst_port" not in call_body
    assert "src_port" not in call_body
    assert "dst_ip" not in call_body
    assert "src_ip" not in call_body


# ============================================================
# DELETE /api/firewall/rules/{id}
# ============================================================


@pytest.mark.anyio
async def test_delete_firewall_rule(client):
    """DELETE /api/firewall/rules/{id} proxies deletion to the DPU."""
    mock_resp = _mock_response({"deleted": True, "id": "rule-1"})
    mock_cm, mock_client = _patch_httpx_client(mock_resp)

    with patch("src.main.httpx.AsyncClient", return_value=mock_cm):
        resp = await client.delete("/api/firewall/rules/rule-1")

    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] is True
    assert data["id"] == "rule-1"
    mock_client.delete.assert_called_once()
    call_url = mock_client.delete.call_args[0][0]
    assert "rule-1" in call_url


# ============================================================
# GET /api/generator/stats
# ============================================================


@pytest.mark.anyio
async def test_get_generator_stats(client):
    """GET /api/generator/stats proxies to the traffic generator."""
    stats_data = {
        "tx_pps": 1_000_000,
        "tx_bps": 8_000_000_000,
        "flows_active": 42,
        "elapsed_sec": 120,
    }
    mock_resp = _mock_response(stats_data)
    mock_cm, mock_client = _patch_httpx_client(mock_resp)

    with patch("src.main.httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/generator/stats")

    assert resp.status_code == 200
    data = resp.json()
    assert data["tx_pps"] == 1_000_000
    assert data["flows_active"] == 42
    mock_client.get.assert_called_once()
    call_url = mock_client.get.call_args[0][0]
    assert "/api/stats" in call_url


@pytest.mark.anyio
async def test_get_generator_stats_timeout(client):
    """GET /api/generator/stats returns error on timeout."""
    import httpx as _httpx

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=_httpx.TimeoutException("timed out"))

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("src.main.httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/generator/stats")

    assert resp.status_code == 200  # FastAPI still returns 200 with error body
    data = resp.json()
    assert "error" in data
    assert "timeout" in data["error"]


@pytest.mark.anyio
async def test_get_generator_stats_connection_error(client):
    """GET /api/generator/stats returns error on connection refused."""
    import httpx as _httpx

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=_httpx.ConnectError("connection refused"))

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("src.main.httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/generator/stats")

    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    assert "connection refused" in data["error"]


# ============================================================
# GET /api/receiver/stats
# ============================================================


@pytest.mark.anyio
async def test_get_receiver_stats(client):
    """GET /api/receiver/stats proxies to the receiver service."""
    stats_data = {
        "rx_pps": 950_000,
        "rx_bps": 7_600_000_000,
        "drops": 50_000,
        "elapsed_sec": 120,
    }
    mock_resp = _mock_response(stats_data)
    mock_cm, mock_client = _patch_httpx_client(mock_resp)

    with patch("src.main.httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/receiver/stats")

    assert resp.status_code == 200
    data = resp.json()
    assert data["rx_pps"] == 950_000
    assert data["drops"] == 50_000
    mock_client.get.assert_called_once()
    call_url = mock_client.get.call_args[0][0]
    assert "/api/stats" in call_url


@pytest.mark.anyio
async def test_get_receiver_stats_timeout(client):
    """GET /api/receiver/stats returns error on timeout."""
    import httpx as _httpx

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=_httpx.TimeoutException("timed out"))

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("src.main.httpx.AsyncClient", return_value=mock_cm):
        resp = await client.get("/api/receiver/stats")

    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    assert "timeout" in data["error"]


# ============================================================
# WebSocket /ws/metrics
# ============================================================


@pytest.mark.anyio
async def test_websocket_metrics_connects():
    """WebSocket /ws/metrics accepts connection and registers client."""
    from httpx_ws import aconnect_ws
    from httpx_ws.transport import ASGIWebSocketTransport

    transport = ASGIWebSocketTransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with aconnect_ws("http://test/ws/metrics", client=ac) as ws:
            # Connection accepted means the endpoint is reachable
            # Send a ping to keep connection alive briefly
            await ws.send_text("ping")
            # Allow event loop to process the accept
            await asyncio.sleep(0.05)
            # Verify client was registered
            assert len(connected_clients) >= 1

    # After disconnect, client should be removed (give time for cleanup)
    await asyncio.sleep(0.1)


@pytest.mark.anyio
async def test_websocket_metrics_receives_broadcast():
    """WebSocket /ws/metrics receives broadcast messages from the metrics broadcaster."""
    from httpx_ws import aconnect_ws
    from httpx_ws.transport import ASGIWebSocketTransport

    # Mock all external service calls to return test data
    mock_firewall_resp = MagicMock()
    mock_firewall_resp.status_code = 200
    mock_firewall_resp.json.return_value = {"rules_count": 3, "packets_matched": 1000}

    mock_gen_resp = MagicMock()
    mock_gen_resp.status_code = 200
    mock_gen_resp.json.return_value = {"tx_pps": 500_000}

    mock_recv_resp = MagicMock()
    mock_recv_resp.status_code = 200
    mock_recv_resp.json.return_value = {"rx_pps": 480_000}

    async def mock_get(url, **kwargs):
        if "firewall" in url or "192.168.0.38" in url:
            return mock_firewall_resp
        elif "5001" in url or "generator" in url:
            return mock_gen_resp
        elif "5002" in url or "receiver" in url:
            return mock_recv_resp
        return mock_firewall_resp

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=mock_get)

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    transport = ASGIWebSocketTransport(app=app)

    with patch("src.main.httpx.AsyncClient", return_value=mock_cm):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            async with aconnect_ws("http://test/ws/metrics", client=ac) as ws:
                # Manually run one iteration of the broadcaster
                from src.main import metrics_broadcaster

                broadcaster_task = asyncio.create_task(metrics_broadcaster())
                try:
                    message = await asyncio.wait_for(ws.receive_text(), timeout=3.0)
                    data = json.loads(message)
                    assert "firewall" in data
                    assert "generator" in data
                    assert "receiver" in data
                    assert "timestamp" in data
                finally:
                    broadcaster_task.cancel()
                    try:
                        await broadcaster_task
                    except asyncio.CancelledError:
                        pass
