"""Basic API tests for the Prism API server."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.anyio
async def test_health(client):
    """Health endpoint returns 200 with status ok."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


@pytest.mark.anyio
async def test_list_tests(client):
    """GET /api/tests returns a list of 10 tests."""
    resp = await client.get("/api/tests")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 10
    # Each test has id and name
    for test in data:
        assert "id" in test
        assert "name" in test


@pytest.mark.anyio
async def test_set_offload_ratio(client):
    """POST /api/controls/offload-ratio sets the ratio."""
    resp = await client.post(
        "/api/controls/offload-ratio",
        json={"ratio": 42.5},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["offload_ratio"] == 42.5


@pytest.mark.anyio
async def test_websocket_metrics():
    """WebSocket /ws/metrics connects and receives JSON data.

    Starts a background broadcaster to simulate the lifespan behavior,
    then verifies the client receives valid JSON with expected keys.
    """
    import asyncio

    from httpx_ws import aconnect_ws
    from httpx_ws.transport import ASGIWebSocketTransport

    from src.main import metrics_broadcaster

    # Start broadcaster manually since lifespan does not run in test transport
    broadcaster_task = asyncio.create_task(metrics_broadcaster())

    try:
        transport = ASGIWebSocketTransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with aconnect_ws("http://test/ws/metrics", client=client) as ws:
                message = await ws.receive_text(timeout=5.0)
                data = json.loads(message)
                assert "tx_gbps" in data
                assert "rx_gbps" in data
    finally:
        broadcaster_task.cancel()
        try:
            await broadcaster_task
        except asyncio.CancelledError:
            pass
