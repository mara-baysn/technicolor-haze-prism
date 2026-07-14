"""Stateful SNAT with ephemeral port allocation tracking.

Manages port pools for masquerade-mode SNAT where multiple outbound
connections from the same tenant share a single public IP. Each new
outbound connection gets a unique source port from the pool.

Pool range: 1024-65535 (64512 available ports per public IP)

For the PoC/tc-flower path, masquerade mode installs a single tc rule
with pedit for the IP rewrite. Actual per-connection port allocation
happens in kernel conntrack (ct action). In production, DOCA Flow
handles port allocation in hardware CT offload.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Port pool boundaries
PORT_MIN = 1024
PORT_MAX = 65535
DEFAULT_POOL_SIZE = PORT_MAX - PORT_MIN + 1  # 64512

# Warning threshold (percentage)
POOL_WARNING_THRESHOLD = 0.80


@dataclass
class PortAllocation:
    """Tracks a single allocated port."""
    port: int
    src_ip: str
    dst_ip: str
    dst_port: int
    protocol: str
    allocated_at: float = field(default_factory=time.time)
    timeout: float = 300.0  # 5 minute default timeout


@dataclass
class PoolStats:
    """Statistics for a single public IP's port pool."""
    public_ip: str
    total_ports: int
    used_ports: int
    free_ports: int
    utilization_pct: float
    warning: bool
    exhausted: bool


class SNATState:
    """Thread-safe stateful SNAT port allocation manager.

    Manages ephemeral port pools for public IPs used in masquerade mode.
    Each public IP has a pool of ports (1024-65535) that are allocated
    to outbound connections and freed on timeout or explicit release.
    """

    def __init__(self, pool_size: int = DEFAULT_POOL_SIZE):
        """Initialize SNAT state tracker.

        Args:
            pool_size: Maximum ports per public IP (default 64512).
                       Can be reduced for testing or resource constraints.
        """
        self._lock = threading.Lock()
        self._pool_size = min(pool_size, DEFAULT_POOL_SIZE)

        # Per public IP: set of allocated ports
        self._allocated: Dict[str, Set[int]] = {}

        # Per public IP: mapping of port -> allocation details
        self._allocations: Dict[str, Dict[int, PortAllocation]] = {}

        # Per public IP: next port to try (round-robin hint)
        self._next_port: Dict[str, int] = {}

    @property
    def pool_size(self) -> int:
        """Configured pool size per public IP."""
        return self._pool_size

    def allocate_port(
        self,
        public_ip: str,
        src_ip: str,
        dst_ip: str,
        dst_port: int,
        protocol: str = "tcp",
        timeout: float = 300.0,
    ) -> Optional[int]:
        """Allocate an ephemeral source port for a new outbound connection.

        Args:
            public_ip: The public IP being NATed to.
            src_ip: Original private source IP.
            dst_ip: Destination IP of the outbound connection.
            dst_port: Destination port of the outbound connection.
            protocol: Protocol (tcp/udp).
            timeout: Seconds before this allocation expires.

        Returns:
            Allocated port number, or None if pool is exhausted.
        """
        with self._lock:
            # Initialize pool tracking for this public IP if needed
            if public_ip not in self._allocated:
                self._allocated[public_ip] = set()
                self._allocations[public_ip] = {}
                self._next_port[public_ip] = PORT_MIN

            allocated_set = self._allocated[public_ip]

            # Check exhaustion
            if len(allocated_set) >= self._pool_size:
                logger.error(
                    f"Port pool EXHAUSTED for {public_ip}: "
                    f"{len(allocated_set)}/{self._pool_size} ports in use"
                )
                return None

            # Log warning if above threshold
            utilization = len(allocated_set) / self._pool_size
            if utilization >= POOL_WARNING_THRESHOLD:
                logger.warning(
                    f"Port pool high utilization for {public_ip}: "
                    f"{len(allocated_set)}/{self._pool_size} "
                    f"({utilization:.1%}) ports in use"
                )

            # Find next available port using round-robin search
            port = self._find_available_port(public_ip)
            if port is None:
                # Should not happen if exhaustion check passed, but be safe
                logger.error(f"Failed to find available port for {public_ip}")
                return None

            # Record the allocation
            allocated_set.add(port)
            self._allocations[public_ip][port] = PortAllocation(
                port=port,
                src_ip=src_ip,
                dst_ip=dst_ip,
                dst_port=dst_port,
                protocol=protocol,
                timeout=timeout,
            )

            logger.debug(
                f"Allocated port {public_ip}:{port} for "
                f"{src_ip} -> {dst_ip}:{dst_port}/{protocol}"
            )

            return port

    def release_port(self, public_ip: str, port: int) -> bool:
        """Release a previously allocated port back to the pool.

        Args:
            public_ip: The public IP the port belongs to.
            port: The port number to release.

        Returns:
            True if the port was released, False if it was not allocated.
        """
        with self._lock:
            if public_ip not in self._allocated:
                return False

            if port not in self._allocated[public_ip]:
                return False

            self._allocated[public_ip].discard(port)
            self._allocations[public_ip].pop(port, None)

            logger.debug(f"Released port {public_ip}:{port}")
            return True

    def release_expired(self) -> int:
        """Release all ports whose timeout has elapsed.

        Returns:
            Number of ports released.
        """
        now = time.time()
        released = 0

        with self._lock:
            for public_ip in list(self._allocations.keys()):
                expired_ports = []
                for port, alloc in self._allocations[public_ip].items():
                    if now - alloc.allocated_at >= alloc.timeout:
                        expired_ports.append(port)

                for port in expired_ports:
                    self._allocated[public_ip].discard(port)
                    del self._allocations[public_ip][port]
                    released += 1

                if expired_ports:
                    logger.info(
                        f"Released {len(expired_ports)} expired ports for {public_ip}"
                    )

        return released

    def get_pool_stats(self, public_ip: Optional[str] = None) -> list:
        """Get port pool statistics.

        Args:
            public_ip: If specified, get stats for just this IP.
                       If None, get stats for all public IPs.

        Returns:
            List of PoolStats for each tracked public IP.
        """
        with self._lock:
            stats = []
            ips = [public_ip] if public_ip else list(self._allocated.keys())

            for ip in ips:
                if ip not in self._allocated:
                    stats.append(PoolStats(
                        public_ip=ip,
                        total_ports=self._pool_size,
                        used_ports=0,
                        free_ports=self._pool_size,
                        utilization_pct=0.0,
                        warning=False,
                        exhausted=False,
                    ))
                    continue

                used = len(self._allocated[ip])
                free = self._pool_size - used
                util = used / self._pool_size if self._pool_size > 0 else 0.0

                stats.append(PoolStats(
                    public_ip=ip,
                    total_ports=self._pool_size,
                    used_ports=used,
                    free_ports=free,
                    utilization_pct=round(util * 100, 2),
                    warning=util >= POOL_WARNING_THRESHOLD,
                    exhausted=used >= self._pool_size,
                ))

            return stats

    def is_exhausted(self, public_ip: str) -> bool:
        """Check if a public IP's port pool is fully exhausted."""
        with self._lock:
            if public_ip not in self._allocated:
                return False
            return len(self._allocated[public_ip]) >= self._pool_size

    def get_utilization(self, public_ip: str) -> float:
        """Get current utilization ratio (0.0 - 1.0) for a public IP."""
        with self._lock:
            if public_ip not in self._allocated:
                return 0.0
            return len(self._allocated[public_ip]) / self._pool_size

    def _find_available_port(self, public_ip: str) -> Optional[int]:
        """Find the next available port using round-robin search.

        Must be called with self._lock held.
        """
        allocated_set = self._allocated[public_ip]
        start = self._next_port[public_ip]

        # Calculate the effective max port based on pool size
        effective_max = PORT_MIN + self._pool_size - 1

        # Search from current position to end of range
        port = start
        while port <= effective_max:
            if port not in allocated_set:
                self._next_port[public_ip] = port + 1
                if self._next_port[public_ip] > effective_max:
                    self._next_port[public_ip] = PORT_MIN
                return port
            port += 1

        # Wrap around and search from beginning to start
        port = PORT_MIN
        while port < start:
            if port not in allocated_set:
                self._next_port[public_ip] = port + 1
                return port
            port += 1

        return None


# Module-level singleton instance
snat_state = SNATState()
