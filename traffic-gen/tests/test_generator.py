"""Unit tests for the traffic generator core logic."""

from __future__ import annotations

import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from src.generator import (
    PAYLOAD,
    PROFILE_PORTS,
    Profile,
    PortStats,
    Stats,
    TargetPort,
    TrafficGenerator,
    get_generator,
)


class TestProfile:
    """Tests for the Profile enum."""

    def test_http_value(self):
        assert Profile.HTTP.value == "http"

    def test_https_value(self):
        assert Profile.HTTPS.value == "https"

    def test_mixed_value(self):
        assert Profile.MIXED.value == "mixed"

    def test_all_ports_value(self):
        assert Profile.ALL_PORTS.value == "all_ports"

    def test_profile_from_string(self):
        assert Profile("http") == Profile.HTTP
        assert Profile("https") == Profile.HTTPS
        assert Profile("mixed") == Profile.MIXED
        assert Profile("all_ports") == Profile.ALL_PORTS

    def test_invalid_profile_raises(self):
        with pytest.raises(ValueError):
            Profile("invalid")


class TestTargetPort:
    """Tests for the TargetPort dataclass."""

    def test_default_protocol_is_tcp(self):
        tp = TargetPort(port=80)
        assert tp.protocol == "tcp"

    def test_custom_protocol(self):
        tp = TargetPort(port=53, protocol="udp")
        assert tp.protocol == "udp"

    def test_port_stored(self):
        tp = TargetPort(port=443)
        assert tp.port == 443


class TestPortStats:
    """Tests for per-port statistics."""

    def test_initial_state(self):
        ps = PortStats(port=80)
        assert ps.port == 80
        assert ps.attempted == 0
        assert ps.succeeded == 0
        assert ps.failed == 0
        assert ps.bytes_sent == 0

    def test_record_success(self):
        ps = PortStats(port=80)
        ps.record_success(64)
        assert ps.attempted == 1
        assert ps.succeeded == 1
        assert ps.failed == 0
        assert ps.bytes_sent == 64

    def test_record_multiple_successes(self):
        ps = PortStats(port=443)
        ps.record_success(64)
        ps.record_success(128)
        assert ps.attempted == 2
        assert ps.succeeded == 2
        assert ps.bytes_sent == 192

    def test_record_failure(self):
        ps = PortStats(port=22)
        ps.record_failure()
        assert ps.attempted == 1
        assert ps.succeeded == 0
        assert ps.failed == 1
        assert ps.bytes_sent == 0

    def test_mixed_success_and_failure(self):
        ps = PortStats(port=80)
        ps.record_success(64)
        ps.record_failure()
        ps.record_success(64)
        assert ps.attempted == 3
        assert ps.succeeded == 2
        assert ps.failed == 1
        assert ps.bytes_sent == 128

    def test_snapshot(self):
        ps = PortStats(port=5432)
        ps.record_success(100)
        ps.record_failure()
        snap = ps.snapshot()
        assert snap == {
            "port": 5432,
            "attempted": 2,
            "succeeded": 1,
            "failed": 1,
            "bytes_sent": 100,
        }

    def test_thread_safety(self):
        ps = PortStats(port=80)

        def hammer_success():
            for _ in range(500):
                ps.record_success(1)

        def hammer_failure():
            for _ in range(500):
                ps.record_failure()

        threads = [threading.Thread(target=hammer_success) for _ in range(4)]
        threads += [threading.Thread(target=hammer_failure) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert ps.attempted == 4000
        assert ps.succeeded == 2000
        assert ps.failed == 2000
        assert ps.bytes_sent == 2000


class TestStats:
    """Tests for aggregate statistics."""

    def test_initial_state(self):
        stats = Stats()
        assert stats.total_attempted == 0
        assert stats.total_succeeded == 0
        assert stats.total_failed == 0
        assert stats.total_bytes_sent == 0
        assert stats.start_time == 0.0

    def test_record_success(self):
        stats = Stats()
        stats.record_success(64)
        assert stats.total_attempted == 1
        assert stats.total_succeeded == 1
        assert stats.total_bytes_sent == 64

    def test_record_failure(self):
        stats = Stats()
        stats.record_failure()
        assert stats.total_attempted == 1
        assert stats.total_failed == 1

    def test_reset(self):
        stats = Stats()
        stats.record_success(100)
        stats.record_failure()
        stats.reset()
        assert stats.total_attempted == 0
        assert stats.total_succeeded == 0
        assert stats.total_failed == 0
        assert stats.total_bytes_sent == 0
        assert stats.start_time > 0

    def test_snapshot_zero_elapsed(self):
        """Snapshot with start_time=0 shows zero elapsed."""
        stats = Stats()
        snap = stats.snapshot()
        assert snap["elapsed_s"] == 0
        assert snap["connections_per_sec"] == 0

    def test_snapshot_with_data(self):
        stats = Stats()
        stats.start_time = time.time() - 10.0  # 10 seconds ago
        stats.total_attempted = 50
        stats.total_succeeded = 45
        stats.total_failed = 5
        stats.total_bytes_sent = 2880
        snap = stats.snapshot()
        assert snap["total_attempted"] == 50
        assert snap["total_succeeded"] == 45
        assert snap["total_failed"] == 5
        assert snap["total_bytes_sent"] == 2880
        assert snap["elapsed_s"] > 9.0
        assert snap["connections_per_sec"] > 0

    def test_snapshot_rate_calculation(self):
        stats = Stats()
        stats.start_time = time.time() - 2.0
        stats.total_attempted = 10
        snap = stats.snapshot()
        # ~5 connections/sec
        assert 4.0 <= snap["connections_per_sec"] <= 6.0

    def test_thread_safety(self):
        stats = Stats()
        stats.start_time = time.time()

        def hammer_success():
            for _ in range(1000):
                stats.record_success(1)

        def hammer_failure():
            for _ in range(1000):
                stats.record_failure()

        threads = [threading.Thread(target=hammer_success) for _ in range(2)]
        threads += [threading.Thread(target=hammer_failure) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert stats.total_attempted == 4000
        assert stats.total_succeeded == 2000
        assert stats.total_failed == 2000
        assert stats.total_bytes_sent == 2000


class TestProfilePorts:
    """Tests for PROFILE_PORTS definitions."""

    def test_all_profiles_have_ports(self):
        for profile in Profile:
            assert len(PROFILE_PORTS[profile]) > 0

    def test_http_profile_targets_port_80(self):
        ports = PROFILE_PORTS[Profile.HTTP]
        assert len(ports) == 1
        assert ports[0].port == 80

    def test_https_profile_targets_port_443(self):
        ports = PROFILE_PORTS[Profile.HTTPS]
        assert len(ports) == 1
        assert ports[0].port == 443

    def test_mixed_profile_has_multiple_ports(self):
        ports = PROFILE_PORTS[Profile.MIXED]
        assert len(ports) == 4
        port_numbers = {tp.port for tp in ports}
        assert 80 in port_numbers
        assert 443 in port_numbers
        assert 22 in port_numbers
        assert 5432 in port_numbers

    def test_all_ports_profile(self):
        ports = PROFILE_PORTS[Profile.ALL_PORTS]
        assert len(ports) == 4


class TestPayload:
    """Tests for the PAYLOAD constant."""

    def test_payload_is_64_bytes(self):
        assert len(PAYLOAD) == 64

    def test_payload_starts_with_prism_probe(self):
        assert PAYLOAD.startswith(b"PRISM-PROBE ")


class TestTrafficGenerator:
    """Tests for the TrafficGenerator class."""

    def test_initial_state(self):
        gen = TrafficGenerator()
        assert not gen.running
        assert gen.profile == Profile.MIXED
        assert gen.rate_pps == 10
        assert gen.dst_ip == "10.0.2.1"
        assert gen.src_ip == "10.0.1.1"

    def test_custom_ips(self):
        gen = TrafficGenerator(dst_ip="192.168.1.1", src_ip="192.168.1.2")
        assert gen.dst_ip == "192.168.1.1"
        assert gen.src_ip == "192.168.1.2"

    def test_profile_setter_with_enum(self):
        gen = TrafficGenerator()
        gen.profile = Profile.HTTP
        assert gen.profile == Profile.HTTP

    def test_profile_setter_with_string(self):
        gen = TrafficGenerator()
        gen.profile = "https"
        assert gen.profile == Profile.HTTPS

    def test_profile_setter_with_uppercase_string(self):
        gen = TrafficGenerator()
        gen.profile = "HTTP"
        assert gen.profile == Profile.HTTP

    def test_profile_setter_invalid_string(self):
        gen = TrafficGenerator()
        with pytest.raises(ValueError):
            gen.profile = "invalid"

    def test_rate_setter_normal(self):
        gen = TrafficGenerator()
        gen.rate_pps = 50
        assert gen.rate_pps == 50

    def test_rate_clamped_to_min(self):
        gen = TrafficGenerator()
        gen.rate_pps = 0
        assert gen.rate_pps == 1

    def test_rate_clamped_to_max(self):
        gen = TrafficGenerator()
        gen.rate_pps = 200
        assert gen.rate_pps == 100

    def test_rate_negative_clamped(self):
        gen = TrafficGenerator()
        gen.rate_pps = -10
        assert gen.rate_pps == 1

    @patch("src.generator.TrafficGenerator._worker")
    def test_start_sets_running(self, mock_worker):
        gen = TrafficGenerator()
        gen.start()
        assert gen.running is True

    @patch("src.generator.TrafficGenerator._worker")
    def test_start_resets_stats(self, mock_worker):
        gen = TrafficGenerator()
        gen.stats.record_success(100)
        gen.start()
        assert gen.stats.total_attempted == 0

    @patch("src.generator.TrafficGenerator._worker")
    def test_start_creates_port_stats(self, mock_worker):
        gen = TrafficGenerator()
        gen.profile = Profile.MIXED
        gen.start()
        assert len(gen.port_stats) == 4
        assert 80 in gen.port_stats
        assert 443 in gen.port_stats
        assert 22 in gen.port_stats
        assert 5432 in gen.port_stats

    @patch("src.generator.TrafficGenerator._worker")
    def test_start_spawns_workers(self, mock_worker):
        gen = TrafficGenerator()
        gen.profile = Profile.HTTP
        gen.start()
        # One worker per port
        assert len(gen._workers) == 1

    @patch("src.generator.TrafficGenerator._worker")
    def test_start_idempotent(self, mock_worker):
        gen = TrafficGenerator()
        gen.start()
        gen.start()  # Second call should be no-op
        # Only 4 workers (MIXED has 4 ports), not 8
        assert len(gen._workers) == 4

    @patch("src.generator.TrafficGenerator._worker")
    def test_stop(self, mock_worker):
        gen = TrafficGenerator()
        gen.start()
        gen.stop()
        assert gen.running is False
        assert len(gen._workers) == 0

    def test_stop_when_not_running(self):
        gen = TrafficGenerator()
        gen.stop()  # Should not raise
        assert gen.running is False

    @patch("src.generator.TrafficGenerator._worker")
    def test_get_stats(self, mock_worker):
        gen = TrafficGenerator()
        gen.profile = Profile.HTTPS
        gen._rate_cps = 25
        gen.start()
        stats = gen.get_stats()
        assert stats["running"] is True
        assert stats["profile"] == "https"
        assert stats["rate_cps"] == 25
        assert "aggregate" in stats
        assert "per_port" in stats
        gen.stop()

    @patch("src.generator.TrafficGenerator._worker")
    def test_get_stats_per_port(self, mock_worker):
        gen = TrafficGenerator()
        gen.profile = Profile.MIXED
        gen.start()
        stats = gen.get_stats()
        assert len(stats["per_port"]) == 4
        gen.stop()

    @patch("socket.socket")
    def test_worker_success_path(self, mock_socket_class):
        """Test that _worker records success on successful connection."""
        mock_sock = MagicMock()
        mock_socket_class.return_value = mock_sock

        gen = TrafficGenerator()
        gen._profile = Profile.HTTP
        gen.port_stats[80] = PortStats(port=80)
        gen._running = True

        # Make stop event fire after one iteration
        gen._stop_event = threading.Event()

        def stop_after_one(*args, **kwargs):
            gen._stop_event.set()

        mock_sock.sendall.side_effect = stop_after_one

        target = TargetPort(port=80)
        gen._worker(target)

        mock_sock.connect.assert_called_with(("10.0.2.1", 80))
        mock_sock.sendall.assert_called_with(PAYLOAD)
        mock_sock.close.assert_called()
        assert gen.port_stats[80].succeeded == 1
        assert gen.stats.total_succeeded == 1

    @patch("socket.socket")
    def test_worker_failure_path(self, mock_socket_class):
        """Test that _worker records failure on connection error."""
        mock_sock = MagicMock()
        mock_socket_class.return_value = mock_sock
        mock_sock.connect.side_effect = ConnectionRefusedError("refused")

        gen = TrafficGenerator()
        gen._profile = Profile.HTTP
        gen.port_stats[80] = PortStats(port=80)
        gen._running = True
        gen._stop_event = threading.Event()

        call_count = [0]

        def stop_on_second_call(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] >= 1:
                gen._stop_event.set()
            raise ConnectionRefusedError("refused")

        mock_sock.connect.side_effect = stop_on_second_call

        target = TargetPort(port=80)
        gen._worker(target)

        assert gen.port_stats[80].failed >= 1
        assert gen.stats.total_failed >= 1

    @patch("socket.socket")
    def test_worker_bind_failure_non_fatal(self, mock_socket_class):
        """Test that bind failure is non-fatal (kernel picks source)."""
        mock_sock = MagicMock()
        mock_socket_class.return_value = mock_sock
        mock_sock.bind.side_effect = OSError("Address in use")

        gen = TrafficGenerator()
        gen._profile = Profile.HTTP
        gen.port_stats[80] = PortStats(port=80)
        gen._running = True
        gen._stop_event = threading.Event()

        def stop_after_send(*args, **kwargs):
            gen._stop_event.set()

        mock_sock.sendall.side_effect = stop_after_send

        target = TargetPort(port=80)
        gen._worker(target)

        # Despite bind failure, connection should still proceed
        mock_sock.connect.assert_called()
        assert gen.port_stats[80].succeeded == 1


class TestGetGenerator:
    """Tests for the get_generator singleton."""

    def setup_method(self):
        import src.generator as mod
        mod._instance = None

    def teardown_method(self):
        import src.generator as mod
        if mod._instance is not None:
            mod._instance._stop_event.set()
            mod._instance._running = False
            for t in mod._instance._workers:
                t.join(timeout=0.5)
            mod._instance._workers.clear()
        mod._instance = None

    def test_returns_instance(self):
        gen = get_generator()
        assert isinstance(gen, TrafficGenerator)

    def test_singleton_returns_same_instance(self):
        gen1 = get_generator()
        gen2 = get_generator()
        assert gen1 is gen2

    def test_singleton_persists_state(self):
        gen1 = get_generator()
        gen1._rate_cps = 42
        gen2 = get_generator()
        assert gen2._rate_cps == 42
