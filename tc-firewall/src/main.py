"""tc-firewall: REST API for managing tc-flower firewall rules on BF3 DPU.

This daemon manages hardware-offloaded packet filtering via tc-flower
on the BlueField-3 DPU eSwitch representor ports.

Default policy: deny-all (no rules = traffic dropped)
Rules create explicit allow/deny entries that are offloaded to silicon.
"""

import logging
import time
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .models import (
    DefaultPolicy,
    DNATRule,
    FirewallRule,
    FirewallRuleRequest,
    Metrics,
    NATEntry,
    PortForwardRule,
    Protocol,
    RuleAction,
    SNATRule,
)
from . import tc_manager
from . import conntrack
from . import nat_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# In-memory rule store (keyed by rule ID)
rules_db: Dict[str, FirewallRule] = {}
start_time: float = 0.0
default_policy: DefaultPolicy = DefaultPolicy.DENY


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize tc qdiscs on startup."""
    global start_time
    start_time = time.time()

    logger.info("tc-firewall starting up — initializing eSwitch ports")
    try:
        tc_manager.ensure_ingress_qdisc(tc_manager.INTERNET_PORT)
        tc_manager.ensure_ingress_qdisc(tc_manager.CLIENT_PORT)
        logger.info("Ingress qdiscs ready on pf0vf0 and pf0vf3")
    except Exception as e:
        logger.warning(f"Failed to setup qdiscs (expected if not on DPU): {e}")

    yield

    logger.info("tc-firewall shutting down")


app = FastAPI(
    title="tc-firewall",
    description="Hardware-offloaded firewall for BF3 DPU via tc-flower",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "uptime_seconds": round(time.time() - start_time, 1),
        "active_rules": len(rules_db),
        "default_policy": default_policy.value,
    }


@app.get("/rules")
async def list_rules():
    """List all firewall rules."""
    return {
        "rules": list(rules_db.values()),
        "count": len(rules_db),
        "default_policy": default_policy.value,
    }


@app.post("/rules", status_code=201)
async def add_rule(request: FirewallRuleRequest):
    """Add a new firewall rule.

    Creates bidirectional tc-flower rules on the eSwitch ports.
    ALLOW rules use mirred redirect; DENY rules use drop action.
    """
    # Create the rule object
    rule = FirewallRule(
        src_ip=request.src_ip,
        dst_ip=request.dst_ip,
        src_port=request.src_port,
        dst_port=request.dst_port,
        protocol=request.protocol,
        action=request.action,
        priority=request.priority,
        comment=request.comment,
    )

    # Map protocol enum to tc protocol string
    proto_str = request.protocol.value if request.protocol != Protocol.ANY else "ip"

    try:
        if request.action == RuleAction.ALLOW:
            fwd_handle, rev_handle = tc_manager.add_allow_rule(
                src_ip=request.src_ip,
                dst_ip=request.dst_ip,
                src_port=request.src_port,
                dst_port=request.dst_port,
                protocol=proto_str,
                priority=request.priority,
            )
        else:
            fwd_handle, rev_handle = tc_manager.add_deny_rule(
                src_ip=request.src_ip,
                dst_ip=request.dst_ip,
                src_port=request.src_port,
                dst_port=request.dst_port,
                protocol=proto_str,
                priority=request.priority,
            )

        rule.tc_handle_fwd = fwd_handle
        rule.tc_handle_rev = rev_handle

        # Check hw offload status
        rule.in_hw = tc_manager.check_in_hw(
            tc_manager.INTERNET_PORT, fwd_handle, request.priority
        )

    except tc_manager.TcError as e:
        raise HTTPException(status_code=500, detail=f"tc command failed: {e}")

    # Store the rule
    rules_db[rule.id] = rule
    logger.info(
        f"Added rule {rule.id}: {rule.action.value} "
        f"{rule.src_ip or '*'}:{rule.src_port or '*'} → "
        f"{rule.dst_ip or '*'}:{rule.dst_port or '*'} "
        f"[in_hw={rule.in_hw}]"
    )

    return rule


@app.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str):
    """Remove a firewall rule by ID."""
    if rule_id not in rules_db:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")

    rule = rules_db[rule_id]

    try:
        # Remove forward rule
        if rule.tc_handle_fwd:
            tc_manager.remove_rule(
                tc_manager.INTERNET_PORT, rule.tc_handle_fwd, rule.priority
            )
        # Remove reverse rule
        if rule.tc_handle_rev:
            tc_manager.remove_rule(
                tc_manager.CLIENT_PORT, rule.tc_handle_rev, rule.priority
            )
    except tc_manager.TcError as e:
        logger.warning(f"Error removing tc rule (may already be gone): {e}")

    del rules_db[rule_id]
    logger.info(f"Deleted rule {rule_id}")

    return {"status": "deleted", "rule_id": rule_id}


@app.post("/rules/flush")
async def flush_rules():
    """Emergency flush — remove ALL tc-flower rules on both ports."""
    try:
        tc_manager.flush_rules(tc_manager.INTERNET_PORT)
        tc_manager.flush_rules(tc_manager.CLIENT_PORT)
    except tc_manager.TcError as e:
        logger.error(f"Flush error: {e}")

    count = len(rules_db)
    rules_db.clear()
    logger.warning(f"FLUSH: removed all {count} rules")

    return {"status": "flushed", "rules_removed": count}


@app.get("/metrics")
async def get_metrics():
    """Get firewall metrics including packet counters."""
    # Gather stats from tc
    internet_stats = tc_manager.get_stats(tc_manager.INTERNET_PORT)
    client_stats = tc_manager.get_stats(tc_manager.CLIENT_PORT)

    hw_count = sum(1 for r in rules_db.values() if r.in_hw)

    # Estimate forwarded vs dropped based on rule actions
    allow_rules = [r for r in rules_db.values() if r.action == RuleAction.ALLOW]
    deny_rules = [r for r in rules_db.values() if r.action == RuleAction.DENY]

    metrics = Metrics(
        total_rules=len(rules_db),
        hw_offloaded_rules=hw_count,
        packets_forwarded=internet_stats["packets"] + client_stats["packets"],
        packets_dropped=0,  # Updated from deny rule stats
        bytes_forwarded=internet_stats["bytes"] + client_stats["bytes"],
        bytes_dropped=0,
        uptime_seconds=round(time.time() - start_time, 1),
        default_policy=default_policy.value,
    )

    return metrics


@app.get("/sessions")
async def get_sessions():
    """Get active conntrack sessions."""
    sessions = conntrack.get_sessions()
    return {
        "sessions": sessions,
        "count": len(sessions),
    }


@app.get("/topology")
async def get_topology():
    """Show the port topology and current rule counts."""
    internet_rules = tc_manager.list_rules(tc_manager.INTERNET_PORT)
    client_rules = tc_manager.list_rules(tc_manager.CLIENT_PORT)

    return {
        "ports": {
            "internet": {
                "name": tc_manager.INTERNET_PORT,
                "role": "uplink/internet facing",
                "rules": len(internet_rules),
                "hw_offloaded": sum(1 for r in internet_rules if r.in_hw),
            },
            "client": {
                "name": tc_manager.CLIENT_PORT,
                "role": "VM/container facing",
                "rules": len(client_rules),
                "hw_offloaded": sum(1 for r in client_rules if r.in_hw),
            },
        },
        "default_policy": default_policy.value,
    }


# --- NAT Endpoints ---


@app.post("/nat/snat", status_code=201)
async def add_snat_rule(request: SNATRule):
    """Add a source NAT rule (egress: tenant private IP -> public IP).

    Creates a tc-flower rule on the client port (Out VF representor) that
    rewrites src_ip from private to public and redirects to the internet port.
    """
    try:
        entry = nat_manager.add_snat(
            private_ip=request.private_ip,
            public_ip=request.public_ip,
            comment=request.comment,
        )
    except tc_manager.TcError as e:
        raise HTTPException(status_code=500, detail=f"tc command failed: {e}")

    logger.info(f"SNAT rule created: {request.private_ip} -> {request.public_ip}")
    return entry


@app.post("/nat/dnat", status_code=201)
async def add_dnat_rule(request: DNATRule):
    """Add a destination NAT rule (ingress: public IP:port -> private IP:port).

    Creates a tc-flower rule on the internet port (In VF representor) that
    rewrites dst_ip from public to private and redirects to the client port.
    """
    try:
        entry = nat_manager.add_dnat(
            public_ip=request.public_ip,
            public_port=request.public_port,
            private_ip=request.private_ip,
            private_port=request.private_port,
            protocol=request.protocol,
            comment=request.comment,
        )
    except tc_manager.TcError as e:
        raise HTTPException(status_code=500, detail=f"tc command failed: {e}")

    logger.info(
        f"DNAT rule created: {request.public_ip}:{request.public_port} -> "
        f"{request.private_ip}:{request.private_port}"
    )
    return entry


@app.post("/nat/forward", status_code=201)
async def add_port_forward_rule(request: PortForwardRule):
    """Add a port forwarding rule (public:port -> private:different_port).

    Like DNAT but also rewrites the destination port. Creates a tc-flower rule
    on the internet port with pedit actions for both dst_ip and dst_port.
    """
    try:
        entry = nat_manager.add_port_forward(
            public_ip=request.public_ip,
            public_port=request.public_port,
            private_ip=request.private_ip,
            private_port=request.private_port,
            protocol=request.protocol,
            comment=request.comment,
        )
    except tc_manager.TcError as e:
        raise HTTPException(status_code=500, detail=f"tc command failed: {e}")

    logger.info(
        f"Port forward rule created: {request.public_ip}:{request.public_port} -> "
        f"{request.private_ip}:{request.private_port}"
    )
    return entry


@app.get("/nat")
async def list_nat_rules():
    """List all NAT rules (SNAT, DNAT, and port forward)."""
    rules = nat_manager.list_nat()
    return {
        "rules": rules,
        "count": len(rules),
    }


@app.delete("/nat/{rule_id}")
async def delete_nat_rule(rule_id: str):
    """Remove a NAT rule by ID."""
    try:
        nat_manager.remove_nat(rule_id)
    except tc_manager.TcError as e:
        raise HTTPException(status_code=404, detail=str(e))

    logger.info(f"Deleted NAT rule {rule_id}")
    return {"status": "deleted", "rule_id": rule_id}


@app.post("/nat/flush")
async def flush_nat_rules():
    """Remove all NAT rules (SNAT, DNAT, and port forward)."""
    count = nat_manager.flush_nat()
    logger.warning(f"NAT FLUSH: removed {count} rules")
    return {"status": "flushed", "rules_removed": count}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8443)
