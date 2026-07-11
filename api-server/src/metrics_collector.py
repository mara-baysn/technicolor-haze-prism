"""Unified metrics collection from DPDK telemetry, DOCA counters, and TRex stats.

Returns zeros for any source that is unreachable — no fake/random data.
"""

import csv
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import httpx

from .ssh_executor import SSHExecutor
from .trex_driver import TRexDriver

logger = logging.getLogger(__name__)

CPU_CMD = "grep 'cpu ' /proc/stat | awk '{u=($2+$4)*100/($2+$4+$5)} END {print u}'"


@dataclass
class MetricsSnapshot:
    timestamp: float = field(default_factory=time.time)
    tx_gbps: float = 0.0
    rx_gbps: float = 0.0
    offload_ratio_pct: float = 0.0
    active_sessions: int = 0
    vm_cpu_pct: float = 0.0
    dpu_arm_cpu_pct: float = 0.0
    new_flows_per_sec: float = 0.0
    offloaded_flows: int = 0
    queue_depths: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class MetricsCollector:
    """Polls real sources only. Returns zeros when hardware is unreachable."""

    def __init__(
        self,
        ssh_executor: SSHExecutor,
        trex_driver: TRexDriver,
        prism_admin_url: str = "http://192.168.9.23:8443",
    ):
        self._ssh = ssh_executor
        self._trex = trex_driver
        self._admin_url = prism_admin_url
        self._latest: Optional[MetricsSnapshot] = None
        self._history: list[MetricsSnapshot] = []
        self._max_history = 1800

    async def collect(self) -> MetricsSnapshot:
        """Collect from real sources. Zeros for anything unreachable."""
        snapshot = MetricsSnapshot(timestamp=time.time())

        # TRex (only if actually connected to hardware)
        try:
            if self._trex.is_connected:
                trex_stats = await self._trex.get_stats()
                snapshot.tx_gbps = trex_stats.tx_bps / 1e9
                snapshot.rx_gbps = trex_stats.rx_bps / 1e9
        except Exception as exc:
            logger.debug("TRex: %s", exc)

        # Prism admin API (real datapath counters from HPE server)
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{self._admin_url}/api/v1/firewalls/default/metrics")
                if resp.status_code == 200:
                    data = resp.json()
                    snapshot.offload_ratio_pct = data.get("offload_ratio_percent", 0.0)
                    snapshot.active_sessions = data.get("active_sessions", 0)
                    snapshot.offloaded_flows = data.get("offloaded_flows", 0)
                    snapshot.new_flows_per_sec = data.get("packets_per_sec", 0.0)
                    snapshot.queue_depths = data.get("queue_depths", [])
        except Exception as exc:
            logger.debug("Prism admin: %s", exc)

        # VM CPU (real SSH to HPE server)
        try:
            result = await self._ssh.execute("192.168.9.23", CPU_CMD, timeout=5.0)
            if result.exit_code == 0 and result.stdout.strip():
                snapshot.vm_cpu_pct = float(result.stdout.strip())
        except Exception as exc:
            logger.debug("VM CPU: %s", exc)

        # DPU ARM CPU (real SSH to DPU)
        try:
            result = await self._ssh.execute("192.168.0.38", CPU_CMD, timeout=5.0)
            if result.exit_code == 0 and result.stdout.strip():
                snapshot.dpu_arm_cpu_pct = float(result.stdout.strip())
        except Exception as exc:
            logger.debug("DPU CPU: %s", exc)

        self._latest = snapshot
        self._history.append(snapshot)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        return snapshot

    @property
    def latest(self) -> Optional[MetricsSnapshot]:
        return self._latest

    @property
    def history(self) -> list[MetricsSnapshot]:
        return list(self._history)

    def export_csv(self, path: str) -> None:
        if not self._history:
            return

        filepath = Path(path)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        fieldnames = [
            "timestamp", "tx_gbps", "rx_gbps", "offload_ratio_pct",
            "active_sessions", "vm_cpu_pct", "dpu_arm_cpu_pct",
            "new_flows_per_sec", "offloaded_flows", "queue_depths",
        ]

        with open(filepath, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for snap in self._history:
                row = snap.to_dict()
                row["queue_depths"] = json.dumps(row["queue_depths"])
                writer.writerow(row)
