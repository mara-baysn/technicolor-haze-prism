"""Query kernel conntrack table for session information."""

from __future__ import annotations

import subprocess
import re
import logging
from typing import List, Optional

from .models import Session

logger = logging.getLogger(__name__)


def get_sessions() -> List[Session]:
    """Query conntrack -L and parse entries into Session objects."""
    try:
        result = subprocess.run(
            ["conntrack", "-L", "-o", "extended"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            logger.warning(f"conntrack -L failed: {result.stderr}")
            return []

        return _parse_conntrack_output(result.stdout)
    except FileNotFoundError:
        logger.warning("conntrack command not found — install conntrack-tools")
        return []
    except subprocess.TimeoutExpired:
        logger.error("conntrack -L timed out")
        return []


def _parse_conntrack_output(output: str) -> List[Session]:
    """Parse conntrack -L extended output into Session objects."""
    sessions = []

    for line in output.strip().split("\n"):
        if not line.strip():
            continue

        session = _parse_conntrack_line(line)
        if session:
            sessions.append(session)

    return sessions


def _parse_conntrack_line(line: str) -> Optional[Session]:
    """Parse a single conntrack line.

    Example lines:
    tcp  6 431999 ESTABLISHED src=10.0.0.1 dst=10.0.0.2 sport=54321 dport=80 packets=10 bytes=1000 ...
    udp  17 29 src=10.0.0.1 dst=10.0.0.2 sport=12345 dport=53 packets=2 bytes=200 ...
    """
    try:
        parts = line.split()
        if len(parts) < 4:
            return None

        protocol = parts[0]

        # Extract fields using regex
        src_match = re.search(r"src=(\S+)", line)
        dst_match = re.search(r"dst=(\S+)", line)
        sport_match = re.search(r"sport=(\d+)", line)
        dport_match = re.search(r"dport=(\d+)", line)
        packets_match = re.search(r"packets=(\d+)", line)
        bytes_match = re.search(r"bytes=(\d+)", line)

        if not src_match or not dst_match:
            return None

        # Detect state (TCP only)
        state = None
        tcp_states = [
            "ESTABLISHED", "SYN_SENT", "SYN_RECV", "FIN_WAIT",
            "CLOSE_WAIT", "LAST_ACK", "TIME_WAIT", "CLOSE",
        ]
        for s in tcp_states:
            if s in line:
                state = s
                break

        # Extract timeout (second numeric field for tcp/udp)
        timeout = None
        if len(parts) >= 3:
            try:
                timeout = int(parts[2])
            except ValueError:
                pass

        return Session(
            protocol=protocol,
            src_ip=src_match.group(1),
            dst_ip=dst_match.group(1),
            src_port=int(sport_match.group(1)) if sport_match else None,
            dst_port=int(dport_match.group(1)) if dport_match else None,
            state=state,
            packets=int(packets_match.group(1)) if packets_match else 0,
            bytes=int(bytes_match.group(1)) if bytes_match else 0,
            timeout=timeout,
        )
    except Exception as e:
        logger.debug(f"Failed to parse conntrack line: {e}")
        return None
