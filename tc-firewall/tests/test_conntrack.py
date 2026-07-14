"""Unit tests for conntrack module — parsing and error handling."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.conntrack import get_sessions, _parse_conntrack_output, _parse_conntrack_line
from src.models import Session


class TestGetSessions:
    @patch("src.conntrack.subprocess.run")
    def test_successful_query(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="tcp  6 431999 ESTABLISHED src=10.0.0.1 dst=10.0.0.2 sport=54321 dport=80 packets=10 bytes=1000\n",
            stderr="",
        )
        sessions = get_sessions()
        assert len(sessions) == 1
        assert sessions[0].protocol == "tcp"
        assert sessions[0].src_ip == "10.0.0.1"
        assert sessions[0].state == "ESTABLISHED"

    @patch("src.conntrack.subprocess.run")
    def test_failed_returncode(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="permission denied",
        )
        sessions = get_sessions()
        assert sessions == []

    @patch("src.conntrack.subprocess.run")
    def test_conntrack_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError("No such file or directory")
        sessions = get_sessions()
        assert sessions == []

    @patch("src.conntrack.subprocess.run")
    def test_timeout_expired(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="conntrack", timeout=5)
        sessions = get_sessions()
        assert sessions == []

    @patch("src.conntrack.subprocess.run")
    def test_empty_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )
        sessions = get_sessions()
        assert sessions == []

    @patch("src.conntrack.subprocess.run")
    def test_multiple_sessions(self, mock_run):
        output = (
            "tcp  6 431999 ESTABLISHED src=10.0.0.1 dst=10.0.0.2 sport=54321 dport=80 packets=10 bytes=1000\n"
            "udp  17 29 src=10.0.0.3 dst=10.0.0.4 sport=12345 dport=53 packets=2 bytes=200\n"
        )
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=output,
            stderr="",
        )
        sessions = get_sessions()
        assert len(sessions) == 2
        assert sessions[0].protocol == "tcp"
        assert sessions[1].protocol == "udp"


class TestParseConntrackOutput:
    def test_tcp_established(self):
        output = "tcp  6 431999 ESTABLISHED src=10.0.0.1 dst=10.0.0.2 sport=54321 dport=80 packets=10 bytes=1000\n"
        sessions = _parse_conntrack_output(output)
        assert len(sessions) == 1
        s = sessions[0]
        assert s.protocol == "tcp"
        assert s.src_ip == "10.0.0.1"
        assert s.dst_ip == "10.0.0.2"
        assert s.src_port == 54321
        assert s.dst_port == 80
        assert s.state == "ESTABLISHED"
        assert s.packets == 10
        assert s.bytes == 1000
        assert s.timeout == 431999

    def test_udp_session(self):
        output = "udp  17 29 src=10.0.0.3 dst=10.0.0.4 sport=12345 dport=53 packets=2 bytes=200\n"
        sessions = _parse_conntrack_output(output)
        assert len(sessions) == 1
        s = sessions[0]
        assert s.protocol == "udp"
        assert s.src_ip == "10.0.0.3"
        assert s.dst_ip == "10.0.0.4"
        assert s.src_port == 12345
        assert s.dst_port == 53
        assert s.state is None  # UDP has no state
        assert s.packets == 2
        assert s.bytes == 200
        assert s.timeout == 29

    def test_multiple_lines(self):
        output = (
            "tcp  6 100 SYN_SENT src=1.1.1.1 dst=2.2.2.2 sport=111 dport=222 packets=1 bytes=60\n"
            "tcp  6 200 ESTABLISHED src=3.3.3.3 dst=4.4.4.4 sport=333 dport=444 packets=50 bytes=5000\n"
            "udp  17 30 src=5.5.5.5 dst=6.6.6.6 sport=555 dport=666 packets=3 bytes=300\n"
        )
        sessions = _parse_conntrack_output(output)
        assert len(sessions) == 3
        assert sessions[0].state == "SYN_SENT"
        assert sessions[1].state == "ESTABLISHED"
        assert sessions[2].state is None

    def test_empty_string(self):
        sessions = _parse_conntrack_output("")
        assert sessions == []

    def test_blank_lines_skipped(self):
        output = "\n\ntcp  6 100 ESTABLISHED src=1.1.1.1 dst=2.2.2.2 sport=111 dport=80 packets=1 bytes=60\n\n"
        sessions = _parse_conntrack_output(output)
        assert len(sessions) == 1


class TestParseConntrackLine:
    def test_tcp_established_line(self):
        line = "tcp  6 431999 ESTABLISHED src=10.0.0.1 dst=10.0.0.2 sport=54321 dport=80 packets=10 bytes=1000"
        s = _parse_conntrack_line(line)
        assert s is not None
        assert s.protocol == "tcp"
        assert s.src_ip == "10.0.0.1"
        assert s.dst_ip == "10.0.0.2"
        assert s.src_port == 54321
        assert s.dst_port == 80
        assert s.state == "ESTABLISHED"
        assert s.packets == 10
        assert s.bytes == 1000
        assert s.timeout == 431999

    def test_tcp_syn_sent(self):
        line = "tcp  6 120 SYN_SENT src=10.0.0.1 dst=10.0.0.2 sport=12345 dport=443 packets=1 bytes=60"
        s = _parse_conntrack_line(line)
        assert s is not None
        assert s.state == "SYN_SENT"

    def test_tcp_syn_recv(self):
        line = "tcp  6 60 SYN_RECV src=10.0.0.1 dst=10.0.0.2 sport=12345 dport=80 packets=2 bytes=120"
        s = _parse_conntrack_line(line)
        assert s is not None
        assert s.state == "SYN_RECV"

    def test_tcp_fin_wait(self):
        line = "tcp  6 120 FIN_WAIT src=10.0.0.1 dst=10.0.0.2 sport=12345 dport=80 packets=5 bytes=300"
        s = _parse_conntrack_line(line)
        assert s is not None
        assert s.state == "FIN_WAIT"

    def test_tcp_close_wait(self):
        line = "tcp  6 60 CLOSE_WAIT src=10.0.0.1 dst=10.0.0.2 sport=12345 dport=80 packets=5 bytes=300"
        s = _parse_conntrack_line(line)
        assert s is not None
        assert s.state == "CLOSE_WAIT"

    def test_tcp_last_ack(self):
        line = "tcp  6 30 LAST_ACK src=10.0.0.1 dst=10.0.0.2 sport=12345 dport=80 packets=5 bytes=300"
        s = _parse_conntrack_line(line)
        assert s is not None
        assert s.state == "LAST_ACK"

    def test_tcp_time_wait(self):
        line = "tcp  6 120 TIME_WAIT src=10.0.0.1 dst=10.0.0.2 sport=12345 dport=80 packets=5 bytes=300"
        s = _parse_conntrack_line(line)
        assert s is not None
        assert s.state == "TIME_WAIT"

    def test_tcp_close(self):
        line = "tcp  6 10 CLOSE src=10.0.0.1 dst=10.0.0.2 sport=12345 dport=80 packets=5 bytes=300"
        s = _parse_conntrack_line(line)
        assert s is not None
        assert s.state == "CLOSE"

    def test_udp_no_state(self):
        line = "udp  17 29 src=10.0.0.1 dst=10.0.0.2 sport=12345 dport=53 packets=2 bytes=200"
        s = _parse_conntrack_line(line)
        assert s is not None
        assert s.protocol == "udp"
        assert s.state is None

    def test_no_ports(self):
        line = "icmp  1 30 src=10.0.0.1 dst=10.0.0.2 packets=1 bytes=84"
        s = _parse_conntrack_line(line)
        assert s is not None
        assert s.protocol == "icmp"
        assert s.src_port is None
        assert s.dst_port is None

    def test_no_packets_or_bytes(self):
        line = "tcp  6 100 ESTABLISHED src=10.0.0.1 dst=10.0.0.2 sport=111 dport=80"
        s = _parse_conntrack_line(line)
        assert s is not None
        assert s.packets == 0
        assert s.bytes == 0

    def test_too_short_line_returns_none(self):
        line = "tcp  6"
        s = _parse_conntrack_line(line)
        assert s is None

    def test_empty_line_returns_none(self):
        line = ""
        s = _parse_conntrack_line(line)
        assert s is None

    def test_no_src_dst_returns_none(self):
        line = "tcp  6 431999 ESTABLISHED sport=12345 dport=80 packets=10 bytes=1000"
        s = _parse_conntrack_line(line)
        assert s is None

    def test_timeout_non_numeric(self):
        # Third field is not numeric — timeout should be None
        line = "tcp  6 ESTABLISHED src=10.0.0.1 dst=10.0.0.2 sport=111 dport=80 packets=1 bytes=60"
        s = _parse_conntrack_line(line)
        assert s is not None
        assert s.timeout is None

    def test_line_with_extra_reply_direction(self):
        # Real conntrack output often has bidirectional info
        line = "tcp  6 431999 ESTABLISHED src=10.0.0.1 dst=10.0.0.2 sport=54321 dport=80 packets=10 bytes=1000 src=10.0.0.2 dst=10.0.0.1 sport=80 dport=54321 packets=8 bytes=800"
        s = _parse_conntrack_line(line)
        assert s is not None
        # Should pick the first src/dst
        assert s.src_ip == "10.0.0.1"
        assert s.dst_ip == "10.0.0.2"

    @patch("src.conntrack.re.search")
    def test_parse_line_exception_returns_none(self, mock_search):
        """If an unexpected exception occurs, returns None."""
        mock_search.side_effect = RuntimeError("unexpected error")
        line = "tcp  6 431999 ESTABLISHED src=10.0.0.1 dst=10.0.0.2 sport=54321 dport=80 packets=10 bytes=1000"
        s = _parse_conntrack_line(line)
        assert s is None
