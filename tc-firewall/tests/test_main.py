"""Unit tests for the FastAPI endpoints in main.py — uses TestClient."""

import time
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.main import app, rules_db, default_policy
from src.models import FirewallRule, RuleAction, Protocol
from src import tc_manager


@pytest.fixture(autouse=True)
def clear_rules_db():
    """Clear the rules_db before each test."""
    rules_db.clear()
    yield
    rules_db.clear()


@pytest.fixture
def client():
    """Create a TestClient with mocked lifespan (no real tc commands)."""
    with patch("src.tc_manager.ensure_ingress_qdisc"):
        with TestClient(app) as c:
            yield c


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_response_fields(self, client):
        response = client.get("/health")
        data = response.json()
        assert "status" in data
        assert data["status"] == "healthy"
        assert "uptime_seconds" in data
        assert "active_rules" in data
        assert "default_policy" in data
        assert data["default_policy"] == "deny-all"

    def test_health_active_rules_count(self, client):
        # Inject a rule manually
        rules_db["test-1"] = FirewallRule(id="test-1", src_ip="1.2.3.4")
        response = client.get("/health")
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
        rules_db["r1"] = FirewallRule(id="r1", src_ip="10.0.0.1", action=RuleAction.ALLOW)
        rules_db["r2"] = FirewallRule(id="r2", dst_ip="10.0.0.2", action=RuleAction.DENY)
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
    @patch("src.tc_manager.flush_rules")
    def test_flush_empty(self, mock_flush, client):
        response = client.post("/rules/flush")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "flushed"
        assert data["rules_removed"] == 0

    @patch("src.tc_manager.flush_rules")
    def test_flush_with_rules(self, mock_flush, client):
        rules_db["r1"] = FirewallRule(id="r1")
        rules_db["r2"] = FirewallRule(id="r2")
        rules_db["r3"] = FirewallRule(id="r3")

        response = client.post("/rules/flush")
        assert response.status_code == 200
        data = response.json()
        assert data["rules_removed"] == 3
        assert len(rules_db) == 0

    @patch("src.tc_manager.flush_rules")
    def test_flush_calls_both_ports(self, mock_flush, client):
        client.post("/rules/flush")
        calls = mock_flush.call_args_list
        assert len(calls) == 2
        ports_flushed = {calls[0][0][0], calls[1][0][0]}
        assert ports_flushed == {tc_manager.INTERNET_PORT, tc_manager.CLIENT_PORT}

    @patch("src.tc_manager.flush_rules")
    def test_flush_tc_error_still_clears_db(self, mock_flush, client):
        mock_flush.side_effect = tc_manager.TcError("flush failed")
        rules_db["r1"] = FirewallRule(id="r1")

        response = client.post("/rules/flush")
        assert response.status_code == 200
        assert len(rules_db) == 0


class TestMetricsEndpoint:
    @patch("src.tc_manager.get_stats")
    def test_metrics_empty(self, mock_stats, client):
        mock_stats.return_value = {"packets": 0, "bytes": 0}

        response = client.get("/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["total_rules"] == 0
        assert data["hw_offloaded_rules"] == 0
        assert data["packets_forwarded"] == 0
        assert data["bytes_forwarded"] == 0
        assert data["default_policy"] == "deny-all"
        assert "uptime_seconds" in data

    @patch("src.tc_manager.get_stats")
    def test_metrics_with_rules(self, mock_stats, client):
        mock_stats.side_effect = [
            {"packets": 100, "bytes": 5000},  # internet port
            {"packets": 80, "bytes": 4000},   # client port
        ]
        rules_db["r1"] = FirewallRule(id="r1", in_hw=True, action=RuleAction.ALLOW)
        rules_db["r2"] = FirewallRule(id="r2", in_hw=False, action=RuleAction.DENY)
        rules_db["r3"] = FirewallRule(id="r3", in_hw=True, action=RuleAction.ALLOW)

        response = client.get("/metrics")
        data = response.json()
        assert data["total_rules"] == 3
        assert data["hw_offloaded_rules"] == 2
        assert data["packets_forwarded"] == 180
        assert data["bytes_forwarded"] == 9000


class TestSessionsEndpoint:
    @patch("src.conntrack.get_sessions")
    def test_sessions_empty(self, mock_get_sessions, client):
        mock_get_sessions.return_value = []

        response = client.get("/sessions")
        assert response.status_code == 200
        data = response.json()
        assert data["sessions"] == []
        assert data["count"] == 0

    @patch("src.conntrack.get_sessions")
    def test_sessions_with_entries(self, mock_get_sessions, client):
        from src.models import Session
        mock_get_sessions.return_value = [
            Session(protocol="tcp", src_ip="10.0.0.1", dst_ip="10.0.0.2", src_port=54321, dst_port=80, state="ESTABLISHED", packets=10, bytes=1000),
            Session(protocol="udp", src_ip="10.0.0.3", dst_ip="10.0.0.4", src_port=12345, dst_port=53, packets=2, bytes=200),
        ]

        response = client.get("/sessions")
        data = response.json()
        assert data["count"] == 2
        assert len(data["sessions"]) == 2
        assert data["sessions"][0]["protocol"] == "tcp"
        assert data["sessions"][1]["protocol"] == "udp"


class TestTopologyEndpoint:
    @patch("src.tc_manager.list_rules")
    def test_topology_empty(self, mock_list_rules, client):
        mock_list_rules.return_value = []

        response = client.get("/topology")
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
    def test_topology_with_rules(self, mock_list_rules, client):
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

        response = client.get("/topology")
        data = response.json()
        assert data["ports"]["internet"]["rules"] == 2
        assert data["ports"]["internet"]["hw_offloaded"] == 1
        assert data["ports"]["client"]["rules"] == 1
        assert data["ports"]["client"]["hw_offloaded"] == 1

    @patch("src.tc_manager.list_rules")
    def test_topology_port_roles(self, mock_list_rules, client):
        mock_list_rules.return_value = []

        response = client.get("/topology")
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
