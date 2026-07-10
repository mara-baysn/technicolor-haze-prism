"""TRex STL/ASTF traffic generator API wrapper."""

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# TRex Python API may not be installed outside the lab
_trex_available = False
try:
    from trex_stl_lib.api import STLClient, STLProfile  # type: ignore[import]

    _trex_available = True
except ImportError:
    logger.info("TRex STL library not available — using mock mode")


@dataclass
class TRexStats:
    tx_bps: float
    rx_bps: float
    tx_pps: float
    rx_pps: float
    total_tx_pkts: int
    total_rx_pkts: int
    drops: int


class TRexDriver:
    """Wraps TRex STL API for traffic generation control."""

    def __init__(self, server: str = "192.168.0.32", sync_port: int = 4501):
        self.server = server
        self.sync_port = sync_port
        self._connected = False
        self._running = False
        self._client: Optional["STLClient"] = None

    async def connect(self) -> bool:
        """Connect to TRex daemon. Returns False if unavailable."""
        if not _trex_available:
            logger.warning(
                "TRex library not installed — running in mock mode"
            )
            return False

        try:
            self._client = STLClient(server=self.server, sync_port=self.sync_port)
            # Run blocking connect in executor to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._client.connect)
            self._connected = True
            logger.info("Connected to TRex at %s:%d", self.server, self.sync_port)
            return True
        except Exception as exc:
            logger.warning("Cannot connect to TRex at %s: %s", self.server, exc)
            self._client = None
            self._connected = False
            return False

    async def load_profile(
        self, profile_path: str, tunables: Optional[dict] = None
    ) -> None:
        """Load a TRex STL traffic profile.

        Args:
            profile_path: Path to .py or .yaml TRex profile file.
            tunables: Optional tunable parameters passed to the profile.
        """
        if not self._connected or self._client is None:
            logger.warning("Not connected to TRex — cannot load profile")
            return

        loop = asyncio.get_event_loop()

        def _load():
            self._client.reset()
            profile = STLProfile.load(profile_path, **(tunables or {}))
            self._client.add_streams(profile.get_streams(), ports=[0])

        await loop.run_in_executor(None, _load)
        logger.info("Loaded TRex profile: %s", profile_path)

    async def start(
        self, duration: Optional[float] = None, multiplier: str = "100%"
    ) -> None:
        """Start traffic generation.

        Args:
            duration: Run duration in seconds (None = run until stopped).
            multiplier: Traffic rate multiplier (e.g., "100%", "10gbps").
        """
        if not self._connected or self._client is None:
            logger.warning("Not connected to TRex — cannot start traffic")
            return

        loop = asyncio.get_event_loop()

        def _start():
            kwargs: dict = {"mult": multiplier, "ports": [0]}
            if duration is not None:
                kwargs["duration"] = duration
            self._client.start(**kwargs)

        await loop.run_in_executor(None, _start)
        self._running = True
        logger.info(
            "TRex traffic started: mult=%s, duration=%s", multiplier, duration
        )

    async def stop(self) -> None:
        """Stop traffic generation."""
        if not self._connected or self._client is None:
            self._running = False
            return

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._client.stop)
        self._running = False
        logger.info("TRex traffic stopped")

    async def get_stats(self) -> TRexStats:
        """Get current traffic statistics.

        Falls back to mock stats if TRex is not connected.
        """
        if not self._connected or self._client is None:
            return await self.get_mock_stats()

        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, self._client.get_stats)

        # TRex returns stats keyed by port number and "global"
        global_stats = raw.get("global", {})
        return TRexStats(
            tx_bps=global_stats.get("tx_bps", 0.0),
            rx_bps=global_stats.get("rx_bps", 0.0),
            tx_pps=global_stats.get("tx_pps", 0.0),
            rx_pps=global_stats.get("rx_pps", 0.0),
            total_tx_pkts=global_stats.get("total_tx_pkts", 0),
            total_rx_pkts=global_stats.get("total_rx_pkts", 0),
            drops=global_stats.get("queue_full", 0),
        )

    async def get_mock_stats(self) -> TRexStats:
        """Generate realistic mock stats for development."""
        return TRexStats(
            tx_bps=random.uniform(80e9, 100e9),
            rx_bps=random.uniform(78e9, 98e9),
            tx_pps=random.uniform(8e6, 12e6),
            rx_pps=random.uniform(7.8e6, 11.8e6),
            total_tx_pkts=random.randint(1_000_000_000, 2_000_000_000),
            total_rx_pkts=random.randint(990_000_000, 1_990_000_000),
            drops=random.randint(0, 1000),
        )

    async def disconnect(self) -> None:
        """Disconnect from TRex daemon."""
        if self._client is not None and self._connected:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._client.disconnect)
            self._connected = False
            logger.info("Disconnected from TRex")

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_running(self) -> bool:
        return self._running
