"""Unit tests for prism_monitor.py — API parsing, throughput, error handling, CLI, panels."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest
import requests

# Ensure the parent package is importable
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from prism_monitor import (
    build_firewall,
    build_header,
    build_receiver,
    build_traffic_source,
    fetch_json,
    format_bps,
    format_bytes,
    make_bar,
)


# ─── format_bytes ────────────────────────────────────────────────────────────


class TestFormatBytes:
    def test_bytes(self):
        assert format_bytes(0) == "0.0 B"
        assert format_bytes(512) == "512.0 B"

    def test_kilobytes(self):
        assert format_bytes(1024) == "1.0 KB"
        assert format_bytes(1536) == "1.5 KB"

    def test_megabytes(self):
        assert format_bytes(1024 ** 2) == "1.0 MB"

    def test_gigabytes(self):
        assert format_bytes(1024 ** 3) == "1.0 GB"

    def test_terabytes(self):
        assert format_bytes(1024 ** 4) == "1.0 TB"

    def test_petabytes(self):
        assert format_bytes(1024 ** 5) == "1.0 PB"


# ─── format_bps ──────────────────────────────────────────────────────────────


class TestFormatBps:
    def test_bps(self):
        assert format_bps(500) == "500 bps"

    def test_kbps(self):
        assert format_bps(1_500) == "1.5 Kbps"

    def test_mbps(self):
        assert format_bps(10_000_000) == "10.0 Mbps"

    def test_gbps(self):
        assert format_bps(2_500_000_000) == "2.50 Gbps"

    def test_zero(self):
        assert format_bps(0) == "0 bps"


# ─── make_bar ────────────────────────────────────────────────────────────────


class TestMakeBar:
    def test_full(self):
        bar = make_bar(100, 100)
        assert bar == "█" * 30

    def test_empty(self):
        bar = make_bar(0, 100)
        assert bar == "░" * 30

    def test_half(self):
        bar = make_bar(50, 100)
        assert "█" in bar and "░" in bar

    def test_total_zero(self):
        bar = make_bar(0, 0)
        assert bar == "░" * 30

    def test_exceeds_total_clamps(self):
        bar = make_bar(200, 100)
        assert bar == "█" * 30


# ─── fetch_json ──────────────────────────────────────────────────────────────


class TestFetchJson:
    @patch("prism_monitor.requests.get")
    def test_successful_fetch(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"key": "value"}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        result = fetch_json("http://localhost:5001/api/stats")
        assert result == {"key": "value"}
        mock_get.assert_called_once_with(
            "http://localhost:5001/api/stats", timeout=0.4, verify=False
        )

    @patch("prism_monitor.requests.get")
    def test_connection_error_returns_none(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("refused")
        result = fetch_json("http://unreachable:9999/api/stats")
        assert result is None

    @patch("prism_monitor.requests.get")
    def test_timeout_returns_none(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout("timed out")
        result = fetch_json("http://slow-host:5001/metrics")
        assert result is None

    @patch("prism_monitor.requests.get")
    def test_http_error_returns_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
        mock_get.return_value = mock_resp
        result = fetch_json("http://localhost:5001/api/stats")
        assert result is None

    @patch("prism_monitor.requests.get")
    def test_invalid_json_returns_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = ValueError("No JSON")
        mock_get.return_value = mock_resp
        result = fetch_json("http://localhost:5001/api/stats")
        assert result is None

    @patch("prism_monitor.requests.get")
    def test_custom_timeout(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        fetch_json("http://localhost:5001/api/stats", timeout=2.0)
        mock_get.assert_called_once_with(
            "http://localhost:5001/api/stats", timeout=2.0, verify=False
        )


# ─── Throughput calculation ──────────────────────────────────────────────────


class TestThroughputCalculation:
    """Test the throughput logic extracted from main loop."""

    def _compute_throughput(
        self, prev_bytes: int, prev_time: float, current_bytes: int, now: float
    ) -> float:
        """Replicate the throughput calculation from main()."""
        dt = now - prev_time
        if dt > 0:
            byte_delta = current_bytes - prev_bytes
            if byte_delta >= 0:
                return (byte_delta * 8) / dt
        return 0.0

    def test_basic_throughput(self):
        # 1000 bytes over 1 second = 8000 bps
        bps = self._compute_throughput(0, 0.0, 1000, 1.0)
        assert bps == 8000.0

    def test_zero_delta_time(self):
        bps = self._compute_throughput(100, 1.0, 200, 1.0)
        assert bps == 0.0

    def test_negative_byte_delta_ignored(self):
        # Counter reset — byte_delta < 0 should not produce negative throughput
        bps = self._compute_throughput(500, 1.0, 100, 2.0)
        assert bps == 0.0

    def test_large_throughput(self):
        # 125 MB over 1 second = 1 Gbps
        bps = self._compute_throughput(0, 0.0, 125_000_000, 1.0)
        assert bps == 1_000_000_000.0

    def test_half_second_interval(self):
        # 500 bytes over 0.5s = 8000 bps
        bps = self._compute_throughput(0, 0.0, 500, 0.5)
        assert bps == 8000.0


# ─── CLI argument parsing ────────────────────────────────────────────────────


class TestCLIArgs:
    def test_defaults(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--gen-url", default="http://localhost:5001")
        parser.add_argument("--recv-url", default="http://localhost:5002")
        parser.add_argument("--fw-url", default="http://192.168.0.38:8443")
        parser.add_argument("--interval", type=float, default=0.5)
        args = parser.parse_args([])

        assert args.gen_url == "http://localhost:5001"
        assert args.recv_url == "http://localhost:5002"
        assert args.fw_url == "http://192.168.0.38:8443"
        assert args.interval == 0.5

    def test_custom_args(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--gen-url", default="http://localhost:5001")
        parser.add_argument("--recv-url", default="http://localhost:5002")
        parser.add_argument("--fw-url", default="http://192.168.0.38:8443")
        parser.add_argument("--interval", type=float, default=0.5)
        args = parser.parse_args([
            "--gen-url", "http://10.0.0.1:6000",
            "--recv-url", "http://10.0.0.2:7000",
            "--fw-url", "https://dpu.local:8443",
            "--interval", "2.0",
        ])

        assert args.gen_url == "http://10.0.0.1:6000"
        assert args.recv_url == "http://10.0.0.2:7000"
        assert args.fw_url == "https://dpu.local:8443"
        assert args.interval == 2.0


# ─── Panel construction — no crash with None data ────────────────────────────


class TestPanelConstruction:
    """Verify panels render without exceptions, including with None inputs."""

    def test_build_header(self):
        panel = build_header(42.5)
        assert panel is not None
        assert hasattr(panel, "renderable")

    def test_build_traffic_source_none(self):
        panel = build_traffic_source(None)
        assert panel is not None

    def test_build_traffic_source_valid(self):
        data = {
            "aggregate": {
                "total_attempted": 1000,
                "total_succeeded": 800,
                "total_failed": 200,
                "connections_per_sec": 15.3,
            },
            "per_port": [
                {"port": 80, "attempted": 500, "succeeded": 450, "failed": 50},
                {"port": 443, "attempted": 500, "succeeded": 350, "failed": 150},
            ],
        }
        panel = build_traffic_source(data)
        assert panel is not None

    def test_build_traffic_source_empty_aggregate(self):
        data = {"aggregate": {}, "per_port": []}
        panel = build_traffic_source(data)
        assert panel is not None

    def test_build_firewall_none_both(self):
        panel = build_firewall(None, None, 0.0)
        assert panel is not None

    def test_build_firewall_metrics_only(self):
        metrics = {
            "packets_forwarded": 5000,
            "packets_dropped": 120,
            "bytes_forwarded": 1_000_000,
            "bytes_dropped": 50_000,
            "default_policy": "DROP",
        }
        panel = build_firewall(metrics, None, 8_000_000.0)
        assert panel is not None

    def test_build_firewall_with_rules(self):
        metrics = {"packets_forwarded": 100, "packets_dropped": 5}
        rules = [
            {"action": "ALLOW", "dst_port": 443, "protocol": "tcp",
             "in_hw": True, "packets_fwd": 80, "packets_rev": 70},
            {"action": "DENY", "dst_port": 22, "protocol": "tcp",
             "in_hw": False, "packets_fwd": 5, "packets_rev": 0},
        ]
        panel = build_firewall(metrics, rules, 1_500_000.0)
        assert panel is not None

    def test_build_firewall_wildcard_port_rule(self):
        metrics = {"packets_forwarded": 10, "packets_dropped": 0}
        rules = [
            {"action": "DROP", "dst_port": None, "protocol": "tcp",
             "in_hw": False, "packets_fwd": 0, "packets_rev": 0},
        ]
        panel = build_firewall(metrics, rules, 0.0)
        assert panel is not None

    def test_build_receiver_none(self):
        panel = build_receiver(None)
        assert panel is not None

    def test_build_receiver_valid(self):
        data = {
            "bind_ip": "10.0.2.1",
            "ports": [
                {"port": 80, "connections": 450, "bytes_received": 2_000_000},
                {"port": 443, "connections": 350, "bytes_received": 5_000_000},
                {"port": 22, "connections": 0, "bytes_received": 0},
                {"port": 5432, "connections": 100, "bytes_received": 800_000},
            ],
        }
        panel = build_receiver(data)
        assert panel is not None

    def test_build_receiver_empty_ports(self):
        data = {"ports": []}
        panel = build_receiver(data)
        assert panel is not None

    def test_build_receiver_unknown_port_label(self):
        data = {"ports": [{"port": 9999, "connections": 7, "bytes_received": 100}]}
        panel = build_receiver(data)
        assert panel is not None
