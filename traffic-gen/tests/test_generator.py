"""Unit tests for the traffic generator packet crafting and control logic."""

from __future__ import annotations

import struct
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.generator import (
    FlowSpec,
    Profile,
    PROFILE_FLOWS,
    Stats,
    TrafficGenerator,
    build_payload,
    create_socket,
)


class TestBuildPayload:
    """Tests for payload construction."""

    def test_payload_has_correct_size(self):
        flow = FlowSpec(dst_ip="10.0.2.1", dst_port=80, protocol="tcp", payload_size=128)
        payload = build_payload(flow, seq=0)
        assert len(payload) == 128

    def test_payload_contains_sequence_number(self):
        flow = FlowSpec(dst_ip="10.0.2.1", dst_port=443, protocol="tcp", payload_size=64)
        payload = build_payload(flow, seq=42)
        seq_num = struct.unpack("!I", payload[:4])[0]
        assert seq_num == 42

    def test_payload_sequence_wraps_at_32bit(self):
        flow = FlowSpec(dst_ip="10.0.2.1", dst_port=80, protocol="tcp", payload_size=8)
        payload = build_payload(flow, seq=2**32 + 7)
        seq_num = struct.unpack("!I", payload[:4])[0]
        assert seq_num == 7

    def test_small_payload_at_least_4_bytes(self):
        flow = FlowSpec(dst_ip="10.0.2.1", dst_port=80, protocol="tcp", payload_size=2)
        payload = build_payload(flow, seq=1)
        # Minimum is 4 bytes (the header), even if payload_size < 4
        assert len(payload) == 4


class TestStats:
    """Tests for the Stats counter."""

    def test_initial_state(self):
        stats = Stats()
        assert stats.packets_sent == 0
        assert stats.bytes_sent == 0
        assert stats.active_flows == 0

    def test_record_packet(self):
        stats = Stats()
        stats.start_time = time.time()
        stats.record_packet(128)
        stats.record_packet(256)
        assert stats.packets_sent == 2
        assert stats.bytes_sent == 384

    def test_record_error(self):
        stats = Stats()
        stats.record_error()
        stats.record_error()
        assert stats.errors == 2

    def test_reset_clears_counters(self):
        stats = Stats()
        stats.record_packet(100)
        stats.record_error()
        stats.reset()
        assert stats.packets_sent == 0
        assert stats.bytes_sent == 0
        assert stats.errors == 0
        assert stats.start_time > 0

    def test_snapshot_returns_dict(self):
        stats = Stats()
        stats.reset()
        stats.record_packet(64)
        snap = stats.snapshot()
        assert snap["packets_sent"] == 1
        assert snap["bytes_sent"] == 64
        assert "current_pps" in snap
        assert "elapsed_s" in snap

    def test_thread_safety(self):
        stats = Stats()
        stats.start_time = time.time()

        def hammer():
            for _ in range(1000):
                stats.record_packet(1)

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert stats.packets_sent == 4000


class TestProfileDefinitions:
    """Tests for profile flow configurations."""

    def test_all_profiles_have_flows(self):
        for profile in Profile:
            assert len(PROFILE_FLOWS[profile]) > 0

    def test_http_profile_targets_port_80(self):
        flows = PROFILE_FLOWS[Profile.HTTP]
        assert all(f.dst_port == 80 for f in flows)

    def test_https_profile_targets_port_443(self):
        flows = PROFILE_FLOWS[Profile.HTTPS]
        assert all(f.dst_port == 443 for f in flows)

    def test_mixed_profile_has_multiple_protocols(self):
        flows = PROFILE_FLOWS[Profile.MIXED]
        protocols = {f.protocol for f in flows}
        assert "tcp" in protocols
        assert "udp" in protocols

    def test_storm_profile_has_many_flows(self):
        flows = PROFILE_FLOWS[Profile.STORM]
        assert len(flows) == 100


class TestTrafficGenerator:
    """Tests for the TrafficGenerator orchestrator."""

    def test_initial_state(self):
        gen = TrafficGenerator()
        assert not gen.running
        assert gen.rate_pps == 1000
        assert gen.profile == Profile.MIXED

    def test_rate_clamping(self):
        gen = TrafficGenerator()
        gen.rate_pps = 50
        assert gen.rate_pps == 100  # minimum
        gen.rate_pps = 100000
        assert gen.rate_pps == 50000  # maximum

    def test_profile_setting_from_string(self):
        gen = TrafficGenerator()
        gen.profile = "http"
        assert gen.profile == Profile.HTTP
        gen.profile = "HTTPS"
        assert gen.profile == Profile.HTTPS

    @patch("src.generator.TrafficGenerator._send_tcp")
    @patch("src.generator.TrafficGenerator._send_udp")
    def test_start_stop(self, mock_udp, mock_tcp):
        gen = TrafficGenerator()
        gen.profile = Profile.HTTP
        gen.rate_pps = 100

        gen.start()
        assert gen.running
        assert gen.stats.active_flows > 0

        time.sleep(0.1)
        gen.stop()
        assert not gen.running
        assert gen.stats.active_flows == 0
