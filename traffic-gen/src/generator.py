"""Packet crafting and sending engine for the Prism traffic generator.

Sends TCP SYN, UDP, and ICMP-like traffic to the client VF interface at
10.0.2.1 via standard sockets (no root required — the DPU handles L2 steering).
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class Profile(str, Enum):
    """Traffic generation profiles."""

    HTTP = "http"
    HTTPS = "https"
    MIXED = "mixed"
    STORM = "storm"


@dataclass
class FlowSpec:
    """Specification of a single traffic flow."""

    dst_ip: str
    dst_port: int
    protocol: str  # "tcp" or "udp"
    payload_size: int = 64


@dataclass
class Stats:
    """Live counters for the traffic generator."""

    packets_sent: int = 0
    bytes_sent: int = 0
    active_flows: int = 0
    errors: int = 0
    start_time: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_packet(self, nbytes: int) -> None:
        with self._lock:
            self.packets_sent += 1
            self.bytes_sent += nbytes

    def record_error(self) -> None:
        with self._lock:
            self.errors += 1

    def reset(self) -> None:
        with self._lock:
            self.packets_sent = 0
            self.bytes_sent = 0
            self.active_flows = 0
            self.errors = 0
            self.start_time = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = time.time() - self.start_time if self.start_time else 0
            pps = self.packets_sent / elapsed if elapsed > 0 else 0
            return {
                "packets_sent": self.packets_sent,
                "bytes_sent": self.bytes_sent,
                "active_flows": self.active_flows,
                "errors": self.errors,
                "elapsed_s": round(elapsed, 1),
                "current_pps": round(pps, 1),
            }


# ---------------------------------------------------------------------------
# Profile definitions
# ---------------------------------------------------------------------------

PROFILE_FLOWS: dict[Profile, list[FlowSpec]] = {
    Profile.HTTP: [
        FlowSpec(dst_ip="10.0.2.1", dst_port=80, protocol="tcp", payload_size=128),
    ],
    Profile.HTTPS: [
        FlowSpec(dst_ip="10.0.2.1", dst_port=443, protocol="tcp", payload_size=256),
    ],
    Profile.MIXED: [
        FlowSpec(dst_ip="10.0.2.1", dst_port=80, protocol="tcp", payload_size=128),
        FlowSpec(dst_ip="10.0.2.1", dst_port=443, protocol="tcp", payload_size=256),
        FlowSpec(dst_ip="10.0.2.1", dst_port=53, protocol="udp", payload_size=64),
        FlowSpec(dst_ip="10.0.2.1", dst_port=5432, protocol="udp", payload_size=96),
    ],
    Profile.STORM: [
        FlowSpec(dst_ip="10.0.2.1", dst_port=p, protocol="tcp", payload_size=64)
        for p in range(1024, 1124)  # 100 ports
    ],
}


def build_payload(flow: FlowSpec, seq: int) -> bytes:
    """Build a simple payload with a sequence number header.

    Format: 4-byte big-endian sequence number + padding to payload_size.
    """
    header = struct.pack("!I", seq % (2**32))
    padding_len = max(0, flow.payload_size - len(header))
    return header + b"\x00" * padding_len


def create_socket(flow: FlowSpec, src_ip: str = "10.0.1.1") -> socket.socket:
    """Create a standard socket bound to the source interface IP.

    Uses SOCK_STREAM for TCP or SOCK_DGRAM for UDP.
    No root required — the DPU hairpins traffic at L2.
    """
    if flow.protocol == "tcp":
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.5)

    # Bind to source IP (ens8f0v0) — will fail gracefully if IP not assigned
    try:
        sock.bind((src_ip, 0))
    except OSError:
        pass  # Non-fatal: kernel picks source

    return sock


class TrafficGenerator:
    """Main traffic generation engine.

    Manages worker threads that send packets at the configured rate.
    """

    def __init__(
        self,
        src_ip: str = "10.0.1.1",
        interface: str = "ens8f0v0",
    ) -> None:
        self.src_ip = src_ip
        self.interface = interface
        self.stats = Stats()
        self._running = False
        self._rate_pps: int = 1000
        self._profile: Profile = Profile.MIXED
        self._workers: list[threading.Thread] = []
        self._stop_event = threading.Event()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def rate_pps(self) -> int:
        return self._rate_pps

    @rate_pps.setter
    def rate_pps(self, value: int) -> None:
        self._rate_pps = max(100, min(50000, value))

    @property
    def profile(self) -> Profile:
        return self._profile

    @profile.setter
    def profile(self, value: Profile | str) -> None:
        if isinstance(value, str):
            value = Profile(value.lower())
        self._profile = value

    def start(self) -> None:
        """Start traffic generation."""
        if self._running:
            return

        self._stop_event.clear()
        self._running = True
        self.stats.reset()

        flows = PROFILE_FLOWS[self._profile]
        self.stats.active_flows = len(flows)

        for i, flow in enumerate(flows):
            t = threading.Thread(
                target=self._worker,
                args=(flow, i),
                daemon=True,
                name=f"gen-worker-{i}",
            )
            self._workers.append(t)
            t.start()

    def stop(self) -> None:
        """Stop all traffic generation."""
        if not self._running:
            return

        self._stop_event.set()
        self._running = False

        for t in self._workers:
            t.join(timeout=2.0)

        self._workers.clear()
        self.stats.active_flows = 0

    def _worker(self, flow: FlowSpec, worker_id: int) -> None:
        """Worker thread that sends packets for a single flow."""
        flows = PROFILE_FLOWS[self._profile]
        num_flows = len(flows)
        seq = 0

        while not self._stop_event.is_set():
            # Calculate per-flow rate
            per_flow_rate = self._rate_pps / max(num_flows, 1)
            interval = 1.0 / per_flow_rate if per_flow_rate > 0 else 1.0

            payload = build_payload(flow, seq)

            try:
                if flow.protocol == "tcp":
                    self._send_tcp(flow, payload)
                else:
                    self._send_udp(flow, payload)
                self.stats.record_packet(len(payload))
            except (OSError, ConnectionError):
                self.stats.record_error()

            seq += 1

            # Rate limiting — sleep for the inter-packet interval
            # Use a short sleep with stop check for responsiveness
            sleep_end = time.monotonic() + interval
            while time.monotonic() < sleep_end:
                if self._stop_event.is_set():
                    return
                time.sleep(min(0.01, interval))

    def _send_tcp(self, flow: FlowSpec, payload: bytes) -> None:
        """Send a TCP connection attempt + payload (SYN-like behavior)."""
        sock = None
        try:
            sock = create_socket(flow, self.src_ip)
            sock.connect((flow.dst_ip, flow.dst_port))
            sock.sendall(payload)
        finally:
            if sock:
                sock.close()

    def _send_udp(self, flow: FlowSpec, payload: bytes) -> None:
        """Send a UDP datagram."""
        sock = None
        try:
            sock = create_socket(flow, self.src_ip)
            sock.sendto(payload, (flow.dst_ip, flow.dst_port))
        finally:
            if sock:
                sock.close()


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

_instance: TrafficGenerator | None = None


def get_generator() -> TrafficGenerator:
    """Get or create the singleton TrafficGenerator instance."""
    global _instance
    if _instance is None:
        _instance = TrafficGenerator()
    return _instance
