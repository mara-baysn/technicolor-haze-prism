"""Unit tests for HA (High Availability) split-brain prevention.

Tests cover:
  - Heartbeat send/receive
  - Failover detection (3 missed heartbeats)
  - Generation fencing (stale active loses)
  - Promotion/demotion endpoints
"""

import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ha import HAManager, HAState, get_ha_manager, init_ha_manager, ha_manager
from src.main import app


@pytest.fixture(autouse=True)
def reset_ha_module():
    """Reset the HA module singleton between tests."""
    import src.ha as ha_mod
    ha_mod.ha_manager = None
    yield
    ha_mod.ha_manager = None


@pytest.fixture
def client():
    """Create a TestClient with mocked lifespan (no real tc commands)."""
    with patch("src.tc_manager.ensure_ingress_qdisc"):
        with TestClient(app) as c:
            yield c


class TestHAStateEnum:
    def test_states_exist(self):
        assert HAState.ACTIVE == "ACTIVE"
        assert HAState.STANDBY == "STANDBY"
        assert HAState.FENCING == "FENCING"

    def test_states_are_strings(self):
        assert isinstance(HAState.ACTIVE, str)
        assert isinstance(HAState.STANDBY, str)
        assert isinstance(HAState.FENCING, str)


class TestHAManagerInit:
    def test_default_single_instance_is_active(self):
        mgr = HAManager(peer_url=None)
        assert mgr.role == HAState.ACTIVE
        assert mgr.generation == 1
        assert mgr.peer_url is None

    def test_with_peer_starts_standby(self):
        mgr = HAManager(peer_url="http://peer:8443")
        assert mgr.role == HAState.STANDBY
        assert mgr.peer_url == "http://peer:8443"

    def test_custom_heartbeat_config(self):
        mgr = HAManager(
            peer_url="http://peer:8443",
            heartbeat_interval_ms=200,
            heartbeat_timeout_ms=600,
        )
        assert mgr.heartbeat_interval_ms == 200
        assert mgr.heartbeat_timeout_ms == 600

    def test_default_heartbeat_config(self):
        mgr = HAManager()
        assert mgr.heartbeat_interval_ms == 100
        assert mgr.heartbeat_timeout_ms == 300


class TestHeartbeatReceive:
    def test_receive_heartbeat_from_active_peer(self):
        mgr = HAManager(peer_url="http://peer:8443")
        assert mgr.role == HAState.STANDBY

        result = mgr.receive_heartbeat(peer_generation=5, peer_role="ACTIVE")

        assert result["accepted"] is True
        assert mgr.peer_generation == 5
        assert mgr.peer_role == HAState.ACTIVE
        assert mgr.last_heartbeat_received > 0

    def test_receive_heartbeat_updates_timestamp(self):
        mgr = HAManager(peer_url="http://peer:8443")
        before = time.time()
        mgr.receive_heartbeat(peer_generation=1, peer_role="ACTIVE")
        after = time.time()

        assert before <= mgr.last_heartbeat_received <= after

    def test_receive_heartbeat_from_standby_peer_no_conflict(self):
        mgr = HAManager()  # single instance, ACTIVE
        result = mgr.receive_heartbeat(peer_generation=1, peer_role="STANDBY")

        assert result["accepted"] is True
        assert result["action"] == "none"
        assert mgr.role == HAState.ACTIVE  # unchanged

    def test_peer_alive_within_timeout(self):
        mgr = HAManager(peer_url="http://peer:8443")
        mgr.receive_heartbeat(peer_generation=1, peer_role="ACTIVE")
        assert mgr.peer_alive is True

    def test_peer_not_alive_after_timeout(self):
        mgr = HAManager(
            peer_url="http://peer:8443",
            heartbeat_timeout_ms=50,
        )
        mgr.last_heartbeat_received = time.time() - 0.1  # 100ms ago, timeout is 50ms
        assert mgr.peer_alive is False

    def test_peer_not_alive_no_heartbeat_received(self):
        mgr = HAManager(peer_url="http://peer:8443")
        assert mgr.peer_alive is False


class TestFailoverDetection:
    def test_missed_heartbeats_calculation(self):
        mgr = HAManager(
            peer_url="http://peer:8443",
            heartbeat_interval_ms=100,
            heartbeat_timeout_ms=300,
        )
        # Simulate 350ms since last heartbeat (3.5 missed intervals)
        mgr.last_heartbeat_received = time.time() - 0.35
        assert mgr.missed_heartbeats >= 3

    def test_missed_heartbeats_zero_when_fresh(self):
        mgr = HAManager(peer_url="http://peer:8443")
        mgr.receive_heartbeat(peer_generation=1, peer_role="ACTIVE")
        # Just received, should be 0
        assert mgr.missed_heartbeats == 0

    def test_missed_heartbeats_zero_no_heartbeat_ever(self):
        mgr = HAManager(peer_url="http://peer:8443")
        # Never received a heartbeat
        assert mgr.missed_heartbeats == 0

    def test_failover_threshold(self):
        """3 missed heartbeats (300ms) should trigger failover."""
        mgr = HAManager(
            peer_url="http://peer:8443",
            heartbeat_interval_ms=100,
            heartbeat_timeout_ms=300,
        )
        # Exactly at timeout boundary
        mgr.last_heartbeat_received = time.time() - 0.301
        assert mgr.missed_heartbeats >= 3
        assert mgr.peer_alive is False


class TestGenerationFencing:
    def test_promotion_increments_generation(self):
        mgr = HAManager(peer_url="http://peer:8443")
        assert mgr.generation == 1
        assert mgr.role == HAState.STANDBY

        result = mgr.promote()
        assert mgr.generation == 2
        assert mgr.role == HAState.ACTIVE
        assert result["status"] == "promoted"

    def test_multiple_promotions_increment(self):
        mgr = HAManager(peer_url="http://peer:8443")
        mgr.promote()  # gen 1 -> 2
        mgr.role = HAState.STANDBY  # simulate demotion without resetting gen
        mgr.promote()  # gen 2 -> 3
        assert mgr.generation == 3

    def test_split_brain_higher_generation_wins(self):
        """When both are ACTIVE, higher generation keeps ACTIVE."""
        mgr = HAManager()  # ACTIVE, gen=1
        mgr.generation = 5

        # Peer claims ACTIVE with lower generation
        result = mgr.receive_heartbeat(peer_generation=3, peer_role="ACTIVE")

        assert mgr.role == HAState.ACTIVE  # we stay active
        assert result["action"] == "peer_must_demote"

    def test_split_brain_lower_generation_loses(self):
        """When both are ACTIVE, lower generation demotes itself."""
        mgr = HAManager()  # ACTIVE, gen=1
        mgr.generation = 2

        # Peer claims ACTIVE with higher generation
        result = mgr.receive_heartbeat(peer_generation=7, peer_role="ACTIVE")

        assert mgr.role == HAState.STANDBY  # we demoted
        assert result["action"] == "self_demoted"

    def test_split_brain_equal_generation_fences(self):
        """Equal generations = FENCING state (operator intervention)."""
        mgr = HAManager()  # ACTIVE, gen=1
        mgr.generation = 4

        result = mgr.receive_heartbeat(peer_generation=4, peer_role="ACTIVE")

        assert mgr.role == HAState.FENCING
        assert result["action"] == "fencing_equal_generation"

    def test_demotion_calls_flush_callback(self):
        """Flush callback should be called on demotion (cleanup stale rules)."""
        mgr = HAManager()  # ACTIVE
        flush_mock = MagicMock()
        mgr._flush_callback = flush_mock

        # Peer has higher generation -> we demote
        mgr.receive_heartbeat(peer_generation=99, peer_role="ACTIVE")

        flush_mock.assert_called_once()

    def test_demotion_flush_callback_error_handled(self):
        """Flush errors should not crash the demotion."""
        mgr = HAManager()  # ACTIVE
        flush_mock = MagicMock(side_effect=Exception("flush failed"))
        mgr._flush_callback = flush_mock

        # Should not raise
        mgr.receive_heartbeat(peer_generation=99, peer_role="ACTIVE")
        assert mgr.role == HAState.STANDBY


class TestPromotionDemotion:
    def test_promote_from_standby(self):
        mgr = HAManager(peer_url="http://peer:8443")
        assert mgr.role == HAState.STANDBY

        result = mgr.promote()
        assert result["status"] == "promoted"
        assert result["previous_role"] == "STANDBY"
        assert mgr.role == HAState.ACTIVE
        assert mgr.generation == 2

    def test_promote_already_active(self):
        mgr = HAManager()  # already ACTIVE
        result = mgr.promote()
        assert result["status"] == "already_active"
        assert mgr.generation == 1  # not incremented

    def test_demote_from_active(self):
        mgr = HAManager()  # ACTIVE
        result = mgr.demote()
        assert result["status"] == "demoted"
        assert result["previous_role"] == "ACTIVE"
        assert mgr.role == HAState.STANDBY

    def test_demote_already_standby(self):
        mgr = HAManager(peer_url="http://peer:8443")
        result = mgr.demote()
        assert result["status"] == "already_standby"

    def test_promote_from_fencing(self):
        mgr = HAManager()
        mgr.role = HAState.FENCING
        result = mgr.promote()
        assert result["status"] == "promoted"
        assert result["previous_role"] == "FENCING"
        assert mgr.role == HAState.ACTIVE


class TestHAStatusEndpoint:
    def test_ha_status_default(self, client):
        response = client.get("/ha/status")
        assert response.status_code == 200
        data = response.json()
        assert data["role"] == "ACTIVE"
        assert data["generation"] == 1
        assert data["peer_url"] is None
        assert data["peer_alive"] is False
        assert data["heartbeat_interval_ms"] == 100
        assert data["heartbeat_timeout_ms"] == 300

    def test_ha_status_after_heartbeat(self, client):
        # Send a heartbeat first
        client.post("/ha/heartbeat", json={"generation": 3, "role": "STANDBY"})

        response = client.get("/ha/status")
        data = response.json()
        assert data["peer_generation"] == 3
        assert data["peer_role"] == "STANDBY"
        assert data["last_heartbeat_from_peer"] > 0


class TestHAHeartbeatEndpoint:
    def test_heartbeat_from_standby_peer(self, client):
        response = client.post(
            "/ha/heartbeat",
            json={"generation": 2, "role": "STANDBY"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["local_role"] == "ACTIVE"
        assert data["accepted"] is True

    def test_heartbeat_split_brain_detection(self, client):
        """Both ACTIVE with local winning (higher gen)."""
        # Demote first, then promote to get generation incremented to 2
        client.post("/ha/demote")
        client.post("/ha/promote")  # gen becomes 2

        response = client.post(
            "/ha/heartbeat",
            json={"generation": 1, "role": "ACTIVE"},
        )
        data = response.json()
        assert data["local_role"] == "ACTIVE"
        assert data["action"] == "peer_must_demote"

    def test_heartbeat_split_brain_self_demotes(self, client):
        """Both ACTIVE with peer winning (higher gen)."""
        response = client.post(
            "/ha/heartbeat",
            json={"generation": 99, "role": "ACTIVE"},
        )
        data = response.json()
        assert data["local_role"] == "STANDBY"
        assert data["action"] == "self_demoted"


class TestHAPromoteEndpoint:
    def test_promote_endpoint(self, client):
        # First demote so we can promote
        client.post("/ha/demote")

        response = client.post("/ha/promote")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "promoted"
        assert data["generation"] == 2

    def test_promote_already_active(self, client):
        response = client.post("/ha/promote")
        data = response.json()
        assert data["status"] == "already_active"


class TestHADemoteEndpoint:
    def test_demote_endpoint(self, client):
        response = client.post("/ha/demote")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "demoted"
        assert data["previous_role"] == "ACTIVE"

    def test_demote_already_standby(self, client):
        client.post("/ha/demote")
        response = client.post("/ha/demote")
        data = response.json()
        assert data["status"] == "already_standby"


class TestHAGetStatus:
    def test_get_status_fields(self):
        mgr = HAManager(peer_url="http://peer:8443")
        status = mgr.get_status()

        assert "role" in status
        assert "generation" in status
        assert "peer_url" in status
        assert "peer_alive" in status
        assert "peer_generation" in status
        assert "peer_role" in status
        assert "last_heartbeat_from_peer" in status
        assert "missed_heartbeats" in status
        assert "heartbeat_interval_ms" in status
        assert "heartbeat_timeout_ms" in status

    def test_get_status_values(self):
        mgr = HAManager(peer_url="http://peer:8443")
        mgr.receive_heartbeat(peer_generation=3, peer_role="ACTIVE")
        status = mgr.get_status()

        assert status["role"] == "STANDBY"
        assert status["generation"] == 1
        assert status["peer_url"] == "http://peer:8443"
        assert status["peer_generation"] == 3
        assert status["peer_role"] == "ACTIVE"


class TestModuleSingleton:
    def test_get_ha_manager_creates_default(self):
        import src.ha as ha_mod
        ha_mod.ha_manager = None

        mgr = get_ha_manager()
        assert mgr is not None
        assert mgr.role == HAState.ACTIVE
        assert mgr.peer_url is None

    def test_init_ha_manager_with_peer(self):
        mgr = init_ha_manager(peer_url="http://other:8443")
        assert mgr.peer_url == "http://other:8443"
        assert mgr.role == HAState.STANDBY

        # Singleton should return the same instance
        assert get_ha_manager() is mgr

    def test_init_ha_manager_custom_config(self):
        mgr = init_ha_manager(
            peer_url="http://other:8443",
            heartbeat_interval_ms=50,
            heartbeat_timeout_ms=150,
        )
        assert mgr.heartbeat_interval_ms == 50
        assert mgr.heartbeat_timeout_ms == 150
