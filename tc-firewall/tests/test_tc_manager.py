"""Unit tests for tc_manager — mocks subprocess to avoid needing real DPU."""

import subprocess
from unittest.mock import patch, MagicMock
import pytest

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.tc_manager import (
    ensure_ingress_qdisc,
    add_allow_rule,
    add_deny_rule,
    remove_rule,
    flush_rules,
    list_rules,
    get_stats,
    check_in_hw,
    _parse_tc_filter_output,
    _build_flower_cmd,
    TcError,
    INTERNET_PORT,
    CLIENT_PORT,
)


@pytest.fixture
def mock_run():
    """Mock subprocess.run for tc commands."""
    with patch("src.tc_manager.subprocess.run") as mock:
        mock.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )
        yield mock


class TestEnsureIngressQdisc:
    def test_qdisc_already_exists(self, mock_run):
        mock_run.return_value.stdout = "qdisc ingress ffff: parent ffff:fff1"
        ensure_ingress_qdisc("pf0vf0")
        # Should only call show, not add
        assert mock_run.call_count == 1
        assert "show" in mock_run.call_args_list[0][0][0]

    def test_qdisc_not_exists_adds_it(self, mock_run):
        # First call (show) returns empty, second call (add) succeeds
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        ensure_ingress_qdisc("pf0vf0")
        assert mock_run.call_count == 2
        add_call = mock_run.call_args_list[1][0][0]
        assert "add" in add_call
        assert "ingress" in add_call


class TestAddAllowRule:
    def test_basic_allow_rule(self, mock_run):
        # Mock: add fwd succeeds, show returns handle, add rev succeeds, show returns handle
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),  # add fwd
            MagicMock(returncode=0, stdout="filter protocol ip pref 100 flower chain 0 handle 0x1\n", stderr=""),  # show fwd
            MagicMock(returncode=0, stdout="", stderr=""),  # add rev
            MagicMock(returncode=0, stdout="filter protocol ip pref 100 flower chain 0 handle 0x1\n", stderr=""),  # show rev
        ]

        fwd, rev = add_allow_rule(
            src_ip="10.0.0.1",
            dst_ip="10.0.0.2",
            protocol="tcp",
            dst_port=80,
            priority=100,
        )

        assert fwd == "0x1"
        assert rev == "0x1"

        # Verify the forward rule command
        fwd_cmd = mock_run.call_args_list[0][0][0]
        assert "mirred" in fwd_cmd
        assert "redirect" in fwd_cmd
        assert "pf0vf3" in fwd_cmd  # redirect target

    def test_allow_rule_swaps_src_dst_for_reverse(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="handle 0x1\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="handle 0x2\n", stderr=""),
        ]

        add_allow_rule(
            src_ip="10.0.0.1",
            dst_ip="10.0.0.2",
            src_port=12345,
            dst_port=80,
            protocol="tcp",
        )

        # Reverse rule should swap src/dst
        rev_cmd = mock_run.call_args_list[2][0][0]
        src_idx = rev_cmd.index("src_ip")
        dst_idx = rev_cmd.index("dst_ip")
        assert rev_cmd[src_idx + 1] == "10.0.0.2"  # was dst, now src
        assert rev_cmd[dst_idx + 1] == "10.0.0.1"  # was src, now dst


class TestAddDenyRule:
    def test_basic_deny_rule(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="handle 0x1\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="handle 0x1\n", stderr=""),
        ]

        fwd, rev = add_deny_rule(
            src_ip="192.168.1.100",
            dst_ip="10.0.0.5",
            protocol="tcp",
            dst_port=22,
        )

        # Verify drop action
        fwd_cmd = mock_run.call_args_list[0][0][0]
        assert "drop" in fwd_cmd
        assert "mirred" not in fwd_cmd


class TestRemoveRule:
    def test_remove_rule_by_handle(self, mock_run):
        remove_rule("pf0vf0", "0x1", priority=100)

        cmd = mock_run.call_args[0][0]
        assert "del" in cmd
        assert "0x1" in cmd
        assert "100" in cmd

    def test_remove_rule_empty_handle_skips(self, mock_run):
        remove_rule("pf0vf0", "", priority=100)
        mock_run.assert_not_called()


class TestFlushRules:
    def test_flush_rules(self, mock_run):
        flush_rules("pf0vf0")
        cmd = mock_run.call_args[0][0]
        assert "del" in cmd
        assert "pf0vf0" in cmd
        assert "ingress" in cmd


class TestListRules:
    def test_list_empty(self, mock_run):
        mock_run.return_value.stdout = ""
        rules = list_rules("pf0vf0")
        assert rules == []

    def test_list_parses_rules(self, mock_run):
        mock_run.return_value.stdout = """filter protocol ip pref 100 flower chain 0 handle 0x1
  eth_type ipv4
  src_ip 10.0.0.1
  dst_ip 10.0.0.2
  in_hw in_hw_count 1
    action order 1: mirred (Egress Redirect to device pf0vf3) stolen
    index 1 ref 1 bind 1 installed 100 sec used 5 sec
    Action statistics:
    Sent 123456 bytes 1000 pkt (dropped 0, overlimits 0 requeues 0)
"""
        rules = list_rules("pf0vf0")
        assert len(rules) == 1
        assert rules[0].handle == "0x1"
        assert rules[0].priority == 100
        assert rules[0].in_hw is True
        assert rules[0].match_src == "10.0.0.1"
        assert rules[0].match_dst == "10.0.0.2"
        assert rules[0].action == "redirect"
        assert rules[0].bytes == 123456
        assert rules[0].packets == 1000


class TestGetStats:
    def test_get_stats_sums_counters(self, mock_run):
        mock_run.return_value.stdout = """    Sent 1000 bytes 10 pkt
    Sent 2000 bytes 20 pkt
"""
        stats = get_stats("pf0vf0")
        assert stats["packets"] == 30
        assert stats["bytes"] == 3000


class TestCheckInHw:
    def test_in_hw_true(self, mock_run):
        mock_run.return_value.stdout = "  in_hw in_hw_count 1\n"
        assert check_in_hw("pf0vf0", "0x1", 100) is True

    def test_in_hw_false(self, mock_run):
        mock_run.return_value.stdout = "  not_in_hw\n"
        assert check_in_hw("pf0vf0", "0x1", 100) is False


class TestBuildFlowerCmd:
    def test_full_tcp_match(self):
        cmd = _build_flower_cmd(
            dev="pf0vf0",
            src_ip="10.0.0.1",
            dst_ip="10.0.0.2",
            src_port=12345,
            dst_port=80,
            protocol="tcp",
            priority=100,
        )
        assert cmd == [
            "filter", "add", "dev", "pf0vf0", "ingress", "protocol", "ip",
            "prio", "100", "flower",
            "ip_proto", "tcp",
            "src_ip", "10.0.0.1",
            "dst_ip", "10.0.0.2",
            "src_port", "12345",
            "dst_port", "80",
        ]

    def test_ip_only_no_ports(self):
        cmd = _build_flower_cmd(
            dev="pf0vf3",
            src_ip="192.168.1.0/24",
            dst_ip=None,
            src_port=None,
            dst_port=None,
            protocol="any",
            priority=50,
        )
        assert "ip_proto" not in cmd
        assert "src_port" not in cmd
        assert "dst_port" not in cmd
        assert "src_ip" in cmd
        assert "192.168.1.0/24" in cmd

    def test_ports_ignored_for_icmp(self):
        cmd = _build_flower_cmd(
            dev="pf0vf0",
            src_ip=None,
            dst_ip=None,
            src_port=80,
            dst_port=443,
            protocol="icmp",
            priority=100,
        )
        assert "src_port" not in cmd
        assert "dst_port" not in cmd
        assert "ip_proto" in cmd
        assert "icmp" in cmd


class TestRunTcError:
    def test_tc_error_raised_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Error: Cannot find device"
        )
        with pytest.raises(TcError, match="Cannot find device"):
            from src.tc_manager import _run_tc
            _run_tc(["filter", "show", "dev", "pf0vf0", "ingress"], check=True)


class TestGetLastHandle:
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="error"
        )
        from src.tc_manager import _get_last_handle
        assert _get_last_handle("pf0vf0", 100) == ""

    def test_returns_empty_on_no_handles(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="no handles here\n", stderr=""
        )
        from src.tc_manager import _get_last_handle
        assert _get_last_handle("pf0vf0", 100) == ""


class TestGetStatsError:
    def test_get_stats_returns_zero_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="error"
        )
        stats = get_stats("pf0vf0")
        assert stats == {"packets": 0, "bytes": 0}


class TestCheckInHwFailure:
    def test_check_in_hw_returns_false_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="error"
        )
        assert check_in_hw("pf0vf0", "0x1", 100) is False


class TestParseOutput:
    def test_multiple_rules(self):
        output = """filter protocol ip pref 100 flower chain 0 handle 0x1
  src_ip 10.0.0.1
  in_hw in_hw_count 1
    action order 1: mirred (Egress Redirect to device pf0vf3) stolen
    Sent 500 bytes 5 pkt
filter protocol ip pref 200 flower chain 0 handle 0x2
  dst_ip 10.0.0.99
    action order 1:  drop
    Sent 100 bytes 2 pkt
"""
        rules = _parse_tc_filter_output(output)
        assert len(rules) == 2
        assert rules[0].handle == "0x1"
        assert rules[0].in_hw is True
        assert rules[0].action == "redirect"
        assert rules[1].handle == "0x2"
        assert rules[1].action == "drop"
        assert rules[1].in_hw is False

    def test_rule_with_ports(self):
        output = """filter protocol ip pref 100 flower chain 0 handle 0x1
  src_ip 10.0.0.1
  dst_ip 10.0.0.2
  src_port 12345
  dst_port 80
  ip_proto tcp
  in_hw in_hw_count 1
    action order 1: mirred (Egress Redirect to device pf0vf3) stolen
    Sent 500 bytes 5 pkt
"""
        rules = _parse_tc_filter_output(output)
        assert len(rules) == 1
        assert rules[0].match_sport == 12345
        assert rules[0].match_dport == 80

    def test_not_in_hw_line(self):
        output = """filter protocol ip pref 100 flower chain 0 handle 0x1
  src_ip 10.0.0.1
  not_in_hw
    action order 1:  drop
    Sent 0 bytes 0 pkt
"""
        rules = _parse_tc_filter_output(output)
        assert len(rules) == 1
        assert rules[0].in_hw is False

    def test_empty_output(self):
        rules = _parse_tc_filter_output("")
        assert rules == []

    def test_no_filter_lines(self):
        output = "some random text\nno filters here\n"
        rules = _parse_tc_filter_output(output)
        assert rules == []

    def test_drop_action_not_confused_with_dropped(self):
        """'dropped' in stats line should not be mistaken for drop action."""
        output = """filter protocol ip pref 100 flower chain 0 handle 0x1
  src_ip 10.0.0.1
    action order 1: mirred (Egress Redirect to device pf0vf3) stolen
    Sent 500 bytes 5 pkt (dropped 0, overlimits 0 requeues 0)
"""
        rules = _parse_tc_filter_output(output)
        assert len(rules) == 1
        assert rules[0].action == "redirect"
