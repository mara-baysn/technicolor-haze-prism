#!/bin/bash
# setup_topology.sh — Initialize tc-flower topology on BF3 DPU eSwitch
#
# This script sets up the baseline ingress qdiscs and default allow-all
# forwarding rules on the representor ports.
#
# Port mapping:
#   pf0vf0 = "internet" side (VF0)
#   pf0vf3 = "client" side (VF3)
#
# Usage: sudo ./setup_topology.sh [--deny-all | --allow-all]

set -euo pipefail

INTERNET_PORT="pf0vf0"
CLIENT_PORT="pf0vf3"
DEFAULT_POLICY="${1:---allow-all}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Check we're running as root
if [ "$EUID" -ne 0 ]; then
    log_error "Must run as root (need tc permissions)"
    exit 1
fi

# Verify ports exist
for port in "$INTERNET_PORT" "$CLIENT_PORT"; do
    if ! ip link show "$port" &>/dev/null; then
        log_error "Port $port does not exist — are we on the DPU?"
        exit 1
    fi
done
log_info "Ports verified: $INTERNET_PORT, $CLIENT_PORT"

# Step 1: Add ingress qdiscs (idempotent)
for port in "$INTERNET_PORT" "$CLIENT_PORT"; do
    if tc qdisc show dev "$port" ingress 2>/dev/null | grep -q ingress; then
        log_info "Ingress qdisc already exists on $port"
    else
        tc qdisc add dev "$port" ingress
        log_info "Added ingress qdisc on $port"
    fi
done

# Step 2: Flush existing rules
log_warn "Flushing existing tc filters..."
tc filter del dev "$INTERNET_PORT" ingress 2>/dev/null || true
tc filter del dev "$CLIENT_PORT" ingress 2>/dev/null || true

# Step 3: Apply default policy
if [ "$DEFAULT_POLICY" = "--allow-all" ]; then
    log_info "Applying default policy: ALLOW-ALL (bidirectional redirect)"

    # Forward: pf0vf0 → pf0vf3
    tc filter add dev "$INTERNET_PORT" ingress protocol ip prio 65535 \
        flower \
        action mirred egress redirect dev "$CLIENT_PORT"

    # Reverse: pf0vf3 → pf0vf0
    tc filter add dev "$CLIENT_PORT" ingress protocol ip prio 65535 \
        flower \
        action mirred egress redirect dev "$INTERNET_PORT"

    log_info "Default allow-all rules installed at lowest priority (65535)"

elif [ "$DEFAULT_POLICY" = "--deny-all" ]; then
    log_info "Applying default policy: DENY-ALL (no default rules)"
    log_info "Traffic will be dropped unless explicit ALLOW rules are added"
else
    log_error "Unknown policy: $DEFAULT_POLICY (use --allow-all or --deny-all)"
    exit 1
fi

# Step 4: Verify hardware offload
log_info "Verifying hardware offload status..."
echo ""
echo "=== $INTERNET_PORT ingress filters ==="
tc -s filter show dev "$INTERNET_PORT" ingress
echo ""
echo "=== $CLIENT_PORT ingress filters ==="
tc -s filter show dev "$CLIENT_PORT" ingress

# Check for in_hw
if tc filter show dev "$INTERNET_PORT" ingress | grep -q "in_hw"; then
    log_info "Hardware offload CONFIRMED on $INTERNET_PORT"
else
    log_warn "No in_hw flag detected on $INTERNET_PORT — rules may be in software"
fi

if tc filter show dev "$CLIENT_PORT" ingress | grep -q "in_hw"; then
    log_info "Hardware offload CONFIRMED on $CLIENT_PORT"
else
    log_warn "No in_hw flag detected on $CLIENT_PORT — rules may be in software"
fi

echo ""
log_info "Topology setup complete!"
log_info "Firewall daemon can now manage rules via REST API on :8443"
