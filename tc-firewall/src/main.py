"""tc-firewall: REST API for managing tc-flower firewall rules on BF3 DPU.

This daemon manages hardware-offloaded packet filtering via tc-flower
on the BlueField-3 DPU eSwitch representor ports.

Default policy: deny-all (no rules = traffic dropped)
Rules create explicit allow/deny entries that are offloaded to silicon.
"""

import hashlib
import logging
import time
from contextlib import asynccontextmanager
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field as PydanticField

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
    TenantConfig,
)
from . import tc_manager
from . import conntrack
from . import nat_manager
from .audit import AuditLogger
from .ha import HAManager, HAState, get_ha_manager, init_ha_manager
from .tenants import tenant_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# In-memory rule store (keyed by rule ID)
rules_db: Dict[str, FirewallRule] = {}
start_time: float = 0.0
default_policy: DefaultPolicy = DefaultPolicy.DENY

# Idempotency indexes: hash(rule signature) -> rule_id, idempotency_key -> rule_id
_rule_signature_index: Dict[str, str] = {}
_idempotency_key_index: Dict[str, str] = {}

# Structured audit logger
audit = AuditLogger()

# NAT entries store (keyed by entry ID, for tenant scoping)
nat_entries_db: Dict[str, NATEntry] = {}


# --- Tenant helpers ---


def get_tenant_id(request: Request) -> str:
    """Extract and validate X-Tenant-ID header from request.

    Returns the tenant_id string.
    Raises HTTPException(400) if header missing.
    Raises HTTPException(403) if tenant not registered.
    """
    tenant_id = request.headers.get("X-Tenant-ID")
    if not tenant_id:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID header is required",
        )
    if not tenant_manager.tenant_exists(tenant_id):
        raise HTTPException(
            status_code=403,
            detail=f"Tenant '{tenant_id}' is not registered",
        )
    return tenant_id


def count_tenant_rules(tenant_id: str) -> int:
    """Count firewall rules belonging to a tenant."""
    return sum(1 for r in rules_db.values() if r.tenant_id == tenant_id)


def count_tenant_nat_entries(tenant_id: str) -> int:
    """Count NAT entries belonging to a tenant."""
    return sum(1 for e in nat_entries_db.values() if e.tenant_id == tenant_id)


# --- Request models for tenant management ---


class TenantRegistrationRequest(BaseModel):
    """Request body for registering a new tenant."""
    tenant_id: str
    public_ips: list[str] = PydanticField(default_factory=list)
    max_rules: int = 100
    max_nat_entries: int = 50


# --- Idempotency helpers ---


def _compute_rule_signature(tenant_id: str, request: FirewallRuleRequest) -> str:
    """Compute a stable hash from the rule's matching fields.

    Two requests with the same (src_ip, dst_ip, src_port, dst_port, protocol,
    action, priority) are considered equivalent tc-flower entries.
    """
    sig_str = (
        f"{tenant_id}|{request.src_ip or ''}|{request.dst_ip or ''}|"
        f"{request.src_port or ''}|{request.dst_port or ''}|"
        f"{request.protocol.value}|{request.action.value}|{request.priority}"
    )
    return hashlib.sha256(sig_str.encode()).hexdigest()[:16]


def _find_existing_rule(tenant_id: str, request: FirewallRuleRequest):
    """Check if an equivalent rule already exists.

    Returns the existing FirewallRule if found, None otherwise.
    Checks idempotency_key first (if provided), then signature hash.
    """
    # Check by idempotency_key first
    if request.idempotency_key:
        existing_id = _idempotency_key_index.get(request.idempotency_key)
        if existing_id and existing_id in rules_db:
            return rules_db[existing_id]

    # Check by rule signature (same port, protocol, action, priority)
    sig = _compute_rule_signature(tenant_id, request)
    existing_id = _rule_signature_index.get(sig)
    if existing_id and existing_id in rules_db:
        return rules_db[existing_id]

    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize tc qdiscs on startup."""
    global start_time
    start_time = time.time()

    logger.info("tc-firewall starting up — initializing eSwitch ports")
    audit.log_daemon_start()
    try:
        tc_manager.ensure_ingress_qdisc(tc_manager.INTERNET_PORT)
        tc_manager.ensure_ingress_qdisc(tc_manager.CLIENT_PORT)
        logger.info("Ingress qdiscs ready on pf0vf0 and pf0vf3")
    except Exception as e:
        logger.warning(f"Failed to setup qdiscs (expected if not on DPU): {e}")

    yield

    audit.log_daemon_stop()
    logger.info("tc-firewall shutting down")


app = FastAPI(
    title="tc-firewall",
    description="Hardware-offloaded firewall for BF3 DPU via tc-flower",
    version="0.1.0",
    lifespan=lifespan,
)


# --- Tenant Management Endpoints ---


@app.post("/tenants", status_code=201)
async def register_tenant(request: TenantRegistrationRequest):
    """Register a new tenant with quota configuration."""
    try:
        config = tenant_manager.register_tenant(
            tenant_id=request.tenant_id,
            public_ips=request.public_ips,
            max_rules=request.max_rules,
            max_nat_entries=request.max_nat_entries,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    logger.info(f"Registered tenant '{request.tenant_id}'")
    return config


@app.get("/tenants")
async def list_tenants():
    """List all registered tenants."""
    tenants = tenant_manager.list_tenants()
    return {
        "tenants": tenants,
        "count": len(tenants),
    }


@app.get("/tenants/{tenant_id}")
async def get_tenant(tenant_id: str):
    """Get tenant configuration and current resource usage."""
    config = tenant_manager.get_tenant(tenant_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    rule_count = count_tenant_rules(tenant_id)
    nat_count = count_tenant_nat_entries(tenant_id)

    return {
        **config.model_dump(),
        "usage": {
            "rules": rule_count,
            "rules_remaining": config.max_rules - rule_count,
            "nat_entries": nat_count,
            "nat_entries_remaining": config.max_nat_entries - nat_count,
        },
    }


@app.delete("/tenants/{tenant_id}")
async def delete_tenant(tenant_id: str):
    """Deregister a tenant and flush all their rules."""
    config = tenant_manager.get_tenant(tenant_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    # Flush all firewall rules belonging to this tenant
    tenant_rule_ids = [
        rid for rid, rule in rules_db.items() if rule.tenant_id == tenant_id
    ]
    for rule_id in tenant_rule_ids:
        rule = rules_db[rule_id]
        try:
            if rule.tc_handle_fwd:
                tc_manager.remove_rule(
                    tc_manager.INTERNET_PORT, rule.tc_handle_fwd, rule.priority
                )
            if rule.tc_handle_rev:
                tc_manager.remove_rule(
                    tc_manager.CLIENT_PORT, rule.tc_handle_rev, rule.priority
                )
        except tc_manager.TcError as e:
            logger.warning(f"Error removing tc rule during tenant flush: {e}")
        del rules_db[rule_id]

    # Flush all NAT entries belonging to this tenant
    tenant_nat_ids = [
        nid for nid, entry in nat_entries_db.items() if entry.tenant_id == tenant_id
    ]
    for nat_id in tenant_nat_ids:
        try:
            nat_manager.remove_nat(nat_id)
        except Exception as e:
            logger.warning(f"Error removing NAT rule during tenant flush: {e}")
        if nat_id in nat_entries_db:
            del nat_entries_db[nat_id]

    # Clean up idempotency indexes for deleted rules
    stale_sig_keys = [
        k for k, v in _rule_signature_index.items() if v in tenant_rule_ids
    ]
    for k in stale_sig_keys:
        del _rule_signature_index[k]
    stale_idem_keys = [
        k for k, v in _idempotency_key_index.items() if v in tenant_rule_ids
    ]
    for k in stale_idem_keys:
        del _idempotency_key_index[k]

    # Remove the tenant registration
    tenant_manager.delete_tenant(tenant_id)

    logger.warning(
        f"Deleted tenant '{tenant_id}': flushed {len(tenant_rule_ids)} rules, "
        f"{len(tenant_nat_ids)} NAT entries"
    )
    return {
        "status": "deleted",
        "tenant_id": tenant_id,
        "rules_flushed": len(tenant_rule_ids),
        "nat_entries_flushed": len(tenant_nat_ids),
    }


# --- Health / Metrics (no tenant scoping needed) ---


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "uptime_seconds": round(time.time() - start_time, 1),
        "active_rules": len(rules_db),
        "default_policy": default_policy.value,
    }


# --- Firewall Rule Endpoints (tenant-scoped) ---


@app.get("/rules")
async def list_rules(request: Request):
    """List firewall rules scoped to the requesting tenant."""
    tenant_id = get_tenant_id(request)

    tenant_rules = [r for r in rules_db.values() if r.tenant_id == tenant_id]
    return {
        "rules": tenant_rules,
        "count": len(tenant_rules),
        "default_policy": default_policy.value,
    }


@app.post("/rules", status_code=201)
async def add_rule(request: Request, rule_request: FirewallRuleRequest):
    """Add a new firewall rule scoped to the requesting tenant.

    Creates bidirectional tc-flower rules on the eSwitch ports.
    ALLOW rules use mirred redirect; DENY rules use drop action.

    Idempotent: if an equivalent rule already exists (same src/dst IP,
    src/dst port, protocol, action, priority), returns the existing rule
    with 200 OK and "already_exists": true. If an idempotency_key is
    provided, deduplication is performed by key first.
    """
    tenant_id = get_tenant_id(request)

    # --- Idempotency check ---
    existing = _find_existing_rule(tenant_id, rule_request)
    if existing:
        logger.info(
            f"Idempotent hit: returning existing rule {existing.id} "
            f"instead of creating duplicate"
        )
        response_data = existing.model_dump()
        response_data["already_exists"] = True
        return JSONResponse(status_code=200, content=response_data)

    # Check quota
    config = tenant_manager.get_tenant(tenant_id)
    current_count = count_tenant_rules(tenant_id)
    if current_count >= config.max_rules:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Tenant '{tenant_id}' has reached the maximum of "
                f"{config.max_rules} firewall rules"
            ),
        )

    # Create the rule object with tenant association
    rule = FirewallRule(
        tenant_id=tenant_id,
        src_ip=rule_request.src_ip,
        dst_ip=rule_request.dst_ip,
        src_port=rule_request.src_port,
        dst_port=rule_request.dst_port,
        protocol=rule_request.protocol,
        action=rule_request.action,
        priority=rule_request.priority,
        comment=rule_request.comment,
    )

    # Map protocol enum to tc protocol string
    proto_str = rule_request.protocol.value if rule_request.protocol != Protocol.ANY else "ip"

    try:
        if rule_request.action == RuleAction.ALLOW:
            fwd_handle, rev_handle = tc_manager.add_allow_rule(
                src_ip=rule_request.src_ip,
                dst_ip=rule_request.dst_ip,
                src_port=rule_request.src_port,
                dst_port=rule_request.dst_port,
                protocol=proto_str,
                priority=rule_request.priority,
            )
        else:
            fwd_handle, rev_handle = tc_manager.add_deny_rule(
                src_ip=rule_request.src_ip,
                dst_ip=rule_request.dst_ip,
                src_port=rule_request.src_port,
                dst_port=rule_request.dst_port,
                protocol=proto_str,
                priority=rule_request.priority,
            )

        rule.tc_handle_fwd = fwd_handle
        rule.tc_handle_rev = rev_handle

        # Check hw offload status
        rule.in_hw = tc_manager.check_in_hw(
            tc_manager.INTERNET_PORT, fwd_handle, rule_request.priority
        )

    except tc_manager.TcError as e:
        raise HTTPException(status_code=500, detail=f"tc command failed: {e}")

    # Store the rule
    rules_db[rule.id] = rule

    # Update idempotency indexes
    sig = _compute_rule_signature(tenant_id, rule_request)
    _rule_signature_index[sig] = rule.id
    if rule_request.idempotency_key:
        _idempotency_key_index[rule_request.idempotency_key] = rule.id

    logger.info(
        f"[tenant={tenant_id}] Added rule {rule.id}: {rule.action.value} "
        f"{rule.src_ip or '*'}:{rule.src_port or '*'} -> "
        f"{rule.dst_ip or '*'}:{rule.dst_port or '*'} "
        f"[in_hw={rule.in_hw}]"
    )

    source_ip = request.client.host if request.client else None
    audit.log_rule_created(
        rule_id=rule.id,
        details={
            "src_ip": rule_request.src_ip,
            "dst_ip": rule_request.dst_ip,
            "src_port": rule_request.src_port,
            "dst_port": rule_request.dst_port,
            "protocol": rule_request.protocol.value,
            "action": rule_request.action.value,
            "priority": rule_request.priority,
        },
        source_ip=source_ip,
    )

    return rule


@app.delete("/rules/{rule_id}")
async def delete_rule(request: Request, rule_id: str):
    """Remove a firewall rule by ID (must belong to requesting tenant)."""
    tenant_id = get_tenant_id(request)

    if rule_id not in rules_db:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")

    rule = rules_db[rule_id]

    # Enforce tenant isolation
    if rule.tenant_id != tenant_id:
        raise HTTPException(
            status_code=403,
            detail=f"Rule {rule_id} does not belong to tenant '{tenant_id}'",
        )

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

    # Clean up idempotency indexes
    stale_sig_keys = [k for k, v in _rule_signature_index.items() if v == rule_id]
    for k in stale_sig_keys:
        del _rule_signature_index[k]
    stale_idem_keys = [k for k, v in _idempotency_key_index.items() if v == rule_id]
    for k in stale_idem_keys:
        del _idempotency_key_index[k]

    del rules_db[rule_id]
    logger.info(f"[tenant={tenant_id}] Deleted rule {rule_id}")

    source_ip = request.client.host if request.client else None
    audit.log_rule_deleted(rule_id=rule_id, source_ip=source_ip)

    return {"status": "deleted", "rule_id": rule_id}


@app.post("/rules/flush")
async def flush_rules(request: Request):
    """Flush all firewall rules for the requesting tenant."""
    tenant_id = get_tenant_id(request)

    tenant_rule_ids = [
        rid for rid, rule in rules_db.items() if rule.tenant_id == tenant_id
    ]

    for rule_id in tenant_rule_ids:
        rule = rules_db[rule_id]
        try:
            if rule.tc_handle_fwd:
                tc_manager.remove_rule(
                    tc_manager.INTERNET_PORT, rule.tc_handle_fwd, rule.priority
                )
            if rule.tc_handle_rev:
                tc_manager.remove_rule(
                    tc_manager.CLIENT_PORT, rule.tc_handle_rev, rule.priority
                )
        except tc_manager.TcError as e:
            logger.warning(f"Error removing tc rule during flush: {e}")
        del rules_db[rule_id]

    # Clean up idempotency indexes for flushed rules
    stale_sig_keys = [
        k for k, v in _rule_signature_index.items() if v in tenant_rule_ids
    ]
    for k in stale_sig_keys:
        del _rule_signature_index[k]
    stale_idem_keys = [
        k for k, v in _idempotency_key_index.items() if v in tenant_rule_ids
    ]
    for k in stale_idem_keys:
        del _idempotency_key_index[k]

    logger.warning(
        f"[tenant={tenant_id}] FLUSH: removed {len(tenant_rule_ids)} rules"
    )

    source_ip = request.client.host if request.client else None
    audit.log_rules_flushed(count=len(tenant_rule_ids), source_ip=source_ip)

    return {"status": "flushed", "rules_removed": len(tenant_rule_ids)}


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


# --- Audit Endpoint ---


@app.get("/audit")
async def get_audit(limit: int = Query(default=100, ge=1, le=1000)):
    """Retrieve recent audit log entries (newest first)."""
    entries = audit.get_recent(limit=limit)
    return {
        "entries": entries,
        "count": len(entries),
    }


# --- NAT Endpoints (tenant-scoped) ---


@app.post("/nat/snat", status_code=201)
async def add_snat_rule(request: Request, snat_request: SNATRule):
    """Add a source NAT rule (egress: tenant private IP -> public IP).

    Creates a tc-flower rule on the client port (Out VF representor) that
    rewrites src_ip from private to public and redirects to the internet port.
    """
    tenant_id = get_tenant_id(request)

    # Check NAT quota
    config = tenant_manager.get_tenant(tenant_id)
    current_count = count_tenant_nat_entries(tenant_id)
    if current_count >= config.max_nat_entries:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Tenant '{tenant_id}' has reached the maximum of "
                f"{config.max_nat_entries} NAT entries"
            ),
        )

    try:
        entry = nat_manager.add_snat(
            private_ip=snat_request.private_ip,
            public_ip=snat_request.public_ip,
            mode=snat_request.mode,
            comment=snat_request.comment,
        )
    except tc_manager.TcError as e:
        raise HTTPException(status_code=500, detail=f"tc command failed: {e}")

    # Track with tenant association
    entry.tenant_id = tenant_id
    nat_entries_db[entry.id] = entry

    logger.info(
        f"[tenant={tenant_id}] SNAT rule created: "
        f"{snat_request.private_ip} -> {snat_request.public_ip}"
    )

    source_ip = request.client.host if request.client else None
    audit.log_nat_created(
        nat_type="snat",
        rule_id=entry.id,
        details={
            "private_ip": snat_request.private_ip,
            "public_ip": snat_request.public_ip,
        },
        source_ip=source_ip,
    )

    return entry


@app.post("/nat/dnat", status_code=201)
async def add_dnat_rule(request: Request, dnat_request: DNATRule):
    """Add a destination NAT rule (ingress: public IP:port -> private IP:port).

    Creates a tc-flower rule on the internet port (In VF representor) that
    rewrites dst_ip from public to private and redirects to the client port.
    """
    tenant_id = get_tenant_id(request)

    # Check NAT quota
    config = tenant_manager.get_tenant(tenant_id)
    current_count = count_tenant_nat_entries(tenant_id)
    if current_count >= config.max_nat_entries:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Tenant '{tenant_id}' has reached the maximum of "
                f"{config.max_nat_entries} NAT entries"
            ),
        )

    try:
        entry = nat_manager.add_dnat(
            public_ip=dnat_request.public_ip,
            public_port=dnat_request.public_port,
            private_ip=dnat_request.private_ip,
            private_port=dnat_request.private_port,
            protocol=dnat_request.protocol,
            comment=dnat_request.comment,
        )
    except tc_manager.TcError as e:
        raise HTTPException(status_code=500, detail=f"tc command failed: {e}")

    # Track with tenant association
    entry.tenant_id = tenant_id
    nat_entries_db[entry.id] = entry

    logger.info(
        f"[tenant={tenant_id}] DNAT rule created: "
        f"{dnat_request.public_ip}:{dnat_request.public_port} -> "
        f"{dnat_request.private_ip}:{dnat_request.private_port}"
    )

    source_ip = request.client.host if request.client else None
    audit.log_nat_created(
        nat_type="dnat",
        rule_id=entry.id,
        details={
            "public_ip": dnat_request.public_ip,
            "public_port": dnat_request.public_port,
            "private_ip": dnat_request.private_ip,
            "private_port": dnat_request.private_port,
            "protocol": dnat_request.protocol,
        },
        source_ip=source_ip,
    )

    return entry


@app.post("/nat/forward", status_code=201)
async def add_port_forward_rule(request: Request, fwd_request: PortForwardRule):
    """Add a port forwarding rule (public:port -> private:different_port).

    Like DNAT but also rewrites the destination port. Creates a tc-flower rule
    on the internet port with pedit actions for both dst_ip and dst_port.
    """
    tenant_id = get_tenant_id(request)

    # Check NAT quota
    config = tenant_manager.get_tenant(tenant_id)
    current_count = count_tenant_nat_entries(tenant_id)
    if current_count >= config.max_nat_entries:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Tenant '{tenant_id}' has reached the maximum of "
                f"{config.max_nat_entries} NAT entries"
            ),
        )

    try:
        entry = nat_manager.add_port_forward(
            public_ip=fwd_request.public_ip,
            public_port=fwd_request.public_port,
            private_ip=fwd_request.private_ip,
            private_port=fwd_request.private_port,
            protocol=fwd_request.protocol,
            comment=fwd_request.comment,
        )
    except tc_manager.TcError as e:
        raise HTTPException(status_code=500, detail=f"tc command failed: {e}")

    # Track with tenant association
    entry.tenant_id = tenant_id
    nat_entries_db[entry.id] = entry

    logger.info(
        f"[tenant={tenant_id}] Port forward rule created: "
        f"{fwd_request.public_ip}:{fwd_request.public_port} -> "
        f"{fwd_request.private_ip}:{fwd_request.private_port}"
    )

    source_ip = request.client.host if request.client else None
    audit.log_nat_created(
        nat_type="forward",
        rule_id=entry.id,
        details={
            "public_ip": fwd_request.public_ip,
            "public_port": fwd_request.public_port,
            "private_ip": fwd_request.private_ip,
            "private_port": fwd_request.private_port,
            "protocol": fwd_request.protocol,
        },
        source_ip=source_ip,
    )

    return entry


@app.get("/nat")
async def list_nat_rules(request: Request):
    """List NAT rules scoped to the requesting tenant."""
    tenant_id = get_tenant_id(request)

    tenant_entries = [e for e in nat_entries_db.values() if e.tenant_id == tenant_id]
    return {
        "rules": tenant_entries,
        "count": len(tenant_entries),
    }


@app.delete("/nat/{rule_id}")
async def delete_nat_rule(request: Request, rule_id: str):
    """Remove a NAT rule by ID (must belong to requesting tenant)."""
    tenant_id = get_tenant_id(request)

    if rule_id not in nat_entries_db:
        raise HTTPException(status_code=404, detail=f"NAT rule {rule_id} not found")

    entry = nat_entries_db[rule_id]

    # Enforce tenant isolation
    if entry.tenant_id != tenant_id:
        raise HTTPException(
            status_code=403,
            detail=f"NAT rule {rule_id} does not belong to tenant '{tenant_id}'",
        )

    try:
        nat_manager.remove_nat(rule_id)
    except tc_manager.TcError as e:
        raise HTTPException(status_code=500, detail=f"tc command failed: {e}")

    del nat_entries_db[rule_id]
    logger.info(f"[tenant={tenant_id}] Deleted NAT rule {rule_id}")

    source_ip = request.client.host if request.client else None
    audit.log_nat_deleted(rule_id=rule_id, source_ip=source_ip)

    return {"status": "deleted", "rule_id": rule_id}


@app.post("/nat/flush")
async def flush_nat_rules(request: Request):
    """Flush all NAT rules for the requesting tenant."""
    tenant_id = get_tenant_id(request)

    tenant_nat_ids = [
        nid for nid, entry in nat_entries_db.items() if entry.tenant_id == tenant_id
    ]

    for nat_id in tenant_nat_ids:
        try:
            nat_manager.remove_nat(nat_id)
        except Exception as e:
            logger.warning(f"Error removing NAT rule during flush: {e}")
        if nat_id in nat_entries_db:
            del nat_entries_db[nat_id]

    logger.warning(
        f"[tenant={tenant_id}] NAT FLUSH: removed {len(tenant_nat_ids)} rules"
    )

    source_ip = request.client.host if request.client else None
    audit.log_nat_flushed(count=len(tenant_nat_ids), source_ip=source_ip)

    return {"status": "flushed", "rules_removed": len(tenant_nat_ids)}


# --- HA Endpoints ---


@app.get("/ha/status")
async def ha_status():
    """Return current HA status: role, generation, peer health."""
    mgr = get_ha_manager()
    return mgr.get_status()


@app.post("/ha/heartbeat")
async def ha_heartbeat(request: Request):
    """Receive a heartbeat from the HA peer.

    Body: {"generation": int, "role": "ACTIVE"|"STANDBY"|"FENCING"}
    """
    body = await request.json()
    generation = body.get("generation", 0)
    role = body.get("role", "STANDBY")

    mgr = get_ha_manager()
    result = mgr.receive_heartbeat(peer_generation=generation, peer_role=role)

    return {
        "status": "ok",
        "local_role": mgr.role.value,
        "local_generation": mgr.generation,
        **result,
    }


@app.post("/ha/promote")
async def ha_promote():
    """Force promotion to ACTIVE (for testing/maintenance)."""
    mgr = get_ha_manager()
    result = mgr.promote()
    logger.warning(f"HA promote requested: {result}")
    return result


@app.post("/ha/demote")
async def ha_demote():
    """Force demotion to STANDBY (for testing/maintenance)."""
    mgr = get_ha_manager()
    result = mgr.demote()
    logger.warning(f"HA demote requested: {result}")
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8443)
