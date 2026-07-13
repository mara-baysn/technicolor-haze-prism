# tc-firewall

Hardware-offloaded firewall daemon for BF3 DPU using tc-flower on the eSwitch.

## Overview

Manages tc-flower rules on VF representor ports (pf0vf0, pf0vf3) via a REST API.
Rules are offloaded to the BlueField-3 eSwitch silicon for line-rate (200 Gbps)
packet forwarding with sub-millisecond latency.

## Quick Start

### On the DPU (192.168.0.38)

```bash
# 1. Setup topology (one-time)
sudo ./src/setup_topology.sh --deny-all

# 2. Install dependencies
pip install -e .

# 3. Run the daemon
sudo python -m uvicorn src.main:app --host 0.0.0.0 --port 8443
```

### Deploy from host

```bash
# Copy to DPU
scp -r tc-firewall/ ubuntu@192.168.0.38:~/tc-firewall/

# SSH and run
ssh ubuntu@192.168.0.38
cd ~/tc-firewall
pip install -e .
sudo python -m uvicorn src.main:app --host 0.0.0.0 --port 8443
```

## API Usage

```bash
# Health check
curl http://192.168.0.38:8443/health

# Add allow rule (TCP port 80)
curl -X POST http://192.168.0.38:8443/rules \
  -H "Content-Type: application/json" \
  -d '{"src_ip": "10.0.0.0/24", "dst_port": 80, "protocol": "tcp", "action": "ALLOW"}'

# Add deny rule (block SSH from specific IP)
curl -X POST http://192.168.0.38:8443/rules \
  -H "Content-Type: application/json" \
  -d '{"src_ip": "192.168.1.100", "dst_port": 22, "protocol": "tcp", "action": "DENY", "priority": 50}'

# List rules
curl http://192.168.0.38:8443/rules

# Delete a rule
curl -X DELETE http://192.168.0.38:8443/rules/{rule_id}

# Emergency flush
curl -X POST http://192.168.0.38:8443/rules/flush

# Metrics
curl http://192.168.0.38:8443/metrics

# Active sessions
curl http://192.168.0.38:8443/sessions

# Port topology
curl http://192.168.0.38:8443/topology
```

## Architecture

```
                    BF3 DPU eSwitch
    ┌─────────────────────────────────────────┐
    │                                         │
    │   pf0vf0 (internet)  ←→  pf0vf3 (client) │
    │       │                       │         │
    │   tc ingress              tc ingress     │
    │   ┌─────────┐            ┌─────────┐   │
    │   │ flower  │            │ flower  │   │
    │   │ rules   │            │ rules   │   │
    │   └────┬────┘            └────┬────┘   │
    │        │                      │         │
    │   mirred redirect ←──→ mirred redirect  │
    │        (in_hw)              (in_hw)     │
    └─────────────────────────────────────────┘
                        │
                   tc-firewall daemon
                   (REST API :8443)
```

## Design Decisions

- **subprocess over pyroute2**: `tc` CLI is proven reliable on BF3; pyroute2 netlink has edge cases with eSwitch offload
- **Bidirectional rules**: Each firewall rule creates filters on BOTH ports (forward + reverse)
- **Priority ordering**: Lower number = higher priority (deny at prio 50 beats allow at prio 100)
- **Default deny**: No rules = no traffic flows (implicit drop by eSwitch)
- **Handle tracking**: tc filter handles are stored for reliable rule removal

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Docker

```bash
docker build -t tc-firewall .
docker run --privileged --network host tc-firewall
```

Note: `--privileged` required for tc access, `--network host` for representor ports.
