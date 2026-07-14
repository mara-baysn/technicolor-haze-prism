"""Unit tests for Pydantic models — validation, defaults, and edge cases."""

import time
from unittest.mock import patch

import pytest
from pydantic import ValidationError

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.models import (
    DefaultPolicy,
    FirewallRule,
    FirewallRuleRequest,
    Metrics,
    Protocol,
    RuleAction,
    Session,
)


class TestProtocolEnum:
    def test_all_values(self):
        assert Protocol.TCP.value == "tcp"
        assert Protocol.UDP.value == "udp"
        assert Protocol.ICMP.value == "icmp"
        assert Protocol.ANY.value == "any"

    def test_is_str_enum(self):
        assert isinstance(Protocol.TCP, str)
        assert Protocol.TCP == "tcp"


class TestRuleActionEnum:
    def test_all_values(self):
        assert RuleAction.ALLOW.value == "ALLOW"
        assert RuleAction.DENY.value == "DENY"

    def test_is_str_enum(self):
        assert isinstance(RuleAction.ALLOW, str)
        assert RuleAction.ALLOW == "ALLOW"


class TestDefaultPolicyEnum:
    def test_all_values(self):
        assert DefaultPolicy.ALLOW.value == "allow-all"
        assert DefaultPolicy.DENY.value == "deny-all"

    def test_is_str_enum(self):
        assert isinstance(DefaultPolicy.DENY, str)


class TestFirewallRuleRequest:
    def test_minimal_request_defaults(self):
        req = FirewallRuleRequest()
        assert req.src_ip is None
        assert req.dst_ip is None
        assert req.src_port is None
        assert req.dst_port is None
        assert req.protocol == Protocol.ANY
        assert req.action == RuleAction.ALLOW
        assert req.priority == 100
        assert req.comment is None

    def test_full_request(self):
        req = FirewallRuleRequest(
            src_ip="10.0.0.1",
            dst_ip="10.0.0.2",
            src_port=12345,
            dst_port=80,
            protocol=Protocol.TCP,
            action=RuleAction.DENY,
            priority=50,
            comment="block SSH",
        )
        assert req.src_ip == "10.0.0.1"
        assert req.dst_ip == "10.0.0.2"
        assert req.src_port == 12345
        assert req.dst_port == 80
        assert req.protocol == Protocol.TCP
        assert req.action == RuleAction.DENY
        assert req.priority == 50
        assert req.comment == "block SSH"

    def test_port_min_boundary(self):
        req = FirewallRuleRequest(src_port=1, dst_port=1)
        assert req.src_port == 1
        assert req.dst_port == 1

    def test_port_max_boundary(self):
        req = FirewallRuleRequest(src_port=65535, dst_port=65535)
        assert req.src_port == 65535
        assert req.dst_port == 65535

    def test_port_below_min_fails(self):
        with pytest.raises(ValidationError) as exc_info:
            FirewallRuleRequest(src_port=0)
        assert "src_port" in str(exc_info.value)

    def test_port_above_max_fails(self):
        with pytest.raises(ValidationError) as exc_info:
            FirewallRuleRequest(dst_port=65536)
        assert "dst_port" in str(exc_info.value)

    def test_priority_min_boundary(self):
        req = FirewallRuleRequest(priority=1)
        assert req.priority == 1

    def test_priority_max_boundary(self):
        req = FirewallRuleRequest(priority=65535)
        assert req.priority == 65535

    def test_priority_below_min_fails(self):
        with pytest.raises(ValidationError):
            FirewallRuleRequest(priority=0)

    def test_priority_above_max_fails(self):
        with pytest.raises(ValidationError):
            FirewallRuleRequest(priority=65536)

    def test_invalid_protocol_fails(self):
        with pytest.raises(ValidationError):
            FirewallRuleRequest(protocol="ftp")

    def test_invalid_action_fails(self):
        with pytest.raises(ValidationError):
            FirewallRuleRequest(action="REJECT")

    def test_negative_port_fails(self):
        with pytest.raises(ValidationError):
            FirewallRuleRequest(src_port=-1)


class TestFirewallRule:
    def test_auto_generated_id(self):
        rule = FirewallRule()
        assert rule.id is not None
        assert len(rule.id) == 8

    def test_unique_ids(self):
        rule1 = FirewallRule()
        rule2 = FirewallRule()
        assert rule1.id != rule2.id

    def test_defaults(self):
        rule = FirewallRule()
        assert rule.src_ip is None
        assert rule.dst_ip is None
        assert rule.src_port is None
        assert rule.dst_port is None
        assert rule.protocol == Protocol.ANY
        assert rule.action == RuleAction.ALLOW
        assert rule.priority == 100
        assert rule.comment is None
        assert rule.tc_handle_fwd is None
        assert rule.tc_handle_rev is None
        assert rule.in_hw is False
        assert rule.created_at > 0
        assert rule.packets_fwd == 0
        assert rule.bytes_fwd == 0
        assert rule.packets_rev == 0
        assert rule.bytes_rev == 0

    def test_created_at_auto_set(self):
        before = time.time()
        rule = FirewallRule()
        after = time.time()
        assert before <= rule.created_at <= after

    def test_full_rule(self):
        rule = FirewallRule(
            src_ip="10.0.0.1",
            dst_ip="10.0.0.2",
            src_port=443,
            dst_port=8080,
            protocol=Protocol.TCP,
            action=RuleAction.DENY,
            priority=200,
            comment="test",
            tc_handle_fwd="0x1",
            tc_handle_rev="0x2",
            in_hw=True,
            packets_fwd=100,
            bytes_fwd=5000,
            packets_rev=50,
            bytes_rev=2500,
        )
        assert rule.src_ip == "10.0.0.1"
        assert rule.tc_handle_fwd == "0x1"
        assert rule.in_hw is True
        assert rule.packets_fwd == 100

    def test_serialization(self):
        rule = FirewallRule(src_ip="10.0.0.1", action=RuleAction.ALLOW)
        data = rule.model_dump()
        assert data["src_ip"] == "10.0.0.1"
        assert data["action"] == "ALLOW"
        assert "id" in data
        assert "created_at" in data


class TestMetrics:
    def test_defaults(self):
        m = Metrics()
        assert m.total_rules == 0
        assert m.hw_offloaded_rules == 0
        assert m.packets_forwarded == 0
        assert m.packets_dropped == 0
        assert m.bytes_forwarded == 0
        assert m.bytes_dropped == 0
        assert m.uptime_seconds == 0.0
        assert m.default_policy == "deny-all"

    def test_full_metrics(self):
        m = Metrics(
            total_rules=5,
            hw_offloaded_rules=3,
            packets_forwarded=10000,
            packets_dropped=500,
            bytes_forwarded=1000000,
            bytes_dropped=50000,
            uptime_seconds=3600.5,
            default_policy="allow-all",
        )
        assert m.total_rules == 5
        assert m.hw_offloaded_rules == 3
        assert m.packets_forwarded == 10000
        assert m.uptime_seconds == 3600.5
        assert m.default_policy == "allow-all"

    def test_serialization(self):
        m = Metrics(total_rules=2)
        data = m.model_dump()
        assert data["total_rules"] == 2


class TestSession:
    def test_minimal_session(self):
        s = Session(protocol="tcp", src_ip="10.0.0.1", dst_ip="10.0.0.2")
        assert s.protocol == "tcp"
        assert s.src_ip == "10.0.0.1"
        assert s.dst_ip == "10.0.0.2"
        assert s.src_port is None
        assert s.dst_port is None
        assert s.state is None
        assert s.packets == 0
        assert s.bytes == 0
        assert s.timeout is None

    def test_full_session(self):
        s = Session(
            protocol="tcp",
            src_ip="10.0.0.1",
            dst_ip="10.0.0.2",
            src_port=54321,
            dst_port=80,
            state="ESTABLISHED",
            packets=100,
            bytes=5000,
            timeout=431999,
        )
        assert s.src_port == 54321
        assert s.dst_port == 80
        assert s.state == "ESTABLISHED"
        assert s.packets == 100
        assert s.bytes == 5000
        assert s.timeout == 431999

    def test_requires_protocol(self):
        with pytest.raises(ValidationError):
            Session(src_ip="10.0.0.1", dst_ip="10.0.0.2")

    def test_requires_src_ip(self):
        with pytest.raises(ValidationError):
            Session(protocol="tcp", dst_ip="10.0.0.2")

    def test_requires_dst_ip(self):
        with pytest.raises(ValidationError):
            Session(protocol="tcp", src_ip="10.0.0.1")
