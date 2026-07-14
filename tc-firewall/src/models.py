"""Pydantic models for the tc-firewall daemon."""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import uuid
import time


class RuleAction(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class Protocol(str, Enum):
    TCP = "tcp"
    UDP = "udp"
    ICMP = "icmp"
    ANY = "any"


class DefaultPolicy(str, Enum):
    ALLOW = "allow-all"
    DENY = "deny-all"


class FirewallRuleRequest(BaseModel):
    """Request to create a firewall rule."""
    src_ip: Optional[str] = None
    dst_ip: Optional[str] = None
    src_port: Optional[int] = Field(None, ge=1, le=65535)
    dst_port: Optional[int] = Field(None, ge=1, le=65535)
    protocol: Protocol = Protocol.ANY
    action: RuleAction = RuleAction.ALLOW
    priority: int = Field(100, ge=1, le=65535)
    comment: Optional[str] = None
    idempotency_key: Optional[str] = None


class FirewallRule(BaseModel):
    """A firewall rule with its metadata."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    tenant_id: Optional[str] = None
    src_ip: Optional[str] = None
    dst_ip: Optional[str] = None
    src_port: Optional[int] = None
    dst_port: Optional[int] = None
    protocol: Protocol = Protocol.ANY
    action: RuleAction = RuleAction.ALLOW
    priority: int = 100
    comment: Optional[str] = None
    # tc-specific metadata
    tc_handle_fwd: Optional[str] = None  # handle on ingress (internet→client)
    tc_handle_rev: Optional[str] = None  # handle on ingress (client→internet)
    in_hw: bool = False
    created_at: float = Field(default_factory=time.time)
    packets_fwd: int = 0
    bytes_fwd: int = 0
    packets_rev: int = 0
    bytes_rev: int = 0


class Metrics(BaseModel):
    """Firewall metrics."""
    total_rules: int = 0
    hw_offloaded_rules: int = 0
    packets_forwarded: int = 0
    packets_dropped: int = 0
    bytes_forwarded: int = 0
    bytes_dropped: int = 0
    uptime_seconds: float = 0.0
    default_policy: str = "deny-all"


class Session(BaseModel):
    """A conntrack session entry."""
    protocol: str
    src_ip: str
    dst_ip: str
    src_port: Optional[int] = None
    dst_port: Optional[int] = None
    state: Optional[str] = None
    packets: int = 0
    bytes: int = 0
    timeout: Optional[int] = None


# --- NAT Models ---


class SNATRule(BaseModel):
    """Request to create a source NAT rule (egress: private IP -> public IP).

    Modes:
      - "static": Fixed 1:1 IP mapping (default). No port tracking.
      - "masquerade": Many-to-one NAT with ephemeral port allocation.
    """
    private_ip: str
    public_ip: str
    mode: str = "static"  # "static" or "masquerade"
    comment: Optional[str] = None


class DNATRule(BaseModel):
    """Request to create a destination NAT rule (ingress: public IP -> private IP)."""
    public_ip: str
    public_port: int = Field(..., ge=1, le=65535)
    private_ip: str
    private_port: int = Field(..., ge=1, le=65535)
    protocol: str = "tcp"
    comment: Optional[str] = None


class PortForwardRule(BaseModel):
    """Request to create a port forwarding rule (public:port -> private:port)."""
    public_ip: str
    public_port: int = Field(..., ge=1, le=65535)
    private_ip: str
    private_port: int = Field(..., ge=1, le=65535)
    protocol: str = "tcp"
    comment: Optional[str] = None


class NATEntry(BaseModel):
    """A NAT rule entry with metadata and statistics."""
    id: str
    tenant_id: Optional[str] = None
    type: str  # "snat", "dnat", "forward"
    mode: Optional[str] = None  # "static" or "masquerade" (SNAT only)
    public_ip: str
    public_port: Optional[int] = None
    private_ip: str
    private_port: Optional[int] = None
    protocol: Optional[str] = None
    in_hw: bool = False
    packets: int = 0
    bytes: int = 0
    created_at: float


class TenantConfig(BaseModel):
    """Configuration and quota for a tenant."""
    tenant_id: str
    public_ips: list[str] = Field(default_factory=list)
    max_rules: int = 100
    max_nat_entries: int = 50
    created_at: float = Field(default_factory=time.time)
