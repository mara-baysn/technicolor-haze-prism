"""High Availability manager for tc-firewall with split-brain prevention.

Implements a heartbeat-based HA protocol between two DPU instances:
  - Heartbeat: 100ms interval, 300ms timeout (3 missed = failover)
  - Fencing: monotonic generation number prevents stale-active scenarios
  - Split-brain resolution: higher generation wins; loser demotes and flushes

Protocol:
  POST /ha/heartbeat  — peer sends heartbeat with its generation
  GET  /ha/status     — returns current role, generation, peer status

Startup behavior:
  - Default (no --ha-peer-url): ACTIVE single-instance mode
  - With --ha-peer-url: start in STANDBY, wait for peer failure to promote
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class HAState(str, Enum):
    """HA role states."""
    ACTIVE = "ACTIVE"
    STANDBY = "STANDBY"
    FENCING = "FENCING"


class HAManager:
    """Manages HA state, heartbeats, and split-brain fencing.

    The fencing_token is a monotonic generation number that increments
    on every promotion to ACTIVE. All tc-flower rules carry this generation
    in their cookie/comment field, allowing stale rules from a previous
    active to be identified and cleaned during reconciliation.

    Split-brain resolution:
      If both nodes believe they are ACTIVE, they compare generation numbers.
      The node with the higher generation wins and remains ACTIVE.
      The loser transitions to STANDBY and flushes its tc rules.
      The edge router only accepts BGP announcements from the ACTIVE generation.
    """

    def __init__(
        self,
        peer_url: Optional[str] = None,
        heartbeat_interval_ms: int = 100,
        heartbeat_timeout_ms: int = 300,
    ):
        # Role and generation
        if peer_url:
            self.role: HAState = HAState.STANDBY
        else:
            self.role = HAState.ACTIVE
        self.peer_url: Optional[str] = peer_url
        self.generation: int = 1  # monotonic fencing token

        # Heartbeat configuration
        self.heartbeat_interval_ms: int = heartbeat_interval_ms
        self.heartbeat_timeout_ms: int = heartbeat_timeout_ms

        # Peer tracking
        self.last_heartbeat_received: float = 0.0
        self.peer_generation: int = 0
        self.peer_role: Optional[HAState] = None

        # Internal state
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._flush_callback = None  # Set by main.py to flush tc rules on demotion

    @property
    def is_active(self) -> bool:
        """True if this instance is the active (primary) node."""
        return self.role == HAState.ACTIVE

    @property
    def missed_heartbeats(self) -> int:
        """Number of missed heartbeat intervals since last peer heartbeat."""
        if self.last_heartbeat_received == 0.0:
            return 0
        elapsed_ms = (time.time() - self.last_heartbeat_received) * 1000
        interval = self.heartbeat_interval_ms
        if interval <= 0:
            return 0
        return int(elapsed_ms / interval)

    @property
    def peer_alive(self) -> bool:
        """True if the peer has sent a heartbeat within the timeout window."""
        if self.last_heartbeat_received == 0.0:
            return False
        elapsed_ms = (time.time() - self.last_heartbeat_received) * 1000
        return elapsed_ms < self.heartbeat_timeout_ms

    def receive_heartbeat(self, peer_generation: int, peer_role: str) -> dict:
        """Process a heartbeat received from the peer.

        Returns a dict with the resolution action taken (if any).
        """
        self.last_heartbeat_received = time.time()
        self.peer_generation = peer_generation
        self.peer_role = HAState(peer_role)

        result = {"accepted": True, "action": "none"}

        # Split-brain detection: both think they are ACTIVE
        if self.role == HAState.ACTIVE and self.peer_role == HAState.ACTIVE:
            result = self._resolve_split_brain(peer_generation)

        return result

    def _resolve_split_brain(self, peer_generation: int) -> dict:
        """Resolve split-brain: higher generation wins.

        If generations are equal (should not happen in normal operation),
        both enter FENCING state — operator intervention required.
        """
        logger.warning(
            f"SPLIT-BRAIN detected! "
            f"local_gen={self.generation} peer_gen={peer_generation}"
        )

        if self.generation > peer_generation:
            # We win — peer should demote
            logger.info(
                f"Split-brain resolved: we win "
                f"(gen {self.generation} > {peer_generation})"
            )
            return {"accepted": True, "action": "peer_must_demote"}

        elif self.generation < peer_generation:
            # We lose — demote ourselves
            logger.warning(
                f"Split-brain resolved: we lose "
                f"(gen {self.generation} < {peer_generation}), demoting"
            )
            self._demote()
            return {"accepted": True, "action": "self_demoted"}

        else:
            # Equal generation — fencing state (should not happen)
            logger.critical(
                f"Split-brain with EQUAL generations ({self.generation})! "
                f"Entering FENCING state — operator intervention required."
            )
            self.role = HAState.FENCING
            return {"accepted": True, "action": "fencing_equal_generation"}

    def promote(self) -> dict:
        """Promote this instance to ACTIVE.

        Increments the generation counter (fencing token) to invalidate
        any stale rules from a previous active.
        """
        if self.role == HAState.ACTIVE:
            return {
                "status": "already_active",
                "generation": self.generation,
            }

        previous_role = self.role
        self.generation += 1
        self.role = HAState.ACTIVE

        logger.warning(
            f"PROMOTED to ACTIVE: {previous_role.value} -> ACTIVE "
            f"(generation={self.generation})"
        )

        return {
            "status": "promoted",
            "previous_role": previous_role.value,
            "generation": self.generation,
        }

    def demote(self) -> dict:
        """Demote this instance to STANDBY (public API)."""
        if self.role == HAState.STANDBY:
            return {
                "status": "already_standby",
                "generation": self.generation,
            }

        previous_role = self.role
        self._demote()

        return {
            "status": "demoted",
            "previous_role": previous_role.value,
            "generation": self.generation,
        }

    def _demote(self) -> None:
        """Internal demotion: transition to STANDBY and flush rules."""
        self.role = HAState.STANDBY
        logger.warning(f"DEMOTED to STANDBY (generation={self.generation})")

        # Flush tc rules — stale rules from our active period must be removed
        if self._flush_callback:
            try:
                self._flush_callback()
            except Exception as e:
                logger.error(f"Error flushing rules on demotion: {e}")

    def get_status(self) -> dict:
        """Return current HA status for the status endpoint."""
        return {
            "role": self.role.value,
            "generation": self.generation,
            "peer_url": self.peer_url,
            "peer_alive": self.peer_alive,
            "peer_generation": self.peer_generation,
            "peer_role": self.peer_role.value if self.peer_role else None,
            "last_heartbeat_from_peer": self.last_heartbeat_received,
            "missed_heartbeats": self.missed_heartbeats,
            "heartbeat_interval_ms": self.heartbeat_interval_ms,
            "heartbeat_timeout_ms": self.heartbeat_timeout_ms,
        }

    async def start(self) -> None:
        """Start the HA heartbeat sender and monitor tasks."""
        if not self.peer_url:
            logger.info("HA: no peer configured, running in single-instance ACTIVE mode")
            return

        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_sender())
        self._monitor_task = asyncio.create_task(self._heartbeat_monitor())
        logger.info(
            f"HA started: role={self.role.value} peer={self.peer_url} "
            f"interval={self.heartbeat_interval_ms}ms "
            f"timeout={self.heartbeat_timeout_ms}ms"
        )

    async def stop(self) -> None:
        """Stop the HA background tasks."""
        self._running = False
        for task in (self._heartbeat_task, self._monitor_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("HA stopped")

    async def _heartbeat_sender(self) -> None:
        """Periodically send heartbeats to the peer."""
        async with httpx.AsyncClient(timeout=1.0) as client:
            while self._running:
                try:
                    await client.post(
                        f"{self.peer_url}/ha/heartbeat",
                        json={
                            "generation": self.generation,
                            "role": self.role.value,
                        },
                    )
                except Exception as e:
                    logger.debug(f"Heartbeat send failed: {e}")

                await asyncio.sleep(self.heartbeat_interval_ms / 1000)

    async def _heartbeat_monitor(self) -> None:
        """Monitor peer heartbeats; trigger failover on timeout."""
        # Give the peer a grace period on startup
        await asyncio.sleep(self.heartbeat_timeout_ms / 1000)

        while self._running:
            if self.role == HAState.STANDBY and not self.peer_alive:
                if self.last_heartbeat_received > 0:
                    # Peer was alive but is now unresponsive — promote
                    logger.warning(
                        f"Peer heartbeat timeout "
                        f"(missed {self.missed_heartbeats} heartbeats), "
                        f"promoting to ACTIVE"
                    )
                    self.promote()

            await asyncio.sleep(self.heartbeat_interval_ms / 1000)


# Module-level singleton — initialized by main.py based on CLI args
ha_manager: Optional[HAManager] = None


def get_ha_manager() -> HAManager:
    """Get the HA manager singleton. Creates a default single-instance one if needed."""
    global ha_manager
    if ha_manager is None:
        ha_manager = HAManager(peer_url=None)
    return ha_manager


def init_ha_manager(peer_url: Optional[str] = None, **kwargs) -> HAManager:
    """Initialize the HA manager with configuration."""
    global ha_manager
    ha_manager = HAManager(peer_url=peer_url, **kwargs)
    return ha_manager
