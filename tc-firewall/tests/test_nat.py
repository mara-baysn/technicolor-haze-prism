"""Unit tests for NAT (SNAT/DNAT/Port Forward) functionality.

Tests both the nat_manager module (with mocked subprocess) and
the FastAPI API endpoints (with TestClient).
"""

import time
from unittest.mock import patch, MagicMock, call

import pytest
from fastapi.testclient import TestClient

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.main import app, nat_entries_db
from src.models import NATEntry, SNATRule, DNATRule, PortForwardRule
from src import nat_manager
from src import tc_manager
from src.tenants import tenant_manager
from tests.conftest import TenantTestClient, DEFAULT_TEST_TENANT


@pytest.fixture(autouse=True)
def clear_nat_db():
    """Clear the NAT database before and after each test."""
    nat_manager.nat_db.clear()
    nat_entries_db.clear()
    yield
    nat_manager.nat_db.clear()
    nat_entries_db.clear()


@pytest.fixture
def client():
    """Create a TestClient with mocked lifespan and default tenant header."""
    with patch("src.tc_manager.ensure_ingress_qdisc"):
        with TestClient(app) as c:
            yield TenantTestClient(c)


class TestSNATManager:
    """Test nat_manager.add_snat directly."""

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_add_snat_basic(self, mock_run_tc, mock_get_handle, mock_check_hw):
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x1"
        mock_check_hw.return_value = True

        entry = nat_manager.add_snat("10.0.1.5", "1.2.3.4")

        assert entry.type == "snat"
        assert entry.private_ip == "10.0.1.5"
        assert entry.public_ip == "1.2.3.4"
        assert entry.in_hw is True
        assert entry.id in nat_manager.nat_db

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_add_snat_tc_command(self, mock_run_tc, mock_get_handle, mock_check_hw):
        """Verify the exact tc command constructed for SNAT."""
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x1"
        mock_check_hw.return_value = False

        nat_manager.add_snat("10.0.1.5", "1.2.3.4")

        expected_cmd = [
            "filter", "add", "dev", "pf0vf3", "ingress",
            "protocol", "ip", "prio", "20",
            "flower", "src_ip", "10.0.1.5",
            "action", "pedit", "ex", "munge", "ip", "src", "set", "1.2.3.4",
            "action", "mirred", "egress", "redirect", "dev", "pf0vf0",
        ]
        mock_run_tc.assert_called_once_with(expected_cmd)

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_add_snat_not_in_hw(self, mock_run_tc, mock_get_handle, mock_check_hw):
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x1"
        mock_check_hw.return_value = False

        entry = nat_manager.add_snat("10.0.1.5", "1.2.3.4")
        assert entry.in_hw is False

    @patch("src.nat_manager._run_tc")
    def test_add_snat_tc_error(self, mock_run_tc):
        mock_run_tc.side_effect = tc_manager.TcError("device not found")

        with pytest.raises(tc_manager.TcError, match="device not found"):
            nat_manager.add_snat("10.0.1.5", "1.2.3.4")


class TestDNATManager:
    """Test nat_manager.add_dnat directly."""

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_add_dnat_basic(self, mock_run_tc, mock_get_handle, mock_check_hw):
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x2"
        mock_check_hw.return_value = True

        entry = nat_manager.add_dnat("1.2.3.4", 443, "10.0.1.5", 443, "tcp")

        assert entry.type == "dnat"
        assert entry.public_ip == "1.2.3.4"
        assert entry.public_port == 443
        assert entry.private_ip == "10.0.1.5"
        assert entry.private_port == 443
        assert entry.protocol == "tcp"
        assert entry.in_hw is True
        assert entry.id in nat_manager.nat_db

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_add_dnat_tc_command(self, mock_run_tc, mock_get_handle, mock_check_hw):
        """Verify the exact tc command constructed for DNAT."""
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x2"
        mock_check_hw.return_value = False

        nat_manager.add_dnat("1.2.3.4", 443, "10.0.1.5", 443, "tcp")

        expected_cmd = [
            "filter", "add", "dev", "pf0vf0", "ingress",
            "protocol", "ip", "prio", "20",
            "flower", "dst_ip", "1.2.3.4",
            "ip_proto", "tcp",
            "dst_port", "443",
            "action", "pedit", "ex", "munge", "ip", "dst", "set", "10.0.1.5",
            "action", "mirred", "egress", "redirect", "dev", "pf0vf3",
        ]
        mock_run_tc.assert_called_once_with(expected_cmd)

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_add_dnat_udp(self, mock_run_tc, mock_get_handle, mock_check_hw):
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x3"
        mock_check_hw.return_value = False

        entry = nat_manager.add_dnat("5.6.7.8", 53, "10.0.2.1", 53, "udp")

        assert entry.protocol == "udp"
        cmd = mock_run_tc.call_args[0][0]
        assert "ip_proto" in cmd
        assert cmd[cmd.index("ip_proto") + 1] == "udp"


class TestPortForwardManager:
    """Test nat_manager.add_port_forward directly."""

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_add_port_forward_basic(self, mock_run_tc, mock_get_handle, mock_check_hw):
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x4"
        mock_check_hw.return_value = True

        entry = nat_manager.add_port_forward("1.2.3.4", 8080, "10.0.1.100", 80, "tcp")

        assert entry.type == "forward"
        assert entry.public_ip == "1.2.3.4"
        assert entry.public_port == 8080
        assert entry.private_ip == "10.0.1.100"
        assert entry.private_port == 80
        assert entry.protocol == "tcp"
        assert entry.in_hw is True

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_add_port_forward_tc_command(self, mock_run_tc, mock_get_handle, mock_check_hw):
        """Verify the exact tc command — includes port rewrite."""
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x4"
        mock_check_hw.return_value = False

        nat_manager.add_port_forward("1.2.3.4", 8080, "10.0.1.100", 80, "tcp")

        expected_cmd = [
            "filter", "add", "dev", "pf0vf0", "ingress",
            "protocol", "ip", "prio", "20",
            "flower", "dst_ip", "1.2.3.4",
            "ip_proto", "tcp",
            "dst_port", "8080",
            "action", "pedit", "ex", "munge", "ip", "dst", "set", "10.0.1.100",
            "action", "pedit", "ex", "munge", "tcp", "dport", "set", "80",
            "action", "mirred", "egress", "redirect", "dev", "pf0vf3",
        ]
        mock_run_tc.assert_called_once_with(expected_cmd)

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_add_port_forward_different_ports(self, mock_run_tc, mock_get_handle, mock_check_hw):
        """Port forward with different public/private ports."""
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x5"
        mock_check_hw.return_value = False

        entry = nat_manager.add_port_forward("5.6.7.8", 2222, "10.0.1.50", 22, "tcp")

        assert entry.public_port == 2222
        assert entry.private_port == 22


class TestNATRemoval:
    """Test removing NAT rules."""

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_remove_snat_rule(self, mock_run_tc, mock_get_handle, mock_check_hw):
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x1"
        mock_check_hw.return_value = False

        entry = nat_manager.add_snat("10.0.1.5", "1.2.3.4")
        rule_id = entry.id
        assert rule_id in nat_manager.nat_db

        # Reset mock to track removal calls
        mock_run_tc.reset_mock()
        nat_manager.remove_nat(rule_id)

        assert rule_id not in nat_manager.nat_db
        # Should have called tc to flush the priority
        assert mock_run_tc.called

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_remove_dnat_rule(self, mock_run_tc, mock_get_handle, mock_check_hw):
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x2"
        mock_check_hw.return_value = False

        entry = nat_manager.add_dnat("1.2.3.4", 443, "10.0.1.5", 443, "tcp")
        rule_id = entry.id

        mock_run_tc.reset_mock()
        nat_manager.remove_nat(rule_id)

        assert rule_id not in nat_manager.nat_db

    def test_remove_nonexistent_rule(self):
        with pytest.raises(tc_manager.TcError, match="not found"):
            nat_manager.remove_nat("nonexistent")


class TestNATListAndFlush:
    """Test listing and flushing NAT rules."""

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_list_nat_empty(self, mock_run_tc, mock_get_handle, mock_check_hw):
        rules = nat_manager.list_nat()
        assert rules == []

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_list_nat_with_rules(self, mock_run_tc, mock_get_handle, mock_check_hw):
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x1"
        mock_check_hw.return_value = True

        nat_manager.add_snat("10.0.1.5", "1.2.3.4")
        nat_manager.add_dnat("5.6.7.8", 80, "10.0.2.1", 80, "tcp")

        rules = nat_manager.list_nat()
        assert len(rules) == 2
        types = {r.type for r in rules}
        assert types == {"snat", "dnat"}

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_flush_nat(self, mock_run_tc, mock_get_handle, mock_check_hw):
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x1"
        mock_check_hw.return_value = False

        nat_manager.add_snat("10.0.1.5", "1.2.3.4")
        nat_manager.add_dnat("5.6.7.8", 80, "10.0.2.1", 80, "tcp")
        nat_manager.add_port_forward("1.2.3.4", 8080, "10.0.1.100", 80, "tcp")

        assert len(nat_manager.nat_db) == 3

        mock_run_tc.reset_mock()
        count = nat_manager.flush_nat()

        assert count == 3
        assert len(nat_manager.nat_db) == 0
        # Should flush both ports
        assert mock_run_tc.call_count == 2

    @patch("src.nat_manager._run_tc")
    def test_flush_nat_empty(self, mock_run_tc):
        mock_run_tc.return_value = MagicMock(returncode=0)
        count = nat_manager.flush_nat()
        assert count == 0


# --- API Endpoint Tests ---


class TestSNATEndpoint:
    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_create_snat(self, mock_run_tc, mock_get_handle, mock_check_hw, client):
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x1"
        mock_check_hw.return_value = True

        response = client.post("/nat/snat", json={
            "private_ip": "10.0.1.5",
            "public_ip": "1.2.3.4",
            "comment": "Tenant A egress",
        })

        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "snat"
        assert data["private_ip"] == "10.0.1.5"
        assert data["public_ip"] == "1.2.3.4"
        assert data["in_hw"] is True
        assert "id" in data

    @patch("src.nat_manager._run_tc")
    def test_create_snat_tc_error(self, mock_run_tc, client):
        mock_run_tc.side_effect = tc_manager.TcError("device not found")

        response = client.post("/nat/snat", json={
            "private_ip": "10.0.1.5",
            "public_ip": "1.2.3.4",
        })

        assert response.status_code == 500
        assert "tc command failed" in response.json()["detail"]

    def test_create_snat_missing_fields(self, client):
        response = client.post("/nat/snat", json={
            "private_ip": "10.0.1.5",
        })
        assert response.status_code == 422


class TestDNATEndpoint:
    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_create_dnat(self, mock_run_tc, mock_get_handle, mock_check_hw, client):
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x2"
        mock_check_hw.return_value = True

        response = client.post("/nat/dnat", json={
            "public_ip": "1.2.3.4",
            "public_port": 443,
            "private_ip": "10.0.1.5",
            "private_port": 443,
            "protocol": "tcp",
        })

        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "dnat"
        assert data["public_ip"] == "1.2.3.4"
        assert data["public_port"] == 443
        assert data["private_ip"] == "10.0.1.5"
        assert data["private_port"] == 443
        assert data["protocol"] == "tcp"
        assert data["in_hw"] is True

    def test_create_dnat_invalid_port(self, client):
        response = client.post("/nat/dnat", json={
            "public_ip": "1.2.3.4",
            "public_port": 99999,
            "private_ip": "10.0.1.5",
            "private_port": 443,
        })
        assert response.status_code == 422

    def test_create_dnat_missing_required(self, client):
        response = client.post("/nat/dnat", json={
            "public_ip": "1.2.3.4",
        })
        assert response.status_code == 422


class TestPortForwardEndpoint:
    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_create_port_forward(self, mock_run_tc, mock_get_handle, mock_check_hw, client):
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x3"
        mock_check_hw.return_value = True

        response = client.post("/nat/forward", json={
            "public_ip": "1.2.3.4",
            "public_port": 8080,
            "private_ip": "10.0.1.100",
            "private_port": 80,
            "protocol": "tcp",
        })

        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "forward"
        assert data["public_ip"] == "1.2.3.4"
        assert data["public_port"] == 8080
        assert data["private_ip"] == "10.0.1.100"
        assert data["private_port"] == 80

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_create_port_forward_different_ports(self, mock_run_tc, mock_get_handle, mock_check_hw, client):
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x4"
        mock_check_hw.return_value = False

        response = client.post("/nat/forward", json={
            "public_ip": "5.6.7.8",
            "public_port": 2222,
            "private_ip": "10.0.1.50",
            "private_port": 22,
            "protocol": "tcp",
        })

        assert response.status_code == 201
        data = response.json()
        assert data["public_port"] == 2222
        assert data["private_port"] == 22


class TestNATListEndpoint:
    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_list_nat_empty(self, mock_run_tc, mock_get_handle, mock_check_hw, client):
        response = client.get("/nat")
        assert response.status_code == 200
        data = response.json()
        assert data["rules"] == []
        assert data["count"] == 0

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_list_nat_with_rules(self, mock_run_tc, mock_get_handle, mock_check_hw, client):
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x1"
        mock_check_hw.return_value = False

        client.post("/nat/snat", json={
            "private_ip": "10.0.1.5",
            "public_ip": "1.2.3.4",
        })
        client.post("/nat/dnat", json={
            "public_ip": "5.6.7.8",
            "public_port": 80,
            "private_ip": "10.0.2.1",
            "private_port": 80,
        })

        response = client.get("/nat")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2


class TestNATDeleteEndpoint:
    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_delete_nat_rule(self, mock_run_tc, mock_get_handle, mock_check_hw, client):
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x1"
        mock_check_hw.return_value = False

        # Create a rule first
        create_resp = client.post("/nat/snat", json={
            "private_ip": "10.0.1.5",
            "public_ip": "1.2.3.4",
        })
        rule_id = create_resp.json()["id"]

        # Delete it
        response = client.delete(f"/nat/{rule_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["rule_id"] == rule_id

        # Verify it's gone
        list_resp = client.get("/nat")
        assert list_resp.json()["count"] == 0

    def test_delete_nonexistent_nat_rule(self, client):
        response = client.delete("/nat/nonexistent")
        assert response.status_code == 404


class TestNATFlushEndpoint:
    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_flush_nat(self, mock_run_tc, mock_get_handle, mock_check_hw, client):
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x1"
        mock_check_hw.return_value = False

        # Create some rules
        client.post("/nat/snat", json={
            "private_ip": "10.0.1.5",
            "public_ip": "1.2.3.4",
        })
        client.post("/nat/dnat", json={
            "public_ip": "5.6.7.8",
            "public_port": 443,
            "private_ip": "10.0.2.1",
            "private_port": 443,
        })

        response = client.post("/nat/flush")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "flushed"
        assert data["rules_removed"] == 2

        # Verify empty
        list_resp = client.get("/nat")
        assert list_resp.json()["count"] == 0

    @patch("src.nat_manager._run_tc")
    def test_flush_nat_empty(self, mock_run_tc, client):
        mock_run_tc.return_value = MagicMock(returncode=0)

        response = client.post("/nat/flush")
        assert response.status_code == 200
        data = response.json()
        assert data["rules_removed"] == 0


# --- Idempotency Tests ---


class TestNATIdempotency:
    """Test that duplicate NAT rule creation is idempotent."""

    @pytest.fixture(autouse=True)
    def clear_nat_signature_index(self):
        """Clear the NAT signature index before each test."""
        nat_manager._nat_signature_index.clear()
        yield
        nat_manager._nat_signature_index.clear()

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_duplicate_snat_returns_existing(self, mock_run_tc, mock_get_handle, mock_check_hw):
        """Adding the same SNAT rule twice returns the existing entry."""
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x1"
        mock_check_hw.return_value = True

        entry1 = nat_manager.add_snat("10.0.1.5", "1.2.3.4")
        entry2 = nat_manager.add_snat("10.0.1.5", "1.2.3.4")

        # Same entry returned
        assert entry1.id == entry2.id
        # tc was only called once
        assert mock_run_tc.call_count == 1
        # Only one entry in the DB
        assert len(nat_manager.nat_db) == 1

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_duplicate_dnat_returns_existing(self, mock_run_tc, mock_get_handle, mock_check_hw):
        """Adding the same DNAT rule twice returns the existing entry."""
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x2"
        mock_check_hw.return_value = True

        entry1 = nat_manager.add_dnat("1.2.3.4", 443, "10.0.1.5", 443, "tcp")
        entry2 = nat_manager.add_dnat("1.2.3.4", 443, "10.0.1.5", 443, "tcp")

        assert entry1.id == entry2.id
        assert mock_run_tc.call_count == 1
        assert len(nat_manager.nat_db) == 1

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_duplicate_port_forward_returns_existing(self, mock_run_tc, mock_get_handle, mock_check_hw):
        """Adding the same port forward rule twice returns the existing entry."""
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x3"
        mock_check_hw.return_value = False

        entry1 = nat_manager.add_port_forward("1.2.3.4", 8080, "10.0.1.100", 80, "tcp")
        entry2 = nat_manager.add_port_forward("1.2.3.4", 8080, "10.0.1.100", 80, "tcp")

        assert entry1.id == entry2.id
        assert mock_run_tc.call_count == 1
        assert len(nat_manager.nat_db) == 1

    @patch("src.nat_manager.check_in_hw")
    @patch("src.nat_manager._get_last_handle")
    @patch("src.nat_manager._run_tc")
    def test_different_dnat_not_deduplicated(self, mock_run_tc, mock_get_handle, mock_check_hw):
        """DNAT rules with different ports are created separately."""
        mock_run_tc.return_value = MagicMock(returncode=0)
        mock_get_handle.return_value = "0x2"
        mock_check_hw.return_value = False

        entry1 = nat_manager.add_dnat("1.2.3.4", 80, "10.0.1.5", 80, "tcp")
        entry2 = nat_manager.add_dnat("1.2.3.4", 443, "10.0.1.5", 443, "tcp")

        assert entry1.id != entry2.id
        assert len(nat_manager.nat_db) == 2
        assert mock_run_tc.call_count == 2
