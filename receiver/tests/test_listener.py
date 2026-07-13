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
