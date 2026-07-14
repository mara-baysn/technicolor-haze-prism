"""Manage tc-flower NAT rules on BF3 DPU eSwitch representor ports.

Uses `tc filter add ... action pedit ... action mirred` to implement:
  - SNAT (egress): rewrite src_ip on Out VF representor
  - DNAT (ingress): rewrite dst_ip on In VF representor
  - Port Forwarding: rewrite dst_ip + dst_port on In VF representor

Port mapping (same as tc_manager):
  pf0vf0 = "internet" side (In VF representor) — uplink facing
  pf0vf3 = "client" side (Out VF representor)  — VM/container facing

SNAT: applied on client port ingress (egress traffic: private->public)
  match src_ip=private → pedit set src_ip=public → mirred redirect to internet port

DNAT: applied on internet port ingress (ingress traffic: public->private)
  match dst_ip=public, dst_port → pedit set dst_ip=private → mirred redirect to client port

Port Forward: same as DNAT but also rewrites dst_port
  match dst_ip=public, dst_port=pub_port → pedit set dst_ip=private, dst_port=priv_port
  → mirred redirect to client port

SNAT Modes:
  - "static": Fixed 1:1 IP mapping (original behavior). Single tc rule rewrites
    src_ip with no port tracking.
  - "masquerade": Many-to-one NAT with ephemeral port allocation. Installs a
    tc pedit rule for IP rewrite; port allocation tracked in SNATState (kernel
    conntrack handles actual per-packet port rewriting via ct action).
    In production DOCA Flow, port allocation happens in hardware CT offload.
"""

import hashlib
import logging
import threading
import time
import uuid
from typing import Dict, List, Optional

from .models import NATEntry
from .snat_state import snat_state
from .tc_manager import (
    INTERNET_PORT,
    CLIENT_PORT,
    TcError,
    _run_tc,
    _get_last_handle,
    _tc_lock,
    check_in_hw,
)

logger = logging.getLogger(__name__)

# NAT priority band (separate from firewall rules which default to prio 100)
NAT_PRIORITY = 20

# Valid SNAT modes
SNAT_MODE_STATIC = "static"
SNAT_MODE_MASQUERADE = "masquerade"

# In-memory NAT rule store
nat_db: Dict[str, NATEntry] = {}

# NAT idempotency index: hash(nat rule signature) -> rule_id
_nat_signature_index: Dict[str, str] = {}


def _compute_nat_signature(nat_type: str, **kwargs) -> str:
    """Compute a stable hash for NAT rule deduplication.

    SNAT key: (type, private_ip, public_ip)
    DNAT key: (type, public_ip, public_port, private_ip, private_port, protocol)
    Forward key: (type, public_ip, public_port, private_ip, private_port, protocol)
    """
    if nat_type == "snat":
        sig = f"snat|{kwargs.get('private_ip', '')}|{kwargs.get('public_ip', '')}"
    else:
        sig = (
            f"{nat_type}|{kwargs.get('public_ip', '')}|{kwargs.get('public_port', '')}|"
            f"{kwargs.get('private_ip', '')}|{kwargs.get('private_port', '')}|"
            f"{kwargs.get('protocol', 'tcp')}"
        )
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def _find_existing_nat(nat_type: str, **kwargs) -> Optional[NATEntry]:
    """Check if an equivalent NAT rule already exists.

    Returns the existing NATEntry if found, None otherwise.
    """
    sig = _compute_nat_signature(nat_type, **kwargs)
    existing_id = _nat_signature_index.get(sig)
    if existing_id and existing_id in nat_db:
        return nat_db[existing_id]
    return None


def add_snat(
    private_ip: str,
    public_ip: str,
    mode: str = SNAT_MODE_STATIC,
    comment: Optional[str] = None,
) -> NATEntry:
    """Add source NAT rule (egress: private IP -> public IP).

    Applied on CLIENT_PORT ingress (captures traffic leaving the tenant VM).
    Rewrites src_ip from private to public, then redirects to INTERNET_PORT.

    Modes:
      - "static": Fixed 1:1 IP mapping. One tc rule, no port tracking.
      - "masquerade": Many-to-one NAT. Installs tc pedit rule for IP rewrite;
        port allocation tracked in SNATState. For PoC, kernel conntrack handles
        per-packet port rewriting. Production DOCA Flow uses hardware CT.

    tc filter add dev pf0vf3 ingress protocol ip prio 20 \
      flower src_ip <private_ip> \
      action pedit ex munge ip src set <public_ip> \
      action mirred egress redirect dev pf0vf0
    """
    if mode not in (SNAT_MODE_STATIC, SNAT_MODE_MASQUERADE):
        raise TcError(f"Invalid SNAT mode: {mode}. Must be 'static' or 'masquerade'")

    # --- Idempotency check ---
    existing = _find_existing_nat("snat", private_ip=private_ip, public_ip=public_ip)
    if existing:
        logger.info(f"Idempotent SNAT hit: returning existing rule {existing.id}")
        return existing

    # For masquerade mode, check pool exhaustion before installing tc rule
    if mode == SNAT_MODE_MASQUERADE and snat_state.is_exhausted(public_ip):
        raise TcError(
            f"Port pool exhausted for {public_ip}: cannot accept new masquerade SNAT rule"
        )

    rule_id = str(uuid.uuid4())[:8]

    cmd = [
        "filter", "add", "dev", CLIENT_PORT, "ingress",
        "protocol", "ip", "prio", str(NAT_PRIORITY),
        "flower", "src_ip", private_ip,
        "action", "pedit", "ex", "munge", "ip", "src", "set", public_ip,
        "action", "mirred", "egress", "redirect", "dev", INTERNET_PORT,
    ]

    with _tc_lock:
        _run_tc(cmd)
        logger.info(
            f"Added SNAT rule {rule_id}: {private_ip} -> {public_ip} (mode={mode})"
        )
        # Get handle and check hw offload — must be atomic with add
        handle = _get_last_handle(CLIENT_PORT, NAT_PRIORITY)
        in_hw = check_in_hw(CLIENT_PORT, handle, NAT_PRIORITY)

    entry = NATEntry(
        id=rule_id,
        type="snat",
        mode=mode,
        public_ip=public_ip,
        private_ip=private_ip,
        in_hw=in_hw,
        created_at=time.time(),
    )
    nat_db[rule_id] = entry

    # Update idempotency index
    sig = _compute_nat_signature("snat", private_ip=private_ip, public_ip=public_ip)
    _nat_signature_index[sig] = rule_id

    return entry


def allocate_masquerade_port(
    public_ip: str,
    src_ip: str,
    dst_ip: str,
    dst_port: int,
    protocol: str = "tcp",
) -> Optional[int]:
    """Allocate an ephemeral port for a masquerade SNAT connection.

    Called when a new outbound connection is detected for a public IP
    that has a masquerade SNAT rule. Returns the allocated source port,
    or None if the pool is exhausted.

    Args:
        public_ip: The public IP being NATed to.
        src_ip: Original private source IP.
        dst_ip: Destination IP of the outbound connection.
        dst_port: Destination port of the outbound connection.
        protocol: Protocol (tcp/udp).

    Returns:
        Allocated port number, or None if pool is exhausted.
    """
    port = snat_state.allocate_port(
        public_ip=public_ip,
        src_ip=src_ip,
        dst_ip=dst_ip,
        dst_port=dst_port,
        protocol=protocol,
    )
    if port is None:
        logger.error(
            f"Port allocation failed for masquerade SNAT {src_ip} -> "
            f"{public_ip} (dst={dst_ip}:{dst_port}/{protocol})"
        )
    return port


def release_masquerade_port(public_ip: str, port: int) -> bool:
    """Release a masquerade SNAT port allocation.

    Called when a connection terminates or times out.

    Args:
        public_ip: The public IP the port belongs to.
        port: The port number to release.

    Returns:
        True if released, False if port was not allocated.
    """
    return snat_state.release_port(public_ip, port)


def get_snat_pool_stats(public_ip: Optional[str] = None) -> List[dict]:
    """Get SNAT port pool statistics.

    Args:
        public_ip: If specified, get stats for just this IP.
                   If None, get stats for all tracked public IPs.

    Returns:
        List of pool stats dicts with utilization info.
    """
    stats = snat_state.get_pool_stats(public_ip)
    return [
        {
            "public_ip": s.public_ip,
            "total_ports": s.total_ports,
            "used_ports": s.used_ports,
            "free_ports": s.free_ports,
            "utilization_pct": s.utilization_pct,
            "warning": s.warning,
            "exhausted": s.exhausted,
        }
        for s in stats
    ]


def add_dnat(
    public_ip: str,
    public_port: int,
    private_ip: str,
    private_port: int,
    protocol: str = "tcp",
    comment: Optional[str] = None,
) -> NATEntry:
    """Add destination NAT rule (ingress: public IP:port -> private IP:port).

    Applied on INTERNET_PORT ingress (captures traffic arriving from internet).
    Rewrites dst_ip from public to private, then redirects to CLIENT_PORT.

    tc filter add dev pf0vf0 ingress protocol ip prio 20 \
      flower dst_ip <public_ip> ip_proto <proto> dst_port <public_port> \
      action pedit ex munge ip dst set <private_ip> \
      action mirred egress redirect dev pf0vf3
    """
    # --- Idempotency check ---
    existing = _find_existing_nat(
        "dnat", public_ip=public_ip, public_port=public_port,
        private_ip=private_ip, private_port=private_port, protocol=protocol,
    )
    if existing:
        logger.info(f"Idempotent DNAT hit: returning existing rule {existing.id}")
        return existing

    rule_id = str(uuid.uuid4())[:8]

    cmd = [
        "filter", "add", "dev", INTERNET_PORT, "ingress",
        "protocol", "ip", "prio", str(NAT_PRIORITY),
        "flower", "dst_ip", public_ip,
        "ip_proto", protocol,
        "dst_port", str(public_port),
        "action", "pedit", "ex", "munge", "ip", "dst", "set", private_ip,
        "action", "mirred", "egress", "redirect", "dev", CLIENT_PORT,
    ]

    with _tc_lock:
        _run_tc(cmd)
        logger.info(
            f"Added DNAT rule {rule_id}: {public_ip}:{public_port} -> "
            f"{private_ip}:{private_port} ({protocol})"
        )
        # Get handle and check hw offload — must be atomic with add
        handle = _get_last_handle(INTERNET_PORT, NAT_PRIORITY)
        in_hw = check_in_hw(INTERNET_PORT, handle, NAT_PRIORITY)

    entry = NATEntry(
        id=rule_id,
        type="dnat",
        public_ip=public_ip,
        public_port=public_port,
        private_ip=private_ip,
        private_port=private_port,
        protocol=protocol,
        in_hw=in_hw,
        created_at=time.time(),
    )
    nat_db[rule_id] = entry

    # Update idempotency index
    sig = _compute_nat_signature(
        "dnat", public_ip=public_ip, public_port=public_port,
        private_ip=private_ip, private_port=private_port, protocol=protocol,
    )
    _nat_signature_index[sig] = rule_id

    return entry


def add_port_forward(
    public_ip: str,
    public_port: int,
    private_ip: str,
    private_port: int,
    protocol: str = "tcp",
    comment: Optional[str] = None,
) -> NATEntry:
    """Add port forwarding rule (public:port -> private:different_port).

    Same as DNAT but also rewrites the destination port. Applied on
    INTERNET_PORT ingress.

    tc filter add dev pf0vf0 ingress protocol ip prio 20 \
      flower dst_ip <public_ip> ip_proto <proto> dst_port <public_port> \
      action pedit ex munge ip dst set <private_ip> \
      action pedit ex munge <proto> dport set <private_port> \
      action mirred egress redirect dev pf0vf3
    """
    # --- Idempotency check ---
    existing = _find_existing_nat(
        "forward", public_ip=public_ip, public_port=public_port,
        private_ip=private_ip, private_port=private_port, protocol=protocol,
    )
    if existing:
        logger.info(f"Idempotent port forward hit: returning existing rule {existing.id}")
        return existing

    rule_id = str(uuid.uuid4())[:8]

    cmd = [
        "filter", "add", "dev", INTERNET_PORT, "ingress",
        "protocol", "ip", "prio", str(NAT_PRIORITY),
        "flower", "dst_ip", public_ip,
        "ip_proto", protocol,
        "dst_port", str(public_port),
        "action", "pedit", "ex", "munge", "ip", "dst", "set", private_ip,
        "action", "pedit", "ex", "munge", protocol, "dport", "set", str(private_port),
        "action", "mirred", "egress", "redirect", "dev", CLIENT_PORT,
    ]

    with _tc_lock:
        _run_tc(cmd)
        logger.info(
            f"Added port forward rule {rule_id}: {public_ip}:{public_port} -> "
            f"{private_ip}:{private_port} ({protocol})"
        )
        # Get handle and check hw offload — must be atomic with add
        handle = _get_last_handle(INTERNET_PORT, NAT_PRIORITY)
        in_hw = check_in_hw(INTERNET_PORT, handle, NAT_PRIORITY)

    entry = NATEntry(
        id=rule_id,
        type="forward",
        public_ip=public_ip,
        public_port=public_port,
        private_ip=private_ip,
        private_port=private_port,
        protocol=protocol,
        in_hw=in_hw,
        created_at=time.time(),
    )
    nat_db[rule_id] = entry

    # Update idempotency index
    sig = _compute_nat_signature(
        "forward", public_ip=public_ip, public_port=public_port,
        private_ip=private_ip, private_port=private_port, protocol=protocol,
    )
    _nat_signature_index[sig] = rule_id

    return entry


def remove_nat(rule_id: str) -> None:
    """Remove a NAT rule by ID.

    Flushes the NAT priority band on the relevant port and re-applies
    remaining rules. For simplicity in the PoC, we flush all NAT rules
    on the affected port and re-add the survivors.

    In production (DOCA Flow), each CT entry has a unique handle and can
    be removed individually without affecting others.
    """
    if rule_id not in nat_db:
        raise TcError(f"NAT rule {rule_id} not found")

    entry = nat_db[rule_id]

    # Clean up idempotency index
    stale_keys = [k for k, v in _nat_signature_index.items() if v == rule_id]
    for k in stale_keys:
        del _nat_signature_index[k]

    # Determine which port this rule is on
    if entry.type == "snat":
        port = CLIENT_PORT
    else:
        port = INTERNET_PORT

    with _tc_lock:
        # Remove the entry from our DB
        del nat_db[rule_id]

        # Flush all NAT-priority rules on that port and re-apply survivors
        _run_tc(["filter", "del", "dev", port, "ingress", "prio", str(NAT_PRIORITY)], check=False)

        # Re-apply remaining rules on the same port
        for remaining in nat_db.values():
            if remaining.type == "snat" and port == CLIENT_PORT:
                _reapply_snat(remaining)
            elif remaining.type in ("dnat", "forward") and port == INTERNET_PORT:
                _reapply_nat_rule(remaining)

    logger.info(f"Removed NAT rule {rule_id} (type={entry.type})")


def list_nat() -> List[NATEntry]:
    """List all NAT rules with current stats."""
    return list(nat_db.values())


def flush_nat() -> int:
    """Remove all NAT rules. Returns count of rules removed."""
    count = len(nat_db)

    # Flush NAT priority on both ports
    _run_tc(
        ["filter", "del", "dev", INTERNET_PORT, "ingress", "prio", str(NAT_PRIORITY)],
        check=False,
    )
    _run_tc(
        ["filter", "del", "dev", CLIENT_PORT, "ingress", "prio", str(NAT_PRIORITY)],
        check=False,
    )

    nat_db.clear()
    _nat_signature_index.clear()
    logger.warning(f"NAT FLUSH: removed all {count} NAT rules")
    return count


def _reapply_snat(entry: NATEntry) -> None:
    """Re-apply an SNAT rule after a flush (internal helper)."""
    cmd = [
        "filter", "add", "dev", CLIENT_PORT, "ingress",
        "protocol", "ip", "prio", str(NAT_PRIORITY),
        "flower", "src_ip", entry.private_ip,
        "action", "pedit", "ex", "munge", "ip", "src", "set", entry.public_ip,
        "action", "mirred", "egress", "redirect", "dev", INTERNET_PORT,
    ]
    _run_tc(cmd, check=False)


def _reapply_nat_rule(entry: NATEntry) -> None:
    """Re-apply a DNAT or port forward rule after a flush (internal helper)."""
    protocol = entry.protocol or "tcp"

    if entry.type == "forward" and entry.public_port and entry.private_port:
        cmd = [
            "filter", "add", "dev", INTERNET_PORT, "ingress",
            "protocol", "ip", "prio", str(NAT_PRIORITY),
            "flower", "dst_ip", entry.public_ip,
            "ip_proto", protocol,
            "dst_port", str(entry.public_port),
            "action", "pedit", "ex", "munge", "ip", "dst", "set", entry.private_ip,
            "action", "pedit", "ex", "munge", protocol, "dport", "set", str(entry.private_port),
            "action", "mirred", "egress", "redirect", "dev", CLIENT_PORT,
        ]
    else:
        cmd = [
            "filter", "add", "dev", INTERNET_PORT, "ingress",
            "protocol", "ip", "prio", str(NAT_PRIORITY),
            "flower", "dst_ip", entry.public_ip,
            "ip_proto", protocol,
            "dst_port", str(entry.public_port),
            "action", "pedit", "ex", "munge", "ip", "dst", "set", entry.private_ip,
            "action", "mirred", "egress", "redirect", "dev", CLIENT_PORT,
        ]
    _run_tc(cmd, check=False)
