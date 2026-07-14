"""Unit tests for the Flask web API endpoints."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from src.generator import Profile, TrafficGenerator
from src.web import app


@pytest.fixture
def client():
    """Create a Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the generator singleton before each test."""
    import src.generator as gen_mod

    gen_mod._instance = None
    yield
    # Stop any running generator and reset
    if gen_mod._instance is not None:
        gen_mod._instance._stop_event.set()
        gen_mod._instance._running = False
        for t in gen_mod._instance._workers:
            t.join(timeout=0.5)
        gen_mod._instance._workers.clear()
    gen_mod._instance = None


class TestIndexEndpoint:
    """Tests for GET /."""

    def test_index_returns_200(self, client):
        """Index page renders successfully."""
        with patch("src.web.render_template") as mock_render:
            mock_render.return_value = "<html>dashboard</html>"
            response = client.get("/")
            assert response.status_code == 200

    def test_index_passes_context_to_template(self, client):
        """Index passes profiles, current profile, rate, and running state."""
        with patch("src.web.render_template") as mock_render:
            mock_render.return_value = "<html></html>"
            client.get("/")
            mock_render.assert_called_once_with(
                "index.html",
                profiles=[p.value for p in Profile],
                current_profile="mixed",
                rate=10,
                running=False,
            )

    def test_index_reflects_running_state(self, client):
        """Index template context reflects generator running state."""
        from src.generator import get_generator

        gen = get_generator()
        gen._running = True
        gen._profile = Profile.HTTP

        with patch("src.web.render_template") as mock_render:
            mock_render.return_value = "<html></html>"
            client.get("/")
            call_kwargs = mock_render.call_args[1]
            assert call_kwargs["running"] is True
            assert call_kwargs["current_profile"] == "http"


class TestApiStatsEndpoint:
    """Tests for GET /api/stats."""

    def test_stats_returns_json(self, client):
        """Stats endpoint returns valid JSON with expected keys."""
        response = client.get("/api/stats")
        assert response.status_code == 200
        data = response.get_json()
        assert "running" in data
        assert "profile" in data
        assert "rate_cps" in data
        assert "aggregate" in data
        assert "per_port" in data

    def test_stats_shows_correct_profile(self, client):
        """Stats shows the currently configured profile."""
        from src.generator import get_generator

        gen = get_generator()
        gen._profile = Profile.HTTPS
        response = client.get("/api/stats")
        data = response.get_json()
        assert data["profile"] == "https"

    def test_stats_shows_not_running_initially(self, client):
        """Stats shows running=False when generator is idle."""
        response = client.get("/api/stats")
        data = response.get_json()
        assert data["running"] is False

    def test_stats_shows_rate(self, client):
        """Stats includes the configured rate."""
        from src.generator import get_generator

        gen = get_generator()
        gen._rate_cps = 50
        response = client.get("/api/stats")
        data = response.get_json()
        assert data["rate_cps"] == 50

    def test_stats_aggregate_fields(self, client):
        """Aggregate stats contain required fields."""
        response = client.get("/api/stats")
        data = response.get_json()
        agg = data["aggregate"]
        assert "total_attempted" in agg
        assert "total_succeeded" in agg
        assert "total_failed" in agg
        assert "total_bytes_sent" in agg
        assert "elapsed_s" in agg
        assert "connections_per_sec" in agg


class TestApiStartEndpoint:
    """Tests for POST /api/start."""

    @patch("src.generator.TrafficGenerator._worker")
    def test_start_returns_started_status(self, mock_worker, client):
        """Start endpoint returns status=started."""
        response = client.post("/api/start", json={})
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "started"

    @patch("src.generator.TrafficGenerator._worker")
    def test_start_sets_running(self, mock_worker, client):
        """Start endpoint sets the generator to running state."""
        from src.generator import get_generator

        client.post("/api/start", json={})
        gen = get_generator()
        assert gen.running is True

    @patch("src.generator.TrafficGenerator._worker")
    def test_start_with_profile(self, mock_worker, client):
        """Start accepts a profile parameter."""
        response = client.post("/api/start", json={"profile": "http"})
        data = response.get_json()
        assert data["profile"] == "http"

    @patch("src.generator.TrafficGenerator._worker")
    def test_start_with_rate(self, mock_worker, client):
        """Start accepts a rate parameter."""
        response = client.post("/api/start", json={"rate": 25})
        data = response.get_json()
        assert data["rate"] == 25

    @patch("src.generator.TrafficGenerator._worker")
    def test_start_with_profile_and_rate(self, mock_worker, client):
        """Start accepts both profile and rate parameters."""
        response = client.post("/api/start", json={"profile": "https", "rate": 50})
        data = response.get_json()
        assert data["profile"] == "https"
        assert data["rate"] == 50

    @patch("src.generator.TrafficGenerator._worker")
    def test_start_without_json_body(self, mock_worker, client):
        """Start works with no JSON body (uses defaults)."""
        response = client.post("/api/start")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "started"
        assert data["profile"] == "mixed"

    @patch("src.generator.TrafficGenerator._worker")
    def test_start_rate_clamped_to_max(self, mock_worker, client):
        """Rate is clamped to maximum (100)."""
        response = client.post("/api/start", json={"rate": 999})
        data = response.get_json()
        assert data["rate"] == 100

    @patch("src.generator.TrafficGenerator._worker")
    def test_start_rate_clamped_to_min(self, mock_worker, client):
        """Rate is clamped to minimum (1)."""
        response = client.post("/api/start", json={"rate": 0})
        data = response.get_json()
        assert data["rate"] == 1

    @patch("src.generator.TrafficGenerator._worker")
    def test_start_idempotent_when_already_running(self, mock_worker, client):
        """Starting when already running is a no-op."""
        client.post("/api/start", json={})
        response = client.post("/api/start", json={"profile": "http"})
        data = response.get_json()
        assert data["status"] == "started"
        # Profile stays as mixed because start() returned early
        assert data["profile"] == "http"


class TestApiStopEndpoint:
    """Tests for POST /api/stop."""

    @patch("src.generator.TrafficGenerator._worker")
    def test_stop_returns_stopped_status(self, mock_worker, client):
        """Stop endpoint returns status=stopped."""
        client.post("/api/start", json={})
        response = client.post("/api/stop")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "stopped"

    def test_stop_when_not_running(self, client):
        """Stop when not running still returns success."""
        response = client.post("/api/stop")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "stopped"

    @patch("src.generator.TrafficGenerator._worker")
    def test_stop_sets_not_running(self, mock_worker, client):
        """Stop endpoint sets the generator to not running state."""
        from src.generator import get_generator

        client.post("/api/start", json={})
        client.post("/api/stop")
        gen = get_generator()
        assert gen.running is False


class TestApiStatusEndpoint:
    """Tests for GET /api/status (backward compat)."""

    def test_status_returns_json(self, client):
        """Status endpoint returns valid JSON."""
        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.get_json()
        assert "running" in data
        assert "profile" in data

    def test_status_matches_stats(self, client):
        """Status endpoint returns same data as stats endpoint."""
        stats_response = client.get("/api/stats")
        status_response = client.get("/api/status")
        assert stats_response.get_json() == status_response.get_json()


class TestMain:
    """Tests for the main() entry point."""

    @patch("src.web.app.run")
    def test_main_default_config(self, mock_run):
        """main() uses default host/port/debug."""
        from src.web import main

        with patch.dict("os.environ", {}, clear=True):
            main()
        mock_run.assert_called_once_with(host="0.0.0.0", port=5001, debug=False)

    @patch("src.web.app.run")
    def test_main_env_overrides(self, mock_run):
        """main() respects PRISM_GEN_HOST, PRISM_GEN_PORT, PRISM_GEN_DEBUG."""
        from src.web import main

        env = {
            "PRISM_GEN_HOST": "127.0.0.1",
            "PRISM_GEN_PORT": "8080",
            "PRISM_GEN_DEBUG": "1",
        }
        with patch.dict("os.environ", env, clear=True):
            main()
        mock_run.assert_called_once_with(host="127.0.0.1", port=8080, debug=True)

    @patch("src.web.app.run")
    def test_main_debug_disabled(self, mock_run):
        """main() debug is False when PRISM_GEN_DEBUG != '1'."""
        from src.web import main

        env = {"PRISM_GEN_DEBUG": "0"}
        with patch.dict("os.environ", env, clear=True):
            main()
        mock_run.assert_called_once()
        assert mock_run.call_args[1]["debug"] is False


class TestGetGeneratorSingleton:
    """Tests for the get_generator() singleton pattern."""

    def test_returns_same_instance(self):
        """get_generator() always returns the same instance."""
        from src.generator import get_generator

        gen1 = get_generator()
        gen2 = get_generator()
        assert gen1 is gen2

    def test_returns_traffic_generator(self):
        """get_generator() returns a TrafficGenerator instance."""
        from src.generator import get_generator

        gen = get_generator()
        assert isinstance(gen, TrafficGenerator)
