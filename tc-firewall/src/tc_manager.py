"""Manage tc-flower rules on BF3 DPU eSwitch representor ports.

Uses subprocess to call `tc` commands directly — proven reliable on BF3
compared to pyroute2/netlink which has edge cases with eSwitch offload.

Port mapping:
  pf0vf0 = "internet" side (VF0) — uplink facing
  pf0vf3 = "client" side (VF3)   — VM/container facing

For ALLOW rules: bidirectional mirred redirect
  ingress pf0vf0 → redirect pf0vf3 (forward)
  ingress pf0vf3 → redirect pf0vf0 (reverse)

For DENY rules: drop action on matched traffic
  ingress pf0vf0 → drop
  ingress pf0vf3 → drop (optional, for symmetric deny)
"""

import subprocess
import re
import logging
import threading
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Lock protecting handle allocation: tc add + get_last_handle must be atomic
# to prevent concurrent rule creation from corrupting the handle map.
_tc_lock = threading.Lock()

# Port mapping
INTERNET_PORT = "pf0vf0"
CLIENT_PORT = "pf0vf3"


@dataclass
class TcRuleStats:
    """Statistics for a single tc filter rule."""
    handle: str
    priority: int
    protocol: str
    in_hw: bool
    packets: int = 0
    bytes: int = 0
    action: str = ""
    match_src: Optional[str] = None
    match_dst: Optional[str] = None
    match_sport: Optional[int] = None
    match_dport: Optional[int] = None


def _run_tc(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a tc command and return the result."""
    cmd = ["tc"] + args
    logger.debug(f"Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if check and result.returncode != 0:
        logger.error(f"tc command failed: {' '.join(cmd)}\n{result.stderr}")
        raise TcError(f"tc failed: {result.stderr.strip()}")
    return result


class TcError(Exception):
    """Error from tc command execution."""
    pass


def ensure_ingress_qdisc(port: str) -> None:
    """Ensure ingress qdisc exists on the port. Idempotent."""
    # Check if qdisc already exists
    result = _run_tc(["qdisc", "show", "dev", port, "ingress"], check=False)
    if "ingress" in result.stdout:
        logger.debug(f"Ingress qdisc already exists on {port}")
        return

    # Add ingress qdisc
    _run_tc(["qdisc", "add", "dev", port, "ingress"])
    logger.info(f"Added ingress qdisc on {port}")


def add_allow_rule(
    src_ip: Optional[str] = None,
    dst_ip: Optional[str] = None,
    src_port: Optional[int] = None,
    dst_port: Optional[int] = None,
    protocol: str = "ip",
    priority: int = 100,
    in_port: str = INTERNET_PORT,
    out_port: str = CLIENT_PORT,
) -> tuple[str, str]:
    """Add bidirectional allow rule (mirred redirect).

    Returns (forward_handle, reverse_handle).
    """
    # Forward direction: in_port ingress → redirect to out_port
    fwd_handle = _add_redirect_rule(
        dev=in_port,
        target=out_port,
        src_ip=src_ip,
        dst_ip=dst_ip,
        src_port=src_port,
        dst_port=dst_port,
        protocol=protocol,
        priority=priority,
    )

    # Reverse direction: out_port ingress → redirect to in_port
    # Swap src/dst for the reverse path
    rev_handle = _add_redirect_rule(
        dev=out_port,
        target=in_port,
        src_ip=dst_ip,      # swap
        dst_ip=src_ip,      # swap
        src_port=dst_port,  # swap
        dst_port=src_port,  # swap
        protocol=protocol,
        priority=priority,
    )

    return fwd_handle, rev_handle


def add_deny_rule(
    src_ip: Optional[str] = None,
    dst_ip: Optional[str] = None,
    src_port: Optional[int] = None,
    dst_port: Optional[int] = None,
    protocol: str = "ip",
    priority: int = 100,
    in_port: str = INTERNET_PORT,
) -> tuple[str, str]:
    """Add deny rule (drop action) on both directions.

    Returns (forward_handle, reverse_handle).
    """
    # Forward: drop on in_port
    fwd_handle = _add_drop_rule(
        dev=in_port,
        src_ip=src_ip,
        dst_ip=dst_ip,
        src_port=src_port,
        dst_port=dst_port,
        protocol=protocol,
        priority=priority,
    )

    # Reverse: drop on client port (symmetric)
    rev_handle = _add_drop_rule(
        dev=CLIENT_PORT if in_port == INTERNET_PORT else INTERNET_PORT,
        src_ip=dst_ip,
        dst_ip=src_ip,
        src_port=dst_port,
        dst_port=src_port,
        protocol=protocol,
        priority=priority,
    )

    return fwd_handle, rev_handle


def _add_redirect_rule(
    dev: str,
    target: str,
    src_ip: Optional[str],
    dst_ip: Optional[str],
    src_port: Optional[int],
    dst_port: Optional[int],
    protocol: str,
    priority: int,
) -> str:
    """Add a single tc flower rule with mirred redirect action."""
    cmd = _build_flower_cmd(
        dev=dev,
        src_ip=src_ip,
        dst_ip=dst_ip,
        src_port=src_port,
        dst_port=dst_port,
        protocol=protocol,
        priority=priority,
    )
    cmd += ["action", "mirred", "egress", "redirect", "dev", target]

    with _tc_lock:
        _run_tc(cmd)
        logger.info(f"Added redirect rule on {dev} → {target} (prio {priority})")
        # Get the handle of the just-added rule — must be atomic with add
        handle = _get_last_handle(dev, priority)
    return handle


def _add_drop_rule(
    dev: str,
    src_ip: Optional[str],
    dst_ip: Optional[str],
    src_port: Optional[int],
    dst_port: Optional[int],
    protocol: str,
    priority: int,
) -> str:
    """Add a single tc flower rule with drop action."""
    cmd = _build_flower_cmd(
        dev=dev,
        src_ip=src_ip,
        dst_ip=dst_ip,
        src_port=src_port,
        dst_port=dst_port,
        protocol=protocol,
        priority=priority,
    )
    cmd += ["action", "drop"]

    with _tc_lock:
        _run_tc(cmd)
        logger.info(f"Added drop rule on {dev} (prio {priority})")
        # Get the handle of the just-added rule — must be atomic with add
        handle = _get_last_handle(dev, priority)
    return handle


def _build_flower_cmd(
    dev: str,
    src_ip: Optional[str],
    dst_ip: Optional[str],
    src_port: Optional[int],
    dst_port: Optional[int],
    protocol: str,
    priority: int,
) -> list[str]:
    """Build the tc filter add ... flower ... portion of the command."""
    cmd = ["filter", "add", "dev", dev, "ingress", "protocol", "ip",
           "prio", str(priority), "flower"]

    # IP protocol match (tcp/udp/icmp)
    if protocol and protocol != "any" and protocol != "ip":
        cmd += ["ip_proto", protocol]

    if src_ip:
        cmd += ["src_ip", src_ip]
    if dst_ip:
        cmd += ["dst_ip", dst_ip]
    if src_port and protocol in ("tcp", "udp"):
        cmd += ["src_port", str(src_port)]
    if dst_port and protocol in ("tcp", "udp"):
        cmd += ["dst_port", str(dst_port)]

    return cmd


def _get_last_handle(dev: str, priority: int) -> str:
    """Get the handle of the most recently added filter on dev at priority.

    Returns the handle string (e.g., '0x1') or empty string on failure.
    """
    result = _run_tc(
        ["filter", "show", "dev", dev, "ingress", "prio", str(priority)],
        check=False,
    )
    if result.returncode != 0:
        return ""

    # Parse handles from output — last one is most recently added
    handles = re.findall(r"handle\s+(0x[0-9a-f]+)", result.stdout)
    return handles[-1] if handles else ""


def remove_rule(dev: str, handle: str, priority: int) -> None:
    """Remove a specific tc filter rule by handle."""
    if not handle:
        logger.warning(f"No handle to remove on {dev}")
        return

    with _tc_lock:
        _run_tc([
            "filter", "del", "dev", dev, "ingress",
            "prio", str(priority), "handle", handle, "flower",
        ])
        logger.info(f"Removed rule {handle} from {dev} prio {priority}")


def flush_rules(port: str) -> None:
    """Remove all tc filter rules on a port's ingress."""
    _run_tc(["filter", "del", "dev", port, "ingress"], check=False)
    logger.info(f"Flushed all rules on {port}")


def list_rules(port: str) -> list[TcRuleStats]:
    """List all tc flower rules on a port with stats."""
    result = _run_tc(
        ["filter", "show", "dev", port, "ingress"],
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    return _parse_tc_filter_output(result.stdout)


def get_stats(port: str) -> dict:
    """Get aggregate packet/byte stats for a port."""
    result = _run_tc(
        ["-s", "filter", "show", "dev", port, "ingress"],
        check=False,
    )
    if result.returncode != 0:
        return {"packets": 0, "bytes": 0}

    total_packets = 0
    total_bytes = 0
    for m in re.finditer(r"Sent\s+(\d+)\s+bytes\s+(\d+)\s+pkt", result.stdout):
        total_bytes += int(m.group(1))
        total_packets += int(m.group(2))

    return {"packets": total_packets, "bytes": total_bytes}


def check_in_hw(port: str, handle: str, priority: int) -> bool:
    """Check if a specific rule is offloaded to hardware."""
    result = _run_tc(
        ["filter", "show", "dev", port, "ingress", "prio", str(priority)],
        check=False,
    )
    if result.returncode != 0:
        return False

    # Look for 'in_hw' near the handle
    # tc output shows "in_hw" or "in_hw_count" for offloaded rules
    # Must match "in_hw" but not "not_in_hw"
    import re as _re
    return bool(_re.search(r"(?<!not_)in_hw", result.stdout))


def _parse_tc_filter_output(output: str) -> list[TcRuleStats]:
    """Parse tc filter show output into TcRuleStats objects.

    Example output:
    filter protocol ip pref 100 flower chain 0 handle 0x1
      eth_type ipv4
      src_ip 10.0.0.1
      dst_ip 10.0.0.2
      in_hw in_hw_count 1
        action order 1: mirred (Egress Redirect to device pf0vf3) ...
        Sent 1234 bytes 10 pkt ...
    """
    rules = []
    current_rule = None

    for line in output.split("\n"):
        # New filter line
        handle_match = re.search(
            r"pref\s+(\d+)\s+flower.*handle\s+(0x[0-9a-f]+)", line
        )
        if handle_match:
            if current_rule:
                rules.append(current_rule)
            current_rule = TcRuleStats(
                handle=handle_match.group(2),
                priority=int(handle_match.group(1)),
                protocol="ip",
                in_hw=False,
            )
            continue

        if current_rule is None:
            continue

        # Check for in_hw (but not "not_in_hw")
        if "in_hw" in line and "not_in_hw" not in line:
            current_rule.in_hw = True

        # Match fields
        src_match = re.search(r"src_ip\s+(\S+)", line)
        if src_match:
            current_rule.match_src = src_match.group(1)

        dst_match = re.search(r"dst_ip\s+(\S+)", line)
        if dst_match:
            current_rule.match_dst = dst_match.group(1)

        sport_match = re.search(r"src_port\s+(\d+)", line)
        if sport_match:
            current_rule.match_sport = int(sport_match.group(1))

        dport_match = re.search(r"dst_port\s+(\d+)", line)
        if dport_match:
            current_rule.match_dport = int(dport_match.group(1))

        # Action — check mirred first, "drop" can appear in stats lines
        if "mirred" in line:
            current_rule.action = "redirect"
        elif "action" in line.lower() and "drop" in line and "dropped" not in line:
            current_rule.action = "drop"

        # Stats
        stats_match = re.search(r"Sent\s+(\d+)\s+bytes\s+(\d+)\s+pkt", line)
        if stats_match:
            current_rule.bytes = int(stats_match.group(1))
            current_rule.packets = int(stats_match.group(2))

    if current_rule:
        rules.append(current_rule)

    return rules
