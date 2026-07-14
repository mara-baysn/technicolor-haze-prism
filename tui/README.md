# PRISM Firewall PoC -- TUI Traffic Monitor

Rich-based terminal dashboard that polls the traffic generator, firewall, and
receiver APIs to display live packet flow, firewall decisions, and throughput.

## Requirements

- Python 3.11+ (AlmaLinux 10.2 system Python works)
- pip packages: `rich`, `requests`

## Install

```bash
pip install -r tui/requirements.txt
```

## Run

```bash
python3 tui/prism_monitor.py
```

### CLI options

| Flag | Default | Description |
|------|---------|-------------|
| `--gen-url` | `http://localhost:5001` | Traffic generator API base URL |
| `--recv-url` | `http://localhost:5002` | Receiver API base URL |
| `--fw-url` | `http://192.168.0.38:8443` | Firewall API base URL |
| `--interval` | `0.5` | Poll interval in seconds |

### Example (lab environment)

```bash
python3 tui/prism_monitor.py \
  --fw-url http://192.168.0.38:8443 \
  --gen-url http://192.168.9.23:5001 \
  --recv-url http://192.168.9.23:5002
```

## Exit

Press **Ctrl+C** for a clean shutdown.

## What it shows

1. **Traffic Source** -- packets sent, success rate per port with progress bars
2. **DPU Firewall** -- forwarded/dropped counters, active rules, hw offload status, throughput
3. **Destination** -- connections received per port

Services that are unreachable display a red "API unreachable" message instead of
crashing. The layout renders correctly even with no data.
