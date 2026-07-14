"""Unit tests for the FastAPI endpoints in main.py — uses TestClient."""

import time
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.main import app, rules_db, default_policy, nat_entries_db
from src.models import FirewallRule, RuleAction, Protocol
from src import tc_manager
from src.tenants import tenant_manager
from tests.conftest import TenantTestClient, DEFAULT_TEST_TENANT


@pytest.fixture(autouse=True)
def clear_rules_db():
    """Clear the rules_db before each test."""
    rules_db.clear()
    nat_entries_db.clear()
    yield
    rules_db.clear()
    nat_entries_db.clear()


@pytest.fixture
def client():
    """Create a TestClient with mocked lifespan and default tenant header."""
    with patch("src.tc_manager.ensure_ingress_qdisc"):
        with TestClient(app) as c:
            yield TenantTestClient(c)


@pytest.fixture
def raw_client():
    """Create a raw TestClient without tenant header injection (for health etc)."""
    with patch("src.tc_manager.ensure_ingress_qdisc"):
        with TestClient(app) as c:
            yield c


class TestHealthEndpoint:
    def test_health_returns_200(self, raw_client):
        response = raw_client.get("/health")
        assert response.status_code == 200

    def test_health_response_fields(self, raw_client):
        response = raw_client.get("/health")
        data = response.json()
        assert "status" in data
        assert data["status"] == "healthy"
        assert "uptime_seconds" in data
        assert "active_rules" in data
        assert "default_policy" in data
        assert data["default_policy"] == "deny-all"

    def test_health_active_rules_count(self, raw_client):
        # Inject a rule manually
        rules_db["test-1"] = FirewallRule(id="test-1", tenant_id=DEFAULT_TEST_TENANT, src_ip="1.2.3.4")
        response = raw_client.get("/health")
        data = response.json()
        assert data["active_rules"] == 1


class TestListRulesEndpoint:
    def test_list_empty(self, client):
        response = client.get("/rules")
        assert response.status_code == 200
        data = response.json()
        assert data["rules"] == []
        assert data["count"] == 0
        assert data["default_policy"] == "deny-all"

    def test_list_with_rules(self, client):
        rules_db["r1"] = FirewallRule(id="r1", tenant_id=DEFAULT_TEST_TENANT, src_ip="10.0.0.1", action=RuleAction.ALLOW)
        rules_db["r2"] = FirewallRule(id="r2", tenant_id=DEFAULT_TEST_TENANT, dst_ip="10.0.0.2", action=RuleAction.DENY)
        response = client.get("/rules")
        data = response.json()
        assert data["count"] == 2
        assert len(data["rules"]) == 2


class TestAddRuleEndpoint:
    @patch("src.tc_manager.check_in_hw")
    @patch("src.tc_manager.add_allow_rule")
    def test_add_allow_rule(self, mock_add_allow, mock_check_hw, client):
        mock_add_allow.return_value = ("0x1", "0x2")
        mock_check_hw.return_value = True

        response = client.post("/rules", json={
            "src_ip": "10.0.0.1",
            "dst_ip": "10.0.0.2",
            "dst_port": 80,
            "protocol": "tcp",
            "action": "ALLOW",
            "priority": 100,
        })

        assert response.status_code == 201
        data = response.json()
        assert data["src_ip"] == "10.0.0.1"
        assert data["dst_ip"] == "10.0.0.2"
        assert data["dst_port"] == 80
        assert data["action"] == "ALLOW"
        assert data["tc_handle_fwd"] == "0x1"
        assert data["tc_handle_rev"] == "0x2"
        assert data["in_hw"] is True
        assert data["id"] is not None
        assert data["tenant_id"] == DEFAULT_TEST_TENANT

        # Verify it was stored
        assert len(rules_db) == 1

    @patch("src.tc_manager.check_in_hw")
    @patch("src.tc_manager.add_deny_rule")
    def test_add_deny_rule(self, mock_add_deny, mock_check_hw, client):
        mock_add_deny.return_value = ("0x3", "0x4")
        mock_check_hw.return_value = False

        response = client.post("/rules", json={
            "src_ip": "192.168.1.100",
            "action": "DENY",
            "protocol": "any",
        })

        assert response.status_code == 201
        data = response.json()
        assert data["action"] == "DENY"
        assert data["tc_handle_fwd"] == "0x3"
        assert data["tc_handle_rev"] == "0x4"
        assert data["in_hw"] is False

    @patch("src.tc_manager.add_allow_rule")
    def test_add_rule_tc_error(self, mock_add_allow, client):
        mock_add_allow.side_effect = tc_manager.TcError("device not found")

        response = client.post("/rules", json={
            "src_ip": "10.0.0.1",
            "action": "ALLOW",
        })

        assert response.status_code == 500
        data = response.json()
        assert "tc command failed" in data["detail"]

    def test_add_rule_invalid_port(self, client):
        response = client.post("/rules", json={
            "src_ip": "10.0.0.1",
            "dst_port": 99999,
        })
        assert response.status_code == 422

    def test_add_rule_invalid_protocol(self, client):
        response = client.post("/rules", json={
            "protocol": "invalid",
        })
        assert response.status_code == 422

    @patch("src.tc_manager.check_in_hw")
    @patch("src.tc_manager.add_allow_rule")
    def test_add_rule_minimal_request(self, mock_add_allow, mock_check_hw, client):
        mock_add_allow.return_value = ("0x1", "0x2")
        mock_check_hw.return_value = False

        response = client.post("/rules", json={})
        assert response.status_code == 201
        data = response.json()
        assert data["protocol"] == "any"
        assert data["action"] == "ALLOW"
        assert data["priority"] == 100

    @patch("src.tc_manager.check_in_hw")
    @patch("src.tc_manager.add_allow_rule")
    def test_add_rule_with_comment(self, mock_add_allow, mock_check_hw, client):
        mock_add_allow.return_value = ("0x1", "0x2")
        mock_check_hw.return_value = False

        response = client.post("/rules", json={
            "src_ip": "10.0.0.1",
            "comment": "Allow web traffic",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["comment"] == "Allow web traffic"

    @patch("src.tc_manager.check_in_hw")
    @patch("src.tc_manager.add_allow_rule")
    def test_add_rule_protocol_any_maps_to_ip(self, mock_add_allow, mock_check_hw, client):
        mock_add_allow.return_value = ("0x1", "0x2")
        mock_check_hw.return_value = False

        client.post("/rules", json={"protocol": "any"})

        # Should pass "ip" as protocol string
        call_kwargs = mock_add_allow.call_args[1]
        assert call_kwargs["protocol"] == "ip"

    @patch("src.tc_manager.check_in_hw")
    @patch("src.tc_manager.add_allow_rule")
    def test_add_rule_protocol_tcp_passes_tcp(self, mock_add_allow, mock_check_hw, client):
        mock_add_allow.return_value = ("0x1", "0x2")
        mock_check_hw.return_value = False

        client.post("/rules", json={"protocol": "tcp"})

        call_kwargs = mock_add_allow.call_args[1]
        assert call_kwargs["protocol"] == "tcp"


class TestDeleteRuleEndpoint:
    @patch("src.tc_manager.remove_rule")
    def test_delete_existing_rule(self, mock_remove, client):
        rules_db["abc123"] = FirewallRule(
            id="abc123",
            tenant_id=DEFAULT_TEST_TENANT,
            src_ip="10.0.0.1",
            tc_handle_fwd="0x1",
            tc_handle_rev="0x2",
            priority=100,
        )

        response = client.delete("/rules/abc123")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["rule_id"] == "abc123"
        assert "abc123" not in rules_db

    def test_delete_nonexistent_rule(self, client):
        response = client.delete("/rules/nonexist")
        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"]

    @patch("src.tc_manager.remove_rule")
    def test_delete_rule_no_handles(self, mock_remove, client):
        rules_db["nohandle"] = FirewallRule(
            id="nohandle",
            tenant_id=DEFAULT_TEST_TENANT,
            src_ip="10.0.0.1",
            tc_handle_fwd=None,
            tc_handle_rev=None,
            priority=100,
        )

        response = client.delete("/rules/nohandle")
        assert response.status_code == 200
        # remove_rule should not be called if handles are None
        mock_remove.assert_not_called()

    @patch("src.tc_manager.remove_rule")
    def test_delete_rule_tc_error_still_removes(self, mock_remove, client):
        """Even if tc removal fails, the rule is removed from the DB."""
        mock_remove.side_effect = tc_manager.TcError("already gone")
        rules_db["err1"] = FirewallRule(
            id="err1",
            tenant_id=DEFAULT_TEST_TENANT,
            src_ip="10.0.0.1",
            tc_handle_fwd="0x1",
            tc_handle_rev="0x2",
            priority=100,
        )

        response = client.delete("/rules/err1")
        assert response.status_code == 200
        assert "err1" not in rules_db

    @patch("src.tc_manager.remove_rule")
    def test_delete_rule_calls_remove_with_correct_ports(self, mock_remove, client):
        rules_db["porttest"] = FirewallRule(
            id="porttest",
            tenant_id=DEFAULT_TEST_TENANT,
            src_ip="10.0.0.1",
            tc_handle_fwd="0x1",
            tc_handle_rev="0x2",
            priority=200,
        )

        client.delete("/rules/porttest")

        calls = mock_remove.call_args_list
        assert len(calls) == 2
        # Forward on INTERNET_PORT
        assert calls[0][0][0] == tc_manager.INTERNET_PORT
        assert calls[0][0][1] == "0x1"
        assert calls[0][0][2] == 200
        # Reverse on CLIENT_PORT
        assert calls[1][0][0] == tc_manager.CLIENT_PORT
        assert calls[1][0][1] == "0x2"
        assert calls[1][0][2] == 200


class TestFlushRulesEndpoint:
    @patch("src.tc_manager.remove_rule")
    def test_flush_empty(self, mock_remove, client):
        response = client.post("/rules/flush")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "flushed"
        assert data["rules_removed"] == 0

    @patch("src.tc_manager.remove_rule")
    def test_flush_with_rules(self, mock_remove, client):
        rules_db["r1"] = FirewallRule(id="r1", tenant_id=DEFAULT_TEST_TENANT)
        rules_db["r2"] = FirewallRule(id="r2", tenant_id=DEFAULT_TEST_TENANT)
        rules_db["r3"] = FirewallRule(id="r3", tenant_id=DEFAULT_TEST_TENANT)

        response = client.post("/rules/flush")
        assert response.status_code == 200
        data = response.json()
        assert data["rules_removed"] == 3
        assert len(rules_db) == 0

    @patch("src.tc_manager.remove_rule")
    def test_flush_only_removes_own_tenant(self, mock_remove, client):
        """Flush only removes rules from the requesting tenant."""
        rules_db["r1"] = FirewallRule(id="r1", tenant_id=DEFAULT_TEST_TENANT)
        rules_db["r2"] = FirewallRule(id="r2", tenant_id="other-tenant")

        response = client.post("/rules/flush")
        assert response.status_code == 200
        data = response.json()
        assert data["rules_removed"] == 1
        # Other tenant's rule remains
        assert "r2" in rules_db

    @patch("src.tc_manager.remove_rule")
    def test_flush_tc_error_still_clears_db(self, mock_remove, client):
        mock_remove.side_effect = tc_manager.TcError("flush failed")
        rules_db["r1"] = FirewallRule(id="r1", tenant_id=DEFAULT_TEST_TENANT)

        response = client.post("/rules/flush")
        assert response.status_code == 200
        # Our tenant's rules are gone even on tc error
        tenant_rules = [r for r in rules_db.values() if r.tenant_id == DEFAULT_TEST_TENANT]
        assert len(tenant_rules) == 0


class TestMetricsEndpoint:
    @patch("src.tc_manager.get_stats")
    def test_metrics_empty(self, mock_stats, raw_client):
        mock_stats.return_value = {"packets": 0, "bytes": 0}

        response = raw_client.get("/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["total_rules"] == 0
        assert data["hw_offloaded_rules"] == 0
        assert data["packets_forwarded"] == 0
        assert data["bytes_forwarded"] == 0
        assert data["default_policy"] == "deny-all"
        assert "uptime_seconds" in data

    @patch("src.tc_manager.get_stats")
    def test_metrics_with_rules(self, mock_stats, raw_client):
        mock_stats.side_effect = [
            {"packets": 100, "bytes": 5000},  # internet port
            {"packets": 80, "bytes": 4000},   # client port
        ]
        rules_db["r1"] = FirewallRule(id="r1", tenant_id=DEFAULT_TEST_TENANT, in_hw=True, action=RuleAction.ALLOW)
        rules_db["r2"] = FirewallRule(id="r2", tenant_id=DEFAULT_TEST_TENANT, in_hw=False, action=RuleAction.DENY)
        rules_db["r3"] = FirewallRule(id="r3", tenant_id=DEFAULT_TEST_TENANT, in_hw=True, action=RuleAction.ALLOW)

        response = raw_client.get("/metrics")
        data = response.json()
        assert data["total_rules"] == 3
        assert data["hw_offloaded_rules"] == 2
        assert data["packets_forwarded"] == 180
        assert data["bytes_forwarded"] == 9000


class TestSessionsEndpoint:
    @patch("src.conntrack.get_sessions")
    def test_sessions_empty(self, mock_get_sessions, raw_client):
        mock_get_sessions.return_value = []

        response = raw_client.get("/sessions")
        assert response.status_code == 200
        data = response.json()
        assert data["sessions"] == []
        assert data["count"] == 0

    @patch("src.conntrack.get_sessions")
    def test_sessions_with_entries(self, mock_get_sessions, raw_client):
        from src.models import Session
        mock_get_sessions.return_value = [
            Session(protocol="tcp", src_ip="10.0.0.1", dst_ip="10.0.0.2", src_port=54321, dst_port=80, state="ESTABLISHED", packets=10, bytes=1000),
            Session(protocol="udp", src_ip="10.0.0.3", dst_ip="10.0.0.4", src_port=12345, dst_port=53, packets=2, bytes=200),
        ]

        response = raw_client.get("/sessions")
        data = response.json()
        assert data["count"] == 2
        assert len(data["sessions"]) == 2
        assert data["sessions"][0]["protocol"] == "tcp"
        assert data["sessions"][1]["protocol"] == "udp"


class TestTopologyEndpoint:
    @patch("src.tc_manager.list_rules")
    def test_topology_empty(self, mock_list_rules, raw_client):
        mock_list_rules.return_value = []

        response = raw_client.get("/topology")
        assert response.status_code == 200
        data = response.json()
        assert "ports" in data
        assert "internet" in data["ports"]
        assert "client" in data["ports"]
        assert data["ports"]["internet"]["name"] == tc_manager.INTERNET_PORT
        assert data["ports"]["client"]["name"] == tc_manager.CLIENT_PORT
        assert data["ports"]["internet"]["rules"] == 0
        assert data["ports"]["client"]["rules"] == 0
        assert data["default_policy"] == "deny-all"

    @patch("src.tc_manager.list_rules")
    def test_topology_with_rules(self, mock_list_rules, raw_client):
        from src.tc_manager import TcRuleStats
        mock_list_rules.side_effect = [
            [
                TcRuleStats(handle="0x1", priority=100, protocol="ip", in_hw=True),
                TcRuleStats(handle="0x2", priority=100, protocol="ip", in_hw=False),
            ],
            [
                TcRuleStats(handle="0x3", priority=100, protocol="ip", in_hw=True),
            ],
        ]

        response = raw_client.get("/topology")
        data = response.json()
        assert data["ports"]["internet"]["rules"] == 2
        assert data["ports"]["internet"]["hw_offloaded"] == 1
        assert data["ports"]["client"]["rules"] == 1
        assert data["ports"]["client"]["hw_offloaded"] == 1

    @patch("src.tc_manager.list_rules")
    def test_topology_port_roles(self, mock_list_rules, raw_client):
        mock_list_rules.return_value = []

        response = raw_client.get("/topology")
        data = response.json()
        assert data["ports"]["internet"]["role"] == "uplink/internet facing"
        assert data["ports"]["client"]["role"] == "VM/container facing"


class TestLifespan:
    @patch("src.tc_manager.ensure_ingress_qdisc")
    def test_startup_initializes_qdiscs(self, mock_ensure):
        with TestClient(app):
            assert mock_ensure.call_count == 2
            ports_called = {mock_ensure.call_args_list[0][0][0], mock_ensure.call_args_list[1][0][0]}
            assert ports_called == {tc_manager.INTERNET_PORT, tc_manager.CLIENT_PORT}

    @patch("src.tc_manager.ensure_ingress_qdisc")
    def test_startup_handles_failure_gracefully(self, mock_ensure):
        mock_ensure.side_effect = Exception("not on DPU")
        # Should not raise — the app should still start
        with TestClient(app) as c:
            response = c.get("/health")
            assert response.status_code == 200


class TestIdempotentRuleCreation:
    """Test that duplicate rule creation is idempotent."""

    @pytest.fixture(autouse=True)
    def clear_indexes(self):
        """Clear idempotency indexes before each test."""
        from src.main import _rule_signature_index, _idempotency_key_index
        _rule_signature_index.clear()
        _idempotency_key_index.clear()
        yield
        _rule_signature_index.clear()
        _idempotency_key_index.clear()

    @patch("src.tc_manager.check_in_hw")
    @patch("src.tc_manager.add_allow_rule")
    def test_duplicate_rule_returns_existing(self, mock_add_allow, mock_check_hw, client):
        """Adding the same rule twice returns the existing rule with 200."""
        mock_add_allow.return_value = ("0x1", "0x2")
        mock_check_hw.return_value = True

        rule_request = {
            "src_ip": "10.0.0.1",
            "dst_ip": "10.0.0.2",
            "dst_port": 80,
            "protocol": "tcp",
            "action": "ALLOW",
            "priority": 100,
        }

        # First request — creates the rule
        response1 = client.post("/rules", json=rule_request)
        assert response1.status_code == 201
        rule_id = response1.json()["id"]

        # Second request — same rule, should return existing
        response2 = client.post("/rules", json=rule_request)
        assert response2.status_code == 200
        data2 = response2.json()
        assert data2["id"] == rule_id
        assert data2["already_exists"] is True

        # tc_manager should only have been called once
        assert mock_add_allow.call_count == 1

        # Only one rule in the DB
        assert len(rules_db) == 1

    @patch("src.tc_manager.check_in_hw")
    @patch("src.tc_manager.add_allow_rule")
    def test_idempotency_key_deduplication(self, mock_add_allow, mock_check_hw, client):
        """Requests with the same idempotency_key return the same rule."""
        mock_add_allow.return_value = ("0x1", "0x2")
        mock_check_hw.return_value = False

        # First request with idempotency_key
        response1 = client.post("/rules", json={
            "src_ip": "10.0.0.5",
            "dst_port": 443,
            "protocol": "tcp",
            "action": "ALLOW",
            "idempotency_key": "tenant-a-https-rule",
        })
        assert response1.status_code == 201
        rule_id = response1.json()["id"]

        # Second request with same idempotency_key (even if fields differ slightly
        # in comment — key takes priority)
        response2 = client.post("/rules", json={
            "src_ip": "10.0.0.5",
            "dst_port": 443,
            "protocol": "tcp",
            "action": "ALLOW",
            "idempotency_key": "tenant-a-https-rule",
            "comment": "retry attempt",
        })
        assert response2.status_code == 200
        data2 = response2.json()
        assert data2["id"] == rule_id
        assert data2["already_exists"] is True

        # Only one rule created
        assert len(rules_db) == 1
        assert mock_add_allow.call_count == 1

    @patch("src.tc_manager.check_in_hw")
    @patch("src.tc_manager.add_allow_rule")
    def test_different_rules_are_not_deduplicated(self, mock_add_allow, mock_check_hw, client):
        """Rules with different fields are created as separate entries."""
        mock_add_allow.return_value = ("0x1", "0x2")
        mock_check_hw.return_value = False

        # Rule 1: port 80
        response1 = client.post("/rules", json={
            "src_ip": "10.0.0.1",
            "dst_port": 80,
            "protocol": "tcp",
            "action": "ALLOW",
        })
        assert response1.status_code == 201

        # Rule 2: port 443 (different)
        response2 = client.post("/rules", json={
            "src_ip": "10.0.0.1",
            "dst_port": 443,
            "protocol": "tcp",
            "action": "ALLOW",
        })
        assert response2.status_code == 201

        # Two separate rules
        assert len(rules_db) == 2
        assert response1.json()["id"] != response2.json()["id"]
        assert mock_add_allow.call_count == 2

    @patch("src.tc_manager.check_in_hw")
    @patch("src.tc_manager.add_allow_rule")
    @patch("src.tc_manager.remove_rule")
    def test_deleted_rule_can_be_recreated(self, mock_remove, mock_add_allow, mock_check_hw, client):
        """After deleting a rule, the same rule can be created again."""
        mock_add_allow.return_value = ("0x1", "0x2")
        mock_check_hw.return_value = False

        rule_request = {
            "src_ip": "10.0.0.1",
            "dst_port": 80,
            "protocol": "tcp",
            "action": "ALLOW",
        }

        # Create rule
        response1 = client.post("/rules", json=rule_request)
        assert response1.status_code == 201
        rule_id_1 = response1.json()["id"]

        # Delete it
        client.delete(f"/rules/{rule_id_1}")
        assert len(rules_db) == 0

        # Create same rule again — should succeed as new rule (not idempotent hit)
        response2 = client.post("/rules", json=rule_request)
        assert response2.status_code == 201
        rule_id_2 = response2.json()["id"]
        assert rule_id_2 != rule_id_1
        assert len(rules_db) == 1
