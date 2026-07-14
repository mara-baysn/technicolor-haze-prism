"""Unit tests for the traffic receiver listener logic."""

from __future__ import annotations

import socket
import threading
import time
from unittest.mock import patch

import pytest

from src.listener import (
    DEFAULT_LISTENERS,
    ListenerConfig,
    PortStats,
    TCPPortListener,
    TrafficReceiver,
    UDPPortListener,
)


class TestPortStats:
    """Tests for per-port statistics tracking."""

    def test_initial_state(self):
        stats = PortStats(port=80, protocol="tcp")
        assert stats.packets == 0
        assert stats.bytes_received == 0
        assert stats.connections == 0

    def test_record_packet(self):
        stats = PortStats(port=443, protocol="tcp")
        stats.record(128)
        stats.record(256)
        assert stats.packets == 2
        assert stats.bytes_received == 384
        assert stats.connections == 0

    def test_record_new_connection(self):
        stats = PortStats(port=80, protocol="tcp")
        stats.record(0, is_new_conn=True)
        stats.record(64)
        assert stats.connections == 1
        assert stats.packets == 2

    def test_last_seen_updates(self):
        stats = PortStats(port=53, protocol="udp")
        assert stats.last_seen == 0.0
        stats.record(32)
        assert stats.last_seen > 0

    def test_snapshot_format(self):
        stats = PortStats(port=22, protocol="tcp")
        stats.record(100, is_new_conn=True)
        snap = stats.snapshot()
        assert snap["port"] == 22
        assert snap["protocol"] == "tcp"
        assert snap["connections"] == 1
        assert snap["packets"] == 1
        assert snap["bytes_received"] == 100
        assert snap["active"] is True
        assert "last_seen_ago_s" in snap

    def test_thread_safety(self):
        stats = PortStats(port=80, protocol="tcp")

        def hammer():
            for _ in range(500):
                stats.record(1, is_new_conn=True)

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert stats.packets == 2000
        assert stats.connections == 2000
        assert stats.bytes_received == 2000


class TestUDPPortListener:
    """Tests for the UDP listener."""

    def test_start_stop_lifecycle(self):
        """Listener should start and stop without error on a high port."""
        cfg = ListenerConfig(port=19753, protocol="udp", bind_ip="127.0.0.1")
        listener = UDPPortListener(cfg)
        listener.start()
        assert listener._thread is not None
        assert listener._thread.is_alive()

        listener.stop()
        time.sleep(0.1)
        assert not listener._thread.is_alive()

    def test_receives_udp_datagram(self):
        """Listener should count a received UDP datagram."""
        cfg = ListenerConfig(port=19754, protocol="udp", bind_ip="127.0.0.1")
        listener = UDPPortListener(cfg)
        listener.start()

        time.sleep(0.05)  # let listener bind

        # Send a test datagram
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(b"hello prism", ("127.0.0.1", 19754))
        sock.close()

        time.sleep(0.1)  # let listener process

        assert listener.stats.packets >= 1
        assert listener.stats.bytes_received >= 11

        listener.stop()


class TestTCPPortListener:
    """Tests for the TCP listener."""

    def test_start_stop_lifecycle(self):
        """Listener should start and stop without error on a high port."""
        cfg = ListenerConfig(port=19755, protocol="tcp", bind_ip="127.0.0.1")
        listener = TCPPortListener(cfg)
        listener.start()
        assert listener._thread is not None
        assert listener._thread.is_alive()

        listener.stop()
        time.sleep(0.1)
        assert not listener._thread.is_alive()

    def test_accepts_tcp_connection(self):
        """Listener should count a TCP connection and received data."""
        cfg = ListenerConfig(port=19756, protocol="tcp", bind_ip="127.0.0.1")
        listener = TCPPortListener(cfg)
        listener.start()

        time.sleep(0.05)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", 19756))
        sock.sendall(b"test payload data")
        sock.close()

        time.sleep(0.2)

        assert listener.stats.connections >= 1
        assert listener.stats.packets >= 1
        assert listener.stats.bytes_received >= 17

        listener.stop()


class TestTrafficReceiver:
    """Tests for the main receiver orchestrator."""

    def test_initial_state(self):
        receiver = TrafficReceiver(
            bind_ip="127.0.0.1",
            configs=[ListenerConfig(port=19760, protocol="udp", bind_ip="127.0.0.1")],
        )
        assert not receiver.running

    def test_start_stop(self):
        configs = [
            ListenerConfig(port=19761, protocol="udp", bind_ip="127.0.0.1"),
            ListenerConfig(port=19762, protocol="tcp", bind_ip="127.0.0.1"),
        ]
        receiver = TrafficReceiver(bind_ip="127.0.0.1", configs=configs)
        receiver.start()
        assert receiver.running
        assert len(receiver._listeners) == 2

        receiver.stop()
        assert not receiver.running

    def test_get_stats_format(self):
        configs = [
            ListenerConfig(port=19763, protocol="tcp", bind_ip="127.0.0.1"),
        ]
        receiver = TrafficReceiver(bind_ip="127.0.0.1", configs=configs)
        receiver.start()

        stats = receiver.get_stats()
        assert stats["running"] is True
        assert "total_packets" in stats
        assert "total_bytes" in stats
        assert "ports" in stats
        assert len(stats["ports"]) == 1
        assert stats["ports"][0]["port"] == 19763

        receiver.stop()

    def test_default_listeners_defined(self):
        """Verify the default listener set covers expected ports."""
        ports = {cfg.port for cfg in DEFAULT_LISTENERS}
        assert 80 in ports
        assert 443 in ports
        assert 53 in ports
        assert 22 in ports
        assert 5432 in ports

    def test_start_is_idempotent(self):
        """Calling start() twice should not re-create listeners."""
        configs = [
            ListenerConfig(port=19764, protocol="udp", bind_ip="127.0.0.1"),
        ]
        receiver = TrafficReceiver(bind_ip="127.0.0.1", configs=configs)
        receiver.start()
        assert receiver.running

        # Second start should be a no-op
        receiver.start()
        assert receiver.running
        assert len(receiver._listeners) == 1

        receiver.stop()

    def test_stop_when_not_running(self):
        """Calling stop() when not running should be a no-op."""
        configs = [
            ListenerConfig(port=19765, protocol="udp", bind_ip="127.0.0.1"),
        ]
        receiver = TrafficReceiver(bind_ip="127.0.0.1", configs=configs)
        assert not receiver.running
        # Should not raise
        receiver.stop()
        assert not receiver.running


class TestTCPBindFallback:
    """Tests for TCP listener bind fallback and error paths."""

    def test_tcp_bind_fallback_to_all_interfaces(self):
        """TCP listener falls back to 0.0.0.0 when specific IP is unavailable."""
        # Use an IP that does not exist on this machine
        cfg = ListenerConfig(port=19770, protocol="tcp", bind_ip="192.0.2.99")
        listener = TCPPortListener(cfg)
        listener.start()
        time.sleep(0.1)
        # Should be running (fell back to 0.0.0.0)
        assert listener._thread is not None
        assert listener._thread.is_alive()
        listener.stop()

    def test_tcp_handle_client_timeout(self):
        """TCP client handler should survive a read timeout."""
        cfg = ListenerConfig(port=19771, protocol="tcp", bind_ip="127.0.0.1")
        listener = TCPPortListener(cfg)
        listener.start()
        time.sleep(0.05)

        # Connect but send nothing, let server-side timeout expire
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", 19771))
        # Wait for the 5s timeout on the handler to trigger
        time.sleep(0.1)
        sock.close()
        time.sleep(0.1)

        # Listener should still be alive
        assert listener._thread.is_alive()
        assert listener.stats.connections == 1
        listener.stop()

    def test_tcp_handle_client_oserror(self):
        """TCP client handler should handle OSError during recv."""
        cfg = ListenerConfig(port=19775, protocol="tcp", bind_ip="127.0.0.1")
        listener = TCPPortListener(cfg)
        listener.start()
        time.sleep(0.05)

        # Connect and then reset the connection abruptly
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", 19775))
        sock.sendall(b"data")
        time.sleep(0.05)
        # Set SO_LINGER to 0 to send RST on close
        import struct
        sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
        sock.close()
        time.sleep(0.2)

        # Listener should still be running
        assert listener._thread.is_alive()
        assert listener.stats.connections == 1
        listener.stop()

    def test_tcp_accept_loop_handles_oserror(self):
        """TCP accept loop should exit cleanly when socket is closed externally."""
        cfg = ListenerConfig(port=19772, protocol="tcp", bind_ip="127.0.0.1")
        listener = TCPPortListener(cfg)
        listener.start()
        time.sleep(0.05)

        # Force-close the server socket to trigger OSError in accept loop
        listener._server_sock.close()
        time.sleep(1.5)  # Wait for thread to exit

        # Thread should have exited
        assert not listener._thread.is_alive()
        # Clean up
        listener._stop_event.set()


class TestUDPBindFallback:
    """Tests for UDP listener bind fallback and error paths."""

    def test_udp_bind_fallback_to_all_interfaces(self):
        """UDP listener falls back to 0.0.0.0 when specific IP is unavailable."""
        cfg = ListenerConfig(port=19773, protocol="udp", bind_ip="192.0.2.99")
        listener = UDPPortListener(cfg)
        listener.start()
        time.sleep(0.1)
        assert listener._thread is not None
        assert listener._thread.is_alive()
        listener.stop()

    def test_udp_recv_loop_handles_oserror(self):
        """UDP recv loop should exit cleanly when socket is closed externally."""
        cfg = ListenerConfig(port=19774, protocol="udp", bind_ip="127.0.0.1")
        listener = UDPPortListener(cfg)
        listener.start()
        time.sleep(0.05)

        # Force-close the socket to trigger OSError
        listener._sock.close()
        time.sleep(1.5)

        assert not listener._thread.is_alive()
        listener._stop_event.set()


class TestPortStatsEdgeCases:
    """Tests for PortStats snapshot edge cases."""

    def test_snapshot_inactive_when_never_seen(self):
        """Snapshot should show active=False when no data ever received."""
        stats = PortStats(port=9999, protocol="tcp")
        snap = stats.snapshot()
        assert snap["active"] is False
        assert snap["last_seen_ago_s"] is None

    def test_snapshot_inactive_after_timeout(self):
        """Snapshot should show active=False when last_seen is old."""
        stats = PortStats(port=9998, protocol="tcp")
        stats.last_seen = time.time() - 10.0  # 10 seconds ago
        snap = stats.snapshot()
        assert snap["active"] is False
        assert snap["last_seen_ago_s"] >= 9.0
