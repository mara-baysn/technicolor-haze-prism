"""TCP connection-based traffic generator for the Prism firewall demo.

Sends TCP connections to the client VF interface (10.0.2.1) on multiple ports.
Designed to run inside ns-inet namespace on the HPE server.

Uses simple socket.connect() — no scapy needed, TCP works through tc-flower.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field
from enum import Enum


class Profile(str, Enum):
    """Traffic generation profiles."""

    HTTP = "http"
    HTTPS = "https"
    MIXED = "mixed"
    ALL_PORTS = "all_ports"


@dataclass
class TargetPort:
    """A target port to send traffic to."""

    port: int
    protocol: str = "tcp"


@dataclass
class PortStats:
    """Per-port connection statistics."""

    port: int
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    bytes_sent: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_success(self, nbytes: int) -> None:
        with self._lock:
            self.attempted += 1
            self.succeeded += 1
            self.bytes_sent += nbytes

    def record_failure(self) -> None:
        with self._lock:
            self.attempted += 1
            self.failed += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "port": self.port,
                "attempted": self.attempted,
                "succeeded": self.succeeded,
                "failed": self.failed,
                "bytes_sent": self.bytes_sent,
            }


@dataclass
class Stats:
    """Aggregate stats across all ports."""

    total_attempted: int = 0
    total_succeeded: int = 0
    total_failed: int = 0
    total_bytes_sent: int = 0
    start_time: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_success(self, nbytes: int) -> None:
        with self._lock:
            self.total_attempted += 1
            self.total_succeeded += 1
            self.total_bytes_sent += nbytes

    def record_failure(self) -> None:
        with self._lock:
            self.total_attempted += 1
            self.total_failed += 1

    def reset(self) -> None:
        with self._lock:
            self.total_attempted = 0
            self.total_succeeded = 0
            self.total_failed = 0
            self.total_bytes_sent = 0
            self.start_time = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = time.time() - self.start_time if self.start_time else 0
            rate = self.total_attempted / elapsed if elapsed > 0 else 0
            return {
                "total_attempted": self.total_attempted,
                "total_succeeded": self.total_succeeded,
                "total_failed": self.total_failed,
                "total_bytes_sent": self.total_bytes_sent,
                "elapsed_s": round(elapsed, 1),
                "connections_per_sec": round(rate, 1),
            }


# Profile definitions: which ports to target
PROFILE_PORTS: dict[Profile, list[TargetPort]] = {
    Profile.HTTP: [
        TargetPort(port=80),
    ],
    Profile.HTTPS: [
        TargetPort(port=443),
    ],
    Profile.MIXED: [
        TargetPort(port=80),
        TargetPort(port=443),
        TargetPort(port=22),
        TargetPort(port=5432),
    ],
    Profile.ALL_PORTS: [
        TargetPort(port=80),
        TargetPort(port=443),
        TargetPort(port=22),
        TargetPort(port=5432),
    ],
}

# Payload sent on each successful connection
PAYLOAD = b"PRISM-PROBE " + b"X" * 52  # 64 bytes total


class TrafficGenerator:
    """TCP connection-based traffic generator.

    Repeatedly attempts TCP connections to target ports on 10.0.2.1.
    When the DPU firewall blocks a port, connections to that port fail,
    providing visible proof that the firewall is working.
    """

    def __init__(
        self,
        dst_ip: str = "10.0.2.1",
        src_ip: str = "10.0.1.1",
    ) -> None:
        self.dst_ip = dst_ip
        self.src_ip = src_ip
        self.stats = Stats()
        self.port_stats: dict[int, PortStats] = {}
        self._running = False
        self._profile: Profile = Profile.MIXED
        self._rate_cps: int = 10  # connections per second total
        self._workers: list[threading.Thread] = []
        self._stop_event = threading.Event()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def profile(self) -> Profile:
        return self._profile

    @profile.setter
    def profile(self, value: Profile | str) -> None:
        if isinstance(value, str):
            value = Profile(value.lower())
        self._profile = value

    @property
    def rate_pps(self) -> int:
        return self._rate_cps

    @rate_pps.setter
    def rate_pps(self, value: int) -> None:
        self._rate_cps = max(1, min(100, value))

    def start(self) -> None:
        """Start traffic generation."""
        if self._running:
            return

        self._stop_event.clear()
        self._running = True
        self.stats.reset()
        self.port_stats.clear()

        ports = PROFILE_PORTS[self._profile]
        for tp in ports:
            self.port_stats[tp.port] = PortStats(port=tp.port)

        for tp in ports:
            t = threading.Thread(
                target=self._worker,
                args=(tp,),
                daemon=True,
                name=f"gen-{tp.port}",
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

    def get_stats(self) -> dict:
        """Return full statistics snapshot."""
        return {
            "running": self._running,
            "profile": self._profile.value,
            "rate_cps": self._rate_cps,
            "aggregate": self.stats.snapshot(),
            "per_port": [ps.snapshot() for ps in self.port_stats.values()],
        }

    def _worker(self, target: TargetPort) -> None:
        """Worker thread: repeatedly connect to a single port."""
        num_ports = len(PROFILE_PORTS[self._profile])
        ps = self.port_stats[target.port]

        while not self._stop_event.is_set():
            # Per-port rate = total rate / number of ports
            per_port_rate = self._rate_cps / max(num_ports, 1)
            interval = 1.0 / per_port_rate if per_port_rate > 0 else 1.0

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                try:
                    sock.bind((self.src_ip, 0))
                except OSError:
                    pass  # Non-fatal: kernel picks source

                sock.connect((self.dst_ip, target.port))
                sock.sendall(PAYLOAD)
                nbytes = len(PAYLOAD)
                sock.close()

                ps.record_success(nbytes)
                self.stats.record_success(nbytes)
            except (OSError, ConnectionError):
                ps.record_failure()
                self.stats.record_failure()
                try:
                    sock.close()
                except Exception:
                    pass

            # Rate limiting with stop check
            sleep_end = time.monotonic() + interval
            while time.monotonic() < sleep_end:
                if self._stop_event.is_set():
                    return
                time.sleep(min(0.05, interval))


# Singleton
_instance: TrafficGenerator | None = None


def get_generator() -> TrafficGenerator:
    """Get or create the singleton TrafficGenerator instance."""
    global _instance
    if _instance is None:
        _instance = TrafficGenerator()
    return _instance
