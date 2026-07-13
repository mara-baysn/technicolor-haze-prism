"""Multi-port traffic listener for the Prism receiver.

Listens on TCP and UDP ports on the client VF interface (10.0.2.1, ens8f0v3)
and counts connections, packets, and bytes per port.

When the DPU firewall blocks a port, that port's counters stop incrementing,
providing visible proof that the firewall policy is working.
"""

from __future__ import annotations

import selectors
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PortStats:
    """Per-port traffic counters."""

    port: int
    protocol: str
    connections: int = 0
    packets: int = 0
    bytes_received: int = 0
    last_seen: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, nbytes: int, is_new_conn: bool = False) -> None:
        with self._lock:
            self.packets += 1
            self.bytes_received += nbytes
            self.last_seen = time.time()
            if is_new_conn:
                self.connections += 1

    def snapshot(self) -> dict:
        with self._lock:
            age = time.time() - self.last_seen if self.last_seen > 0 else -1
            return {
                "port": self.port,
                "protocol": self.protocol,
                "connections": self.connections,
                "packets": self.packets,
                "bytes_received": self.bytes_received,
                "last_seen_ago_s": round(age, 1) if age >= 0 else None,
                "active": age < 5.0 if age >= 0 else False,
            }


@dataclass
class ListenerConfig:
    """Configuration for a port listener."""

    port: int
    protocol: str  # "tcp" or "udp"
    bind_ip: str = "10.0.2.1"


# Default ports to listen on
DEFAULT_LISTENERS: list[ListenerConfig] = [
    ListenerConfig(port=80, protocol="tcp"),
    ListenerConfig(port=443, protocol="tcp"),
    ListenerConfig(port=5432, protocol="tcp"),
    ListenerConfig(port=22, protocol="tcp"),
    ListenerConfig(port=53, protocol="udp"),
]


class TCPPortListener:
    """Accepts TCP connections on a single port and counts traffic."""

    def __init__(self, config: ListenerConfig) -> None:
        self.config = config
        self.stats = PortStats(port=config.port, protocol="tcp")
        self._server_sock: Optional[socket.socket] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start listening for TCP connections."""
        self._stop_event.clear()
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.settimeout(1.0)

        try:
            self._server_sock.bind((self.config.bind_ip, self.config.port))
        except OSError:
            # Fallback: bind to all interfaces if specific IP unavailable
            self._server_sock.bind(("0.0.0.0", self.config.port))

        self._server_sock.listen(128)
        self._thread = threading.Thread(
            target=self._accept_loop,
            daemon=True,
            name=f"tcp-listener-{self.config.port}",
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop listening."""
        self._stop_event.set()
        if self._server_sock:
            self._server_sock.close()
        if self._thread:
            self._thread.join(timeout=3.0)

    def _accept_loop(self) -> None:
        """Accept connections and spawn handlers."""
        while not self._stop_event.is_set():
            try:
                conn, addr = self._server_sock.accept()
                self.stats.record(0, is_new_conn=True)
                # Handle connection in a short-lived thread
                threading.Thread(
                    target=self._handle_client,
                    args=(conn,),
                    daemon=True,
                ).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_client(self, conn: socket.socket) -> None:
        """Read data from a connected client until they disconnect."""
        conn.settimeout(5.0)
        try:
            while not self._stop_event.is_set():
                data = conn.recv(4096)
                if not data:
                    break
                self.stats.record(len(data))
        except (socket.timeout, OSError):
            pass
        finally:
            conn.close()


class UDPPortListener:
    """Listens for UDP datagrams on a single port and counts traffic."""

    def __init__(self, config: ListenerConfig) -> None:
        self.config = config
        self.stats = PortStats(port=config.port, protocol="udp")
        self._sock: Optional[socket.socket] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start listening for UDP datagrams."""
        self._stop_event.clear()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.settimeout(1.0)

        try:
            self._sock.bind((self.config.bind_ip, self.config.port))
        except OSError:
            self._sock.bind(("0.0.0.0", self.config.port))

        self._thread = threading.Thread(
            target=self._recv_loop,
            daemon=True,
            name=f"udp-listener-{self.config.port}",
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop listening."""
        self._stop_event.set()
        if self._sock:
            self._sock.close()
        if self._thread:
            self._thread.join(timeout=3.0)

    def _recv_loop(self) -> None:
        """Receive datagrams in a loop."""
        while not self._stop_event.is_set():
            try:
                data, addr = self._sock.recvfrom(65535)
                self.stats.record(len(data), is_new_conn=True)
            except socket.timeout:
                continue
            except OSError:
                break


class TrafficReceiver:
    """Orchestrates all port listeners and aggregates stats.

    Provides the single point of control for starting/stopping all listeners
    and querying per-port statistics.
    """

    def __init__(
        self,
        bind_ip: str = "10.0.2.1",
        interface: str = "ens8f0v3",
        configs: list[ListenerConfig] | None = None,
    ) -> None:
        self.bind_ip = bind_ip
        self.interface = interface
        self._configs = configs or DEFAULT_LISTENERS
        self._listeners: list[TCPPortListener | UDPPortListener] = []
        self._running = False
        self._start_time: float = 0.0

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start all configured port listeners."""
        if self._running:
            return

        self._start_time = time.time()
        self._listeners.clear()

        for cfg in self._configs:
            # Override bind_ip from instance
            cfg_with_ip = ListenerConfig(
                port=cfg.port,
                protocol=cfg.protocol,
                bind_ip=self.bind_ip,
            )

            if cfg.protocol == "tcp":
                listener = TCPPortListener(cfg_with_ip)
            else:
                listener = UDPPortListener(cfg_with_ip)

            listener.start()
            self._listeners.append(listener)

        self._running = True

    def stop(self) -> None:
        """Stop all listeners."""
        if not self._running:
            return

        for listener in self._listeners:
            listener.stop()

        self._running = False

    def get_stats(self) -> dict:
        """Return aggregate and per-port statistics."""
        port_stats = [listener.stats.snapshot() for listener in self._listeners]
        total_packets = sum(s["packets"] for s in port_stats)
        total_bytes = sum(s["bytes_received"] for s in port_stats)
        total_conns = sum(s["connections"] for s in port_stats)
        elapsed = time.time() - self._start_time if self._start_time else 0

        return {
            "running": self._running,
            "bind_ip": self.bind_ip,
            "interface": self.interface,
            "elapsed_s": round(elapsed, 1),
            "total_packets": total_packets,
            "total_bytes": total_bytes,
            "total_connections": total_conns,
            "ports": port_stats,
        }


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_instance: TrafficReceiver | None = None


def get_receiver() -> TrafficReceiver:
    """Get or create the singleton TrafficReceiver instance."""
    global _instance
    if _instance is None:
        _instance = TrafficReceiver()
    return _instance
