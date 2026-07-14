"""Unit tests for the AuditLogger and GET /audit endpoint."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.audit import AuditLogger
from src.main import app, rules_db, audit, nat_entries_db
from src.models import FirewallRule, RuleAction, Protocol, NATEntry
from src import tc_manager
from tests.conftest import TenantTestClient, DEFAULT_TEST_TENANT


@pytest.fixture
def client():
    """Create a TenantTestClient with mocked lifespan (no real tc commands)."""
    with patch("src.tc_manager.ensure_ingress_qdisc"):
        with TestClient(app) as c:
            yield TenantTestClient(c)


@pytest.fixture
def raw_client():
    """Create a raw TestClient without tenant header injection (for /audit)."""
    with patch("src.tc_manager.ensure_ingress_qdisc"):
        with TestClient(app) as c:
            yield c


@pytest.fixture(autouse=True)
def clear_state():
    """Clear stores before each test."""
    rules_db.clear()
    nat_entries_db.clear()
    yield
    rules_db.clear()
    nat_entries_db.clear()


@pytest.fixture
def temp_audit_logger():
    """Create an AuditLogger writing to a temp directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = AuditLogger(log_dir=tmpdir, log_file="test_audit.jsonl")
        yield logger, tmpdir


class TestAuditLoggerUnit:
    """Unit tests for the AuditLogger class itself."""

    def test_log_creates_entry_with_required_fields(self, temp_audit_logger):
        logger, tmpdir = temp_audit_logger

        entry = logger.log(
            action="rule.created",
            resource_type="firewall_rule",
            resource_id="abc123",
            details={"dst_port": 80, "protocol": "tcp", "action": "DENY"},
            source_ip="192.168.9.16",
        )

        assert entry["action"] == "rule.created"
        assert entry["actor"] == "api"
        assert entry["resource_type"] == "firewall_rule"
        assert entry["resource_id"] == "abc123"
        assert entry["details"]["dst_port"] == 80
        assert entry["result"] == "success"
        assert entry["source_ip"] == "192.168.9.16"
        assert "timestamp" in entry

    def test_log_writes_json_to_file(self, temp_audit_logger):
        logger, tmpdir = temp_audit_logger

        logger.log(
            action="rule.created",
            resource_type="firewall_rule",
            resource_id="test1",
            source_ip="10.0.0.1",
        )

        log_path = os.path.join(tmpdir, "test_audit.jsonl")
        assert os.path.exists(log_path)

        with open(log_path) as f:
            lines = f.readlines()

        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["action"] == "rule.created"
        assert data["resource_id"] == "test1"

    def test_log_multiple_entries(self, temp_audit_logger):
        logger, tmpdir = temp_audit_logger

        logger.log(action="rule.created", resource_type="firewall_rule", resource_id="r1")
        logger.log(action="rule.deleted", resource_type="firewall_rule", resource_id="r2")
        logger.log(action="rules.flushed", resource_type="firewall_rule", resource_id="*")

        log_path = os.path.join(tmpdir, "test_audit.jsonl")
        with open(log_path) as f:
            lines = f.readlines()

        assert len(lines) == 3

    def test_get_recent_returns_newest_first(self, temp_audit_logger):
        logger, tmpdir = temp_audit_logger

        logger.log(action="first", resource_type="t", resource_id="1")
        logger.log(action="second", resource_type="t", resource_id="2")
        logger.log(action="third", resource_type="t", resource_id="3")

        recent = logger.get_recent(limit=10)
        assert len(recent) == 3
        assert recent[0]["action"] == "third"
        assert recent[1]["action"] == "second"
        assert recent[2]["action"] == "first"

    def test_get_recent_respects_limit(self, temp_audit_logger):
        logger, tmpdir = temp_audit_logger

        for i in range(50):
            logger.log(action=f"action_{i}", resource_type="t", resource_id=str(i))

        recent = logger.get_recent(limit=10)
        assert len(recent) == 10
        # Should be the 10 newest
        assert recent[0]["action"] == "action_49"
        assert recent[9]["action"] == "action_40"

    def test_log_daemon_start(self, temp_audit_logger):
        logger, tmpdir = temp_audit_logger

        entry = logger.log_daemon_start()
        assert entry["action"] == "daemon.started"
        assert entry["actor"] == "system"
        assert entry["resource_type"] == "system"

    def test_log_daemon_stop(self, temp_audit_logger):
        logger, tmpdir = temp_audit_logger

        entry = logger.log_daemon_stop()
        assert entry["action"] == "daemon.stopped"
        assert entry["actor"] == "system"

    def test_log_rule_created_convenience(self, temp_audit_logger):
        logger, tmpdir = temp_audit_logger

        entry = logger.log_rule_created(
            rule_id="abc123",
            details={"dst_port": 443, "protocol": "tcp", "action": "ALLOW"},
            source_ip="10.0.0.5",
        )

        assert entry["action"] == "rule.created"
        assert entry["resource_type"] == "firewall_rule"
        assert entry["resource_id"] == "abc123"
        assert entry["details"]["dst_port"] == 443
        assert entry["source_ip"] == "10.0.0.5"

    def test_log_rule_deleted_convenience(self, temp_audit_logger):
        logger, tmpdir = temp_audit_logger

        entry = logger.log_rule_deleted(rule_id="xyz789", source_ip="10.0.0.6")

        assert entry["action"] == "rule.deleted"
        assert entry["resource_id"] == "xyz789"

    def test_log_nat_created_convenience(self, temp_audit_logger):
        logger, tmpdir = temp_audit_logger

        entry = logger.log_nat_created(
            nat_type="snat",
            rule_id="nat1",
            details={"private_ip": "10.0.0.1", "public_ip": "1.2.3.4"},
            source_ip="10.0.0.7",
        )

        assert entry["action"] == "nat.snat_created"
        assert entry["resource_type"] == "nat_rule"
        assert entry["resource_id"] == "nat1"

    def test_unknown_source_ip_defaults_to_unknown(self, temp_audit_logger):
        logger, tmpdir = temp_audit_logger

        entry = logger.log(
            action="test",
            resource_type="t",
            resource_id="r",
            source_ip=None,
        )
        assert entry["source_ip"] == "unknown"


class TestAuditFileRotation:
    """Test that file rotation is configured correctly."""

    def test_rotation_config(self):
        """Verify rotation parameters are set correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(
                log_dir=tmpdir,
                log_file="rotate_test.jsonl",
                max_bytes=1024,  # 1KB for testing
                backup_count=3,
            )

            # Write enough data to trigger rotation
            for i in range(100):
                logger.log(
                    action=f"action_{i}",
                    resource_type="firewall_rule",
                    resource_id=f"rule_{i}",
                    details={"padding": "x" * 50},
                )

            # Check that backup files were created
            files = os.listdir(tmpdir)
            log_files = [f for f in files if f.startswith("rotate_test")]
            # Should have the main file + at least 1 backup
            assert len(log_files) > 1
            # Should not exceed backup_count + 1 (main file)
            assert len(log_files) <= 4  # main + 3 backups

    def test_rotation_does_not_lose_data(self):
        """Verify in-memory buffer retains entries after rotation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(
                log_dir=tmpdir,
                log_file="rotate_mem.jsonl",
                max_bytes=512,
                backup_count=2,
            )

            for i in range(50):
                logger.log(
                    action=f"action_{i}",
                    resource_type="t",
                    resource_id=str(i),
                )

            # In-memory buffer should still have all entries
            recent = logger.get_recent(limit=50)
            assert len(recent) == 50

    def test_fallback_when_dir_not_writable(self):
        """AuditLogger works in memory-only mode if dir is unwritable."""
        logger = AuditLogger(
            log_dir="/nonexistent/path/that/wont/exist",
            log_file="audit.jsonl",
        )

        # Should not raise
        entry = logger.log(
            action="test",
            resource_type="t",
            resource_id="r",
        )
        assert entry["action"] == "test"

        # In-memory buffer still works
        recent = logger.get_recent()
        assert len(recent) == 1


class TestAuditEndpoint:
    """Test the GET /audit API endpoint."""

    def test_audit_endpoint_returns_entries(self, raw_client):
        """GET /audit returns audit entries."""
        audit._recent_entries.clear()

        # Add some entries directly
        audit.log(action="test.event", resource_type="test", resource_id="1")

        response = raw_client.get("/audit")
        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert "count" in data
        assert data["count"] >= 1

    def test_audit_endpoint_with_limit(self, raw_client):
        """GET /audit respects the limit parameter."""
        audit._recent_entries.clear()

        for i in range(5):
            audit.log(
                action=f"test.action_{i}",
                resource_type="test",
                resource_id=str(i),
            )

        response = raw_client.get("/audit?limit=3")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3
        assert len(data["entries"]) == 3

    def test_audit_endpoint_default_limit(self, raw_client):
        """GET /audit defaults to 100 entries."""
        audit._recent_entries.clear()

        for i in range(150):
            audit.log(action=f"a{i}", resource_type="t", resource_id=str(i))

        response = raw_client.get("/audit")
        data = response.json()
        assert data["count"] == 100

    def test_audit_endpoint_invalid_limit(self, raw_client):
        """GET /audit rejects invalid limit values."""
        response = raw_client.get("/audit?limit=0")
        assert response.status_code == 422

        response = raw_client.get("/audit?limit=2000")
        assert response.status_code == 422


class TestAuditIntegration:
    """Integration tests: verify endpoints generate audit entries."""

    @patch("src.tc_manager.check_in_hw")
    @patch("src.tc_manager.add_allow_rule")
    def test_rule_creation_generates_audit_entry(
        self, mock_add_allow, mock_check_hw, client
    ):
        mock_add_allow.return_value = ("0x1", "0x2")
        mock_check_hw.return_value = True

        audit._recent_entries.clear()

        response = client.post("/rules", json={
            "src_ip": "10.0.0.1",
            "dst_ip": "10.0.0.2",
            "dst_port": 80,
            "protocol": "tcp",
            "action": "ALLOW",
            "priority": 100,
        })

        assert response.status_code == 201

        # Check audit entries
        entries = audit.get_recent(limit=10)
        rule_entries = [e for e in entries if e["action"] == "rule.created"]
        assert len(rule_entries) >= 1

        entry = rule_entries[0]
        assert entry["resource_type"] == "firewall_rule"
        assert entry["details"]["dst_port"] == 80
        assert entry["details"]["protocol"] == "tcp"
        assert entry["details"]["action"] == "ALLOW"
        assert entry["source_ip"] == "testclient"

    @patch("src.tc_manager.remove_rule")
    def test_rule_deletion_generates_audit_entry(self, mock_remove, client):
        rules_db["del-test"] = FirewallRule(
            id="del-test",
            tenant_id=DEFAULT_TEST_TENANT,
            src_ip="10.0.0.1",
            tc_handle_fwd="0x1",
            tc_handle_rev="0x2",
            priority=100,
        )

        audit._recent_entries.clear()

        response = client.delete("/rules/del-test")
        assert response.status_code == 200

        entries = audit.get_recent(limit=10)
        del_entries = [e for e in entries if e["action"] == "rule.deleted"]
        assert len(del_entries) >= 1
        assert del_entries[0]["resource_id"] == "del-test"

    @patch("src.tc_manager.remove_rule")
    def test_flush_generates_audit_entry(self, mock_remove, client):
        rules_db["f1"] = FirewallRule(id="f1", tenant_id=DEFAULT_TEST_TENANT)
        rules_db["f2"] = FirewallRule(id="f2", tenant_id=DEFAULT_TEST_TENANT)

        audit._recent_entries.clear()

        response = client.post("/rules/flush")
        assert response.status_code == 200

        entries = audit.get_recent(limit=10)
        flush_entries = [e for e in entries if e["action"] == "rules.flushed"]
        assert len(flush_entries) >= 1
        assert flush_entries[0]["details"]["rules_removed"] == 2

    @patch("src.nat_manager.remove_nat")
    def test_nat_deletion_generates_audit_entry(self, mock_remove, client):
        import time as _time
        nat_entries_db["nat-del-1"] = NATEntry(
            id="nat-del-1",
            type="snat",
            public_ip="1.2.3.4",
            private_ip="10.0.0.1",
            tenant_id=DEFAULT_TEST_TENANT,
            created_at=_time.time(),
        )

        audit._recent_entries.clear()

        response = client.delete("/nat/nat-del-1")
        assert response.status_code == 200

        entries = audit.get_recent(limit=10)
        nat_entries = [e for e in entries if e["action"] == "nat.deleted"]
        assert len(nat_entries) >= 1
        assert nat_entries[0]["resource_id"] == "nat-del-1"

    @patch("src.nat_manager.remove_nat")
    def test_nat_flush_generates_audit_entry(self, mock_remove, client):
        import time as _time
        nat_entries_db["nf1"] = NATEntry(
            id="nf1",
            type="snat",
            public_ip="1.2.3.4",
            private_ip="10.0.0.1",
            tenant_id=DEFAULT_TEST_TENANT,
            created_at=_time.time(),
        )
        nat_entries_db["nf2"] = NATEntry(
            id="nf2",
            type="dnat",
            public_ip="1.2.3.5",
            private_ip="10.0.0.2",
            tenant_id=DEFAULT_TEST_TENANT,
            created_at=_time.time(),
        )

        audit._recent_entries.clear()

        response = client.post("/nat/flush")
        assert response.status_code == 200

        entries = audit.get_recent(limit=10)
        flush_entries = [e for e in entries if e["action"] == "nat.flushed"]
        assert len(flush_entries) >= 1
        assert flush_entries[0]["details"]["rules_removed"] == 2

    def test_daemon_start_logged_on_app_startup(self, raw_client):
        """The lifespan logs daemon.started on startup."""
        entries = audit.get_recent(limit=100)
        start_entries = [e for e in entries if e["action"] == "daemon.started"]
        assert len(start_entries) >= 1
