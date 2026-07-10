"""SSH connection pool and command executor for lab hardware."""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import asyncssh
except ImportError:
    asyncssh = None  # type: ignore[assignment]
    logger.warning("asyncssh not available — SSH operations will fail gracefully")


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float


class SSHExecutor:
    """Manages SSH connections to lab hardware with connection pooling."""

    def __init__(self):
        self._connections: dict[str, "asyncssh.SSHClientConnection"] = {}

    async def connect(
        self,
        host: str,
        user: str,
        auth_method: str = "key",
        key_path: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        """Establish SSH connection to a host.

        Args:
            host: Hostname or IP address.
            user: SSH username.
            auth_method: Either "key" or "password".
            key_path: Path to private key file (when auth_method="key").
            password: SSH password (when auth_method="password").
        """
        if asyncssh is None:
            logger.warning(
                "asyncssh not installed — cannot connect to %s", host
            )
            return

        if host in self._connections:
            logger.debug("Already connected to %s, reusing connection", host)
            return

        connect_kwargs: dict = {
            "host": host,
            "username": user,
            "known_hosts": None,  # Lab environment — skip host key verification
        }

        if auth_method == "key" and key_path:
            connect_kwargs["client_keys"] = [key_path]
        elif auth_method == "password" and password:
            connect_kwargs["password"] = password
        else:
            # Fall back to agent-based auth
            pass

        try:
            conn = await asyncssh.connect(**connect_kwargs)
            self._connections[host] = conn
            logger.info("Connected to %s@%s", user, host)
        except (ConnectionRefusedError, OSError) as exc:
            logger.warning(
                "Cannot reach %s — lab may be offline: %s", host, exc
            )
        except asyncssh.Error as exc:
            logger.warning("SSH error connecting to %s: %s", host, exc)

    async def execute(
        self, host: str, command: str, timeout: float = 30.0
    ) -> CommandResult:
        """Execute command on a connected host.

        Args:
            host: Target host (must be previously connected).
            command: Shell command to run.
            timeout: Maximum seconds to wait for completion.

        Returns:
            CommandResult with exit code, stdout, stderr, and duration.

        Raises:
            RuntimeError: If not connected to the specified host.
            TimeoutError: If command exceeds timeout.
        """
        if host not in self._connections:
            raise RuntimeError(
                f"Not connected to {host}. Call connect() first."
            )

        conn = self._connections[host]
        start = time.perf_counter()

        try:
            result = await asyncio.wait_for(
                conn.run(command, check=False), timeout=timeout
            )
            duration_ms = (time.perf_counter() - start) * 1000.0

            return CommandResult(
                exit_code=result.exit_status or 0,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                duration_ms=duration_ms,
            )
        except asyncio.TimeoutError:
            duration_ms = (time.perf_counter() - start) * 1000.0
            raise TimeoutError(
                f"Command on {host} timed out after {timeout}s: {command}"
            ) from None

    async def execute_background(self, host: str, command: str) -> None:
        """Execute command in background (don't wait for completion).

        Uses nohup + output redirection to fully detach the process.
        """
        if host not in self._connections:
            raise RuntimeError(
                f"Not connected to {host}. Call connect() first."
            )

        conn = self._connections[host]
        bg_command = f"nohup {command} > /dev/null 2>&1 &"
        await conn.run(bg_command, check=False)
        logger.debug("Background command launched on %s: %s", host, command)

    async def disconnect(self, host: str) -> None:
        """Close SSH connection to a host."""
        if host in self._connections:
            self._connections[host].close()
            del self._connections[host]
            logger.info("Disconnected from %s", host)

    async def disconnect_all(self) -> None:
        """Close all SSH connections."""
        for host in list(self._connections.keys()):
            await self.disconnect(host)

    @property
    def connected_hosts(self) -> list[str]:
        """Return list of currently connected host addresses."""
        return list(self._connections.keys())

    def is_connected(self, host: str) -> bool:
        """Check if a specific host is connected."""
        return host in self._connections
