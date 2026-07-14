"""Unit tests for the Flask web API endpoints."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from src.web import app


@pytest.fixture
def client():
    """Create a Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def mock_receiver():
    """Mock the TrafficReceiver singleton returned by get_receiver()."""
    receiver = MagicMock()
    receiver.running = False
    receiver.get_stats.return_value = {
        "running": False,
        "bind_ip": "10.0.2.1",
        "interface": "ens8f0v3",
        "elapsed_s": 0.0,
        "total_packets": 0,
        "total_bytes": 0,
        "total_connections": 0,
        "ports": [],
    }
    with patch("src.web.get_receiver", return_value=receiver):
        yield receiver


class TestIndexEndpoint:
    """Tests for the / dashboard endpoint."""

    def test_index_returns_200(self, client, mock_receiver):
        """Index page should return 200 OK."""
        mock_receiver.running = False
        response = client.get("/")
        assert response.status_code == 200

    def test_index_when_running(self, client, mock_receiver):
        """Index page should render when receiver is running."""
        mock_receiver.running = True
        response = client.get("/")
        assert response.status_code == 200


class TestApiStatsEndpoint:
    """Tests for the /api/stats endpoint."""

    def test_stats_returns_json(self, client, mock_receiver):
        """Stats endpoint should return JSON with expected keys."""
        response = client.get("/api/stats")
        assert response.status_code == 200
        assert response.content_type == "application/json"

        data = response.get_json()
        assert "running" in data
        assert "total_packets" in data
        assert "total_bytes" in data
        assert "total_connections" in data
        assert "ports" in data
        assert "bind_ip" in data
        assert "interface" in data
        assert "elapsed_s" in data

    def test_stats_calls_get_stats(self, client, mock_receiver):
        """Stats endpoint should call receiver.get_stats()."""
        client.get("/api/stats")
        mock_receiver.get_stats.assert_called_once()

    def test_stats_with_active_ports(self, client, mock_receiver):
        """Stats endpoint returns port data when listeners are active."""
        mock_receiver.running = True
        mock_receiver.get_stats.return_value = {
            "running": True,
            "bind_ip": "10.0.2.1",
            "interface": "ens8f0v3",
            "elapsed_s": 42.5,
            "total_packets": 150,
            "total_bytes": 9600,
            "total_connections": 12,
            "ports": [
                {
                    "port": 80,
                    "protocol": "tcp",
                    "connections": 10,
                    "packets": 100,
                    "bytes_received": 6400,
                    "last_seen_ago_s": 0.3,
                    "active": True,
                },
                {
                    "port": 53,
                    "protocol": "udp",
                    "connections": 2,
                    "packets": 50,
                    "bytes_received": 3200,
                    "last_seen_ago_s": 1.2,
                    "active": True,
                },
            ],
        }

        response = client.get("/api/stats")
        data = response.get_json()
        assert data["running"] is True
        assert data["total_packets"] == 150
        assert data["total_bytes"] == 9600
        assert len(data["ports"]) == 2
        assert data["ports"][0]["port"] == 80
        assert data["ports"][1]["protocol"] == "udp"


class TestApiStatusEndpoint:
    """Tests for the /api/status backward-compat endpoint."""

    def test_status_returns_json(self, client, mock_receiver):
        """Status endpoint should return JSON (same as stats)."""
        response = client.get("/api/status")
        assert response.status_code == 200
        assert response.content_type == "application/json"
        mock_receiver.get_stats.assert_called_once()

    def test_status_matches_stats(self, client, mock_receiver):
        """Status endpoint should return same data as stats endpoint."""
        stats_response = client.get("/api/stats")
        mock_receiver.get_stats.reset_mock()
        status_response = client.get("/api/status")
        assert stats_response.get_json() == status_response.get_json()


class TestApiStartEndpoint:
    """Tests for the /api/start endpoint."""

    def test_start_requires_post(self, client, mock_receiver):
        """Start endpoint should reject GET requests."""
        response = client.get("/api/start")
        assert response.status_code == 405

    def test_start_calls_receiver_start(self, client, mock_receiver):
        """Start endpoint should call receiver.start()."""
        response = client.post("/api/start")
        assert response.status_code == 200
        mock_receiver.start.assert_called_once()

    def test_start_returns_status(self, client, mock_receiver):
        """Start endpoint should return started status JSON."""
        response = client.post("/api/start")
        data = response.get_json()
        assert data == {"status": "started"}


class TestApiStopEndpoint:
    """Tests for the /api/stop endpoint."""

    def test_stop_requires_post(self, client, mock_receiver):
        """Stop endpoint should reject GET requests."""
        response = client.get("/api/stop")
        assert response.status_code == 405

    def test_stop_calls_receiver_stop(self, client, mock_receiver):
        """Stop endpoint should call receiver.stop()."""
        response = client.post("/api/stop")
        assert response.status_code == 200
        mock_receiver.stop.assert_called_once()

    def test_stop_returns_status(self, client, mock_receiver):
        """Stop endpoint should return stopped status JSON."""
        response = client.post("/api/stop")
        data = response.get_json()
        assert data == {"status": "stopped"}


class TestMainFunction:
    """Tests for the web module main() entry point."""

    @patch("src.web.app.run")
    @patch("src.web.get_receiver")
    def test_main_defaults(self, mock_get_receiver, mock_run):
        """main() should start receiver and run Flask with defaults."""
        receiver = MagicMock()
        mock_get_receiver.return_value = receiver

        from src.web import main

        with patch.dict("os.environ", {}, clear=True):
            main()

        receiver.start.assert_called_once()
        mock_run.assert_called_once_with(host="0.0.0.0", port=5002, debug=False)

    @patch("src.web.app.run")
    @patch("src.web.get_receiver")
    def test_main_custom_env(self, mock_get_receiver, mock_run):
        """main() should respect environment variable overrides."""
        receiver = MagicMock()
        mock_get_receiver.return_value = receiver

        from src.web import main

        env = {
            "PRISM_RCV_HOST": "127.0.0.1",
            "PRISM_RCV_PORT": "8080",
            "PRISM_RCV_DEBUG": "1",
        }
        with patch.dict("os.environ", env, clear=True):
            main()

        mock_run.assert_called_once_with(host="127.0.0.1", port=8080, debug=True)

    @patch("src.web.app.run")
    @patch("src.web.get_receiver")
    def test_main_debug_off(self, mock_get_receiver, mock_run):
        """main() should set debug=False when PRISM_RCV_DEBUG is not '1'."""
        receiver = MagicMock()
        mock_get_receiver.return_value = receiver

        from src.web import main

        env = {"PRISM_RCV_DEBUG": "0"}
        with patch.dict("os.environ", env, clear=True):
            main()

        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["debug"] is False


class TestGetReceiverSingleton:
    """Tests for the get_receiver singleton behavior."""

    def test_get_receiver_returns_instance(self):
        """get_receiver should return a TrafficReceiver instance."""
        from src.listener import TrafficReceiver, get_receiver, _instance

        # Reset singleton for isolated test
        import src.listener

        original = src.listener._instance
        src.listener._instance = None
        try:
            receiver = get_receiver()
            assert isinstance(receiver, TrafficReceiver)
        finally:
            src.listener._instance = original

    def test_get_receiver_is_singleton(self):
        """get_receiver should return the same instance on repeated calls."""
        import src.listener

        original = src.listener._instance
        src.listener._instance = None
        try:
            r1 = src.listener.get_receiver()
            r2 = src.listener.get_receiver()
            assert r1 is r2
        finally:
            src.listener._instance = original
