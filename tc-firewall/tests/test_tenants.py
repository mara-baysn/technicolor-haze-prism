"""Unit tests for per-tenant rule isolation.

Tests cover:
- Tenant registration and management
- Rule scoping (tenant A can't see tenant B's rules)
- Quota enforcement (429 when limit exceeded)
- Tenant deletion flushes all rules
- Missing X-Tenant-ID header returns 400
"""

import time
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.main import app, rules_db, nat_entries_db
from src.models import FirewallRule, NATEntry, RuleAction, Protocol
from src.tenants import tenant_manager
from src import tc_manager


@pytest.fixture(autouse=True)
def clear_state():
    """Clear all state before each test."""
    rules_db.clear()
    nat_entries_db.clear()
    # Clear tenant registrations
    tenant_manager._tenants.clear()
    yield
    rules_db.clear()
    nat_entries_db.clear()
    tenant_manager._tenants.clear()


@pytest.fixture
def client():
    """Create a TestClient with mocked lifespan (no real tc commands)."""
    with patch("src.tc_manager.ensure_ingress_qdisc"):
        with TestClient(app) as c:
            yield c


@pytest.fixture
def tenant_a(client):
    """Register tenant-a and return its ID."""
    resp = client.post("/tenants", json={
        "tenant_id": "tenant-a",
        "public_ips": ["203.0.113.1"],
        "max_rules": 5,
        "max_nat_entries": 3,
    })
    assert resp.status_code == 201
    return "tenant-a"


@pytest.fixture
def tenant_b(client):
    """Register tenant-b and return its ID."""
    resp = client.post("/tenants", json={
        "tenant_id": "tenant-b",
        "public_ips": ["203.0.113.2"],
        "max_rules": 100,
        "max_nat_entries": 50,
    })
    assert resp.status_code == 201
    return "tenant-b"


class TestTenantRegistration:
    def test_register_tenant(self, client):
        response = client.post("/tenants", json={
            "tenant_id": "acme-corp",
            "public_ips": ["1.2.3.4", "1.2.3.5"],
            "max_rules": 200,
            "max_nat_entries": 100,
        })
        assert response.status_code == 201
        data = response.json()
        assert data["tenant_id"] == "acme-corp"
        assert data["public_ips"] == ["1.2.3.4", "1.2.3.5"]
        assert data["max_rules"] == 200
        assert data["max_nat_entries"] == 100

    def test_register_tenant_defaults(self, client):
        response = client.post("/tenants", json={
            "tenant_id": "minimal-tenant",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["max_rules"] == 100
        assert data["max_nat_entries"] == 50
        assert data["public_ips"] == []

    def test_register_duplicate_tenant_returns_409(self, client):
        client.post("/tenants", json={"tenant_id": "dup-tenant"})
        response = client.post("/tenants", json={"tenant_id": "dup-tenant"})
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]

    def test_list_tenants(self, client, tenant_a, tenant_b):
        response = client.get("/tenants")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        tenant_ids = [t["tenant_id"] for t in data["tenants"]]
        assert "tenant-a" in tenant_ids
        assert "tenant-b" in tenant_ids

    def test_get_tenant_with_usage(self, client, tenant_a):
        # Add a rule for tenant-a
        rules_db["r1"] = FirewallRule(
            id="r1", tenant_id="tenant-a", src_ip="10.0.0.1"
        )

        response = client.get("/tenants/tenant-a")
        assert response.status_code == 200
        data = response.json()
        assert data["tenant_id"] == "tenant-a"
        assert data["usage"]["rules"] == 1
        assert data["usage"]["rules_remaining"] == 4  # max_rules=5 - 1
        assert data["usage"]["nat_entries"] == 0
        assert data["usage"]["nat_entries_remaining"] == 3

    def test_get_nonexistent_tenant_returns_404(self, client):
        response = client.get("/tenants/nonexistent")
        assert response.status_code == 404


class TestMissingTenantHeader:
    def test_get_rules_without_header_returns_400(self, client, tenant_a):
        response = client.get("/rules")
        assert response.status_code == 400
        assert "X-Tenant-ID header is required" in response.json()["detail"]

    def test_post_rules_without_header_returns_400(self, client, tenant_a):
        response = client.post("/rules", json={"src_ip": "10.0.0.1"})
        assert response.status_code == 400
        assert "X-Tenant-ID header is required" in response.json()["detail"]

    def test_delete_rules_without_header_returns_400(self, client, tenant_a):
        response = client.delete("/rules/some-id")
        assert response.status_code == 400

    def test_flush_rules_without_header_returns_400(self, client, tenant_a):
        response = client.post("/rules/flush")
        assert response.status_code == 400

    def test_unregistered_tenant_returns_403(self, client, tenant_a):
        response = client.get(
            "/rules", headers={"X-Tenant-ID": "unknown-tenant"}
        )
        assert response.status_code == 403
        assert "not registered" in response.json()["detail"]


class TestRuleScoping:
    @patch("src.tc_manager.check_in_hw")
    @patch("src.tc_manager.add_allow_rule")
    def test_tenant_a_cannot_see_tenant_b_rules(
        self, mock_add_allow, mock_check_hw, client, tenant_a, tenant_b
    ):
        mock_add_allow.return_value = ("0x1", "0x2")
        mock_check_hw.return_value = True

        # Tenant A creates a rule
        client.post(
            "/rules",
            json={"src_ip": "10.0.0.1", "action": "ALLOW"},
            headers={"X-Tenant-ID": "tenant-a"},
        )

        # Tenant B creates a rule
        client.post(
            "/rules",
            json={"src_ip": "10.0.0.2", "action": "ALLOW"},
            headers={"X-Tenant-ID": "tenant-b"},
        )

        # Tenant A should only see their rule
        response_a = client.get("/rules", headers={"X-Tenant-ID": "tenant-a"})
        data_a = response_a.json()
        assert data_a["count"] == 1
        assert data_a["rules"][0]["src_ip"] == "10.0.0.1"
        assert data_a["rules"][0]["tenant_id"] == "tenant-a"

        # Tenant B should only see their rule
        response_b = client.get("/rules", headers={"X-Tenant-ID": "tenant-b"})
        data_b = response_b.json()
        assert data_b["count"] == 1
        assert data_b["rules"][0]["src_ip"] == "10.0.0.2"
        assert data_b["rules"][0]["tenant_id"] == "tenant-b"

    @patch("src.tc_manager.remove_rule")
    def test_tenant_cannot_delete_other_tenants_rule(
        self, mock_remove, client, tenant_a, tenant_b
    ):
        # Inject a rule for tenant-a
        rules_db["rule-a1"] = FirewallRule(
            id="rule-a1",
            tenant_id="tenant-a",
            src_ip="10.0.0.1",
            tc_handle_fwd="0x1",
            tc_handle_rev="0x2",
            priority=100,
        )

        # Tenant B tries to delete tenant A's rule -> 403
        response = client.delete(
            "/rules/rule-a1", headers={"X-Tenant-ID": "tenant-b"}
        )
        assert response.status_code == 403
        assert "does not belong to tenant" in response.json()["detail"]

        # Rule should still exist
        assert "rule-a1" in rules_db

    @patch("src.tc_manager.remove_rule")
    def test_tenant_can_delete_own_rule(
        self, mock_remove, client, tenant_a
    ):
        # Inject a rule for tenant-a
        rules_db["rule-a1"] = FirewallRule(
            id="rule-a1",
            tenant_id="tenant-a",
            src_ip="10.0.0.1",
            tc_handle_fwd="0x1",
            tc_handle_rev="0x2",
            priority=100,
        )

        # Tenant A deletes their own rule -> 200
        response = client.delete(
            "/rules/rule-a1", headers={"X-Tenant-ID": "tenant-a"}
        )
        assert response.status_code == 200
        assert "rule-a1" not in rules_db

    @patch("src.tc_manager.flush_rules")
    @patch("src.tc_manager.remove_rule")
    def test_flush_only_removes_own_rules(
        self, mock_remove, mock_flush, client, tenant_a, tenant_b
    ):
        # Inject rules for both tenants
        rules_db["rule-a1"] = FirewallRule(
            id="rule-a1", tenant_id="tenant-a", src_ip="10.0.0.1"
        )
        rules_db["rule-a2"] = FirewallRule(
            id="rule-a2", tenant_id="tenant-a", src_ip="10.0.0.2"
        )
        rules_db["rule-b1"] = FirewallRule(
            id="rule-b1", tenant_id="tenant-b", src_ip="10.0.0.3"
        )

        # Tenant A flushes
        response = client.post(
            "/rules/flush", headers={"X-Tenant-ID": "tenant-a"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["rules_removed"] == 2

        # Tenant B's rule should still exist
        assert "rule-b1" in rules_db
        assert "rule-a1" not in rules_db
        assert "rule-a2" not in rules_db


class TestQuotaEnforcement:
    @patch("src.tc_manager.check_in_hw")
    @patch("src.tc_manager.add_allow_rule")
    def test_rule_quota_exceeded_returns_429(
        self, mock_add_allow, mock_check_hw, client, tenant_a
    ):
        mock_add_allow.return_value = ("0x1", "0x2")
        mock_check_hw.return_value = False

        # tenant-a has max_rules=5, fill up the quota
        for i in range(5):
            resp = client.post(
                "/rules",
                json={"src_ip": f"10.0.0.{i+1}", "dst_port": 80 + i},
                headers={"X-Tenant-ID": "tenant-a"},
            )
            assert resp.status_code == 201

        # 6th rule should fail with 429
        response = client.post(
            "/rules",
            json={"src_ip": "10.0.0.99", "dst_port": 9999},
            headers={"X-Tenant-ID": "tenant-a"},
        )
        assert response.status_code == 429
        assert "maximum of 5 firewall rules" in response.json()["detail"]

    @patch("src.nat_manager.add_snat")
    def test_nat_quota_exceeded_returns_429(
        self, mock_add_snat, client, tenant_a
    ):
        mock_entry = NATEntry(
            id="nat-1",
            type="snat",
            public_ip="203.0.113.1",
            private_ip="10.0.0.1",
            created_at=time.time(),
        )
        mock_add_snat.return_value = mock_entry

        # tenant-a has max_nat_entries=3, fill up
        for i in range(3):
            entry = NATEntry(
                id=f"nat-{i}",
                tenant_id="tenant-a",
                type="snat",
                public_ip="203.0.113.1",
                private_ip=f"10.0.0.{i+1}",
                created_at=time.time(),
            )
            nat_entries_db[entry.id] = entry

        # 4th NAT entry should fail with 429
        response = client.post(
            "/nat/snat",
            json={"private_ip": "10.0.0.99", "public_ip": "203.0.113.1"},
            headers={"X-Tenant-ID": "tenant-a"},
        )
        assert response.status_code == 429
        assert "maximum of 3 NAT entries" in response.json()["detail"]

    @patch("src.tc_manager.check_in_hw")
    @patch("src.tc_manager.add_allow_rule")
    def test_quota_is_per_tenant(
        self, mock_add_allow, mock_check_hw, client, tenant_a, tenant_b
    ):
        """Tenant B (max_rules=100) is not affected by tenant A's quota."""
        mock_add_allow.return_value = ("0x1", "0x2")
        mock_check_hw.return_value = False

        # Fill up tenant-a (max=5)
        for i in range(5):
            client.post(
                "/rules",
                json={"src_ip": f"10.0.0.{i+1}", "dst_port": 80 + i},
                headers={"X-Tenant-ID": "tenant-a"},
            )

        # Tenant B should still be able to add rules
        response = client.post(
            "/rules",
            json={"src_ip": "192.168.1.1"},
            headers={"X-Tenant-ID": "tenant-b"},
        )
        assert response.status_code == 201


class TestTenantDeletion:
    @patch("src.tc_manager.remove_rule")
    @patch("src.nat_manager.remove_nat")
    def test_delete_tenant_flushes_all_rules(
        self, mock_remove_nat, mock_remove_rule, client, tenant_a
    ):
        # Inject rules and NAT entries for tenant-a
        rules_db["rule-a1"] = FirewallRule(
            id="rule-a1",
            tenant_id="tenant-a",
            src_ip="10.0.0.1",
            tc_handle_fwd="0x1",
            tc_handle_rev="0x2",
            priority=100,
        )
        rules_db["rule-a2"] = FirewallRule(
            id="rule-a2",
            tenant_id="tenant-a",
            src_ip="10.0.0.2",
            tc_handle_fwd="0x3",
            tc_handle_rev="0x4",
            priority=100,
        )
        nat_entries_db["nat-a1"] = NATEntry(
            id="nat-a1",
            tenant_id="tenant-a",
            type="snat",
            public_ip="203.0.113.1",
            private_ip="10.0.0.1",
            created_at=time.time(),
        )

        response = client.delete("/tenants/tenant-a")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["tenant_id"] == "tenant-a"
        assert data["rules_flushed"] == 2
        assert data["nat_entries_flushed"] == 1

        # Verify everything is cleaned up
        assert "rule-a1" not in rules_db
        assert "rule-a2" not in rules_db
        assert "nat-a1" not in nat_entries_db
        assert not tenant_manager.tenant_exists("tenant-a")

    @patch("src.tc_manager.remove_rule")
    def test_delete_tenant_does_not_affect_other_tenants(
        self, mock_remove, client, tenant_a, tenant_b
    ):
        # Rules for both tenants
        rules_db["rule-a1"] = FirewallRule(
            id="rule-a1", tenant_id="tenant-a", src_ip="10.0.0.1"
        )
        rules_db["rule-b1"] = FirewallRule(
            id="rule-b1", tenant_id="tenant-b", src_ip="10.0.0.2"
        )

        client.delete("/tenants/tenant-a")

        # Tenant B's rule should be untouched
        assert "rule-b1" in rules_db
        assert tenant_manager.tenant_exists("tenant-b")

    def test_delete_nonexistent_tenant_returns_404(self, client):
        response = client.delete("/tenants/nonexistent")
        assert response.status_code == 404


class TestHealthEndpointNoTenantRequired:
    def test_health_does_not_require_tenant_header(self, client):
        """Health endpoint should work without X-Tenant-ID."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
