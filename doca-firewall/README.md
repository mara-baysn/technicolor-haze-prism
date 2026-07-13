# DOCA Flow CT Firewall Daemon

A connection-tracking firewall daemon for BlueField-3 DPU running in DOCA Flow switch mode. Uses hardware-offloaded CT (Connection Tracking) to achieve line-rate forwarding for allowed sessions while performing policy evaluation on the ARM cores for new connections.

## Architecture

```
                     BlueField-3 DPU
    ┌──────────────────────────────────────────────┐
    │                                              │
    │  VF0 (internet) ──┐         ┌── VF3 (client) │
    │                   ▼         ▼                │
    │            ┌─────────────────┐               │
    │            │  ROOT PIPE      │               │
    │            │  (control)      │               │
    │            └────────┬────────┘               │
    │                     ▼                        │
    │            ┌─────────────────┐               │
    │            │   CT PIPE       │               │
    │            │                 │               │
    │    HIT ◄───┤                 ├───► MISS      │
    │    │       └─────────────────┘       │       │
    │    ▼                                 ▼       │
    │  Forward                         RSS to ARM  │
    │  (bypass)                        ┌───────┐   │
    │  VF0↔VF3                         │Policy │   │
    │                                  │Engine │   │
    │                                  └───┬───┘   │
    │                                      │       │
    │                              ALLOW: ct_add   │
    │                              DENY:  drop     │
    └──────────────────────────────────────────────┘
```

## Building

### Prerequisites
- BlueField-3 DPU with DOCA 3.4 installed
- DPDK bundled with DOCA

### Build on DPU

```bash
cd ~/doca-firewall
PKG_CONFIG_PATH=/opt/mellanox/doca/lib/aarch64-linux-gnu/pkgconfig:/opt/mellanox/dpdk/lib/aarch64-linux-gnu/pkgconfig \
  meson setup build
ninja -C build
```

## Running

The daemon requires hugepages and must be run as root:

```bash
# Ensure hugepages are available
echo 2048 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages

# Run with DPDK EAL parameters for switch mode
sudo ./build/doca_firewall \
  -a auxiliary:mlx5_core.sf.4,dv_flow_en=2 \
  -a auxiliary:mlx5_core.sf.5 \
  -- \
  --representor=pf0hpf \
  --representor=pf0vf0 \
  --representor=pf0vf1 \
  --representor=pf0vf2 \
  --representor=pf0vf3
```

## REST API

The daemon exposes a REST API on port 8443 for runtime management.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /health | Health check |
| GET | /metrics | Firewall counters and session stats |
| GET | /rules | List all policy rules |
| POST | /rules | Add a new policy rule |
| DELETE | /rules/:id | Remove a policy rule |

### Add a Rule

```bash
curl -X POST http://192.168.0.38:8443/rules \
  -H "Content-Type: application/json" \
  -d '{
    "src_ip": "10.0.0.0/8",
    "dst_ip": "0.0.0.0/0",
    "dst_port_min": 22,
    "dst_port_max": 22,
    "protocol": 6,
    "action": "ALLOW",
    "priority": 5
  }'
```

### Get Metrics

```bash
curl http://192.168.0.38:8443/metrics
```

### Default Rules

The daemon starts with these default ALLOW rules:
- TCP port 80 (HTTP)
- TCP port 443 (HTTPS)
- ICMP (ping)
- UDP port 53 (DNS)

All other traffic is denied by default.

## Port Mapping

| Port ID | Representor | Role |
|---------|-------------|------|
| 0 | pf0hpf | Uplink PF |
| 1 | pf0vf0 | Internet facing |
| 2 | pf0vf1 | Firewall in (optional) |
| 3 | pf0vf2 | Firewall out (optional) |
| 4 | pf0vf3 | Client facing |

## Key Design Decisions

1. **Switch mode**: All traffic flows through the eSwitch, controlled by the DPU ARM.
2. **CT HIT bypass**: Established sessions are forwarded in hardware without ARM involvement.
3. **CT MISS to ARM**: New connections are steered to ARM via RSS for policy evaluation.
4. **Bidirectional offload**: Each CT entry covers both origin and reply directions.
5. **Aging**: Sessions time out after 5 minutes of inactivity (configurable).
