# Prism Virtual Firewall PoC — Demo Runbook

Complete step-by-step demo script for the Prism DPU-accelerated virtual firewall.
Anyone on the team should be able to follow this cold with zero assumed knowledge.

---

## Table of Contents

1. [Environment Overview](#environment-overview)
2. [TUI Framework Recommendation](#tui-framework-recommendation)
3. [TUI Wrapper Specification](#tui-wrapper-specification)
4. [Pre-Demo Setup (5 min before)](#pre-demo-setup)
5. [Demo Flow (8-10 minutes)](#demo-flow)
6. [Teardown](#teardown)
7. [Troubleshooting](#troubleshooting)

---

## Environment Overview

| Device | IP | Role | Access |
|--------|-----|------|--------|
| HPE ProLiant x86 | 192.168.9.23 | Traffic gen (VF0/ns-inet) + receiver (VF3/ns-client) | SSH as `almalinux` |
| BlueField-3 DPU | 192.168.0.38 | tc-flower firewall daemon on :8443 | SSH as `ubuntu` (from HPE only) |
| Gigabyte ARM Server | 192.168.9.16 | Dashboard UI on :8000, API orchestrator | SSH as `almalinux` |
| Presenter Laptop | — | SSH tunnel, browser, demo terminals | — |

### Network Topology (Data Plane)

```
ns-inet (10.0.1.1)        BF3 DPU eSwitch         ns-client (10.0.2.1)
   VF0 ──────────────── pf0vf0  ────  pf0vf3 ──────────────── VF3
               (internet side)           (client side)
```

### Key Facts for the Audience

- Default policy: **deny-all** (no rules = no traffic)
- All rules offloaded to BF3 silicon (`in_hw=true`)
- Measured throughput: **148 Gbps** (iperf3, 4 parallel streams)
- Latency: **sub-millisecond** (< 0.3ms measured)
- CPU usage during forwarding: **~0%** (all in hardware)

---

## TUI Framework Recommendation

### Evaluation

| Framework | Install | Live Updates | Visual Impact | Complexity |
|-----------|---------|--------------|---------------|------------|
| **Rich** | `pip install rich` (pure Python) | Yes — `Live` display, tables, progress bars | High — colored panels, sparklines, spinners | Low |
| **Textual** | `pip install textual` (depends on Rich) | Yes — reactive widgets, CSS layout | Very high — full app, mouse support | Medium-High |
| **curses** | stdlib (zero install) | Yes — manual refresh | Low — plain text unless heavy coding | High |
| **urwid** | `pip install urwid` | Yes — event loop based | Medium — widgets but dated look | Medium |

### Recommendation: Rich

**Use Rich** for the demo TUI. Rationale:

1. **Zero-dependency install** on AlmaLinux 10.2: `pip install rich` — no C extensions, no wheel issues on air-gapped lab.
2. **`Live` display** with 0.5s refresh gives buttery smooth counter updates.
3. **Visual impact** out of the box: colored panels, progress bars with percentage, styled tables, emoji-free bold numbers. Looks like htop without the complexity.
4. **Simple code path**: ~150 lines for the full TUI vs ~500 for Textual. If something breaks 5 minutes before the demo, you can debug it.
5. **No event loop conflict**: Works with the existing Flask/threading architecture in the traffic-gen. Just reads the `Stats` dataclass snapshot.

Textual would look marginally better but adds complexity and a learning curve with no payoff for a demo that only needs to display stats for 10 minutes. Rich delivers 90% of the visual impact with 20% of the effort.

---

## TUI Wrapper Specification

### What It Shows

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  PRISM TRAFFIC MONITOR                          ▸ RUNNING  10s  ┃
┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┫
┃                                                                  ┃
┃  Connections/sec:   42.3         Total Attempted:      423       ┃
┃  Bytes Sent:        27.1 KB      Success Rate:         78.3%     ┃
┃                                                                  ┃
┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┫
┃  PORT     SENT   OK     FAIL   RATE     STATUS                   ┃
┃  ─────────────────────────────────────────────────────────────── ┃
┃  :80      120    94     26     9.4/s    ████████░░ 78%  PASS     ┃
┃  :443     105    105    0      10.5/s   ██████████ 100% PASS     ┃
┃  :22      98     98     0      9.8/s    ██████████ 100% PASS     ┃
┃  :5432    100    34     66     10.0/s   ███░░░░░░░ 34%  BLOCKED  ┃
┃                                                                  ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
```

### Design Details

- **Header panel**: Title + running state + elapsed time
- **Aggregate row**: Connections/sec, total bytes, success rate (big bold numbers)
- **Per-port table**: Each port gets a row with colored progress bar
  - Green bar = success rate
  - Red "BLOCKED" label when success < 10%
  - Green "PASS" label when success > 90%
  - Yellow "PARTIAL" when in between
- **Refresh**: Every 0.5 seconds via `rich.live.Live(refresh_per_second=2)`
- **Colors**: Green for passing, red for blocked, yellow for partial, cyan for headers

### File Structure

```
traffic-gen/
  src/
    tui.py          # Main TUI display (Rich Live + Table + Panel)
    generator.py    # (existing) — TrafficGenerator with Stats
    web.py          # (existing) — Flask web API
  pyproject.toml    # Add: rich>=13.0 to dependencies
```

### `tui.py` Interface

```python
# Entry point: python -m src.tui [--profile mixed] [--rate 20]
# Starts TrafficGenerator + renders Rich Live display
# Ctrl+C to stop cleanly

def main():
    gen = TrafficGenerator(...)
    gen.profile = args.profile
    gen.rate_pps = args.rate
    gen.start()
    render_live(gen)  # blocks, updates every 0.5s
```

### Installation on HPE Server

```bash
# One-time setup (air-gapped: pre-download wheel)
pip install rich>=13.0

# Launch
sudo ip netns exec ns-inet python3 -m src.tui --profile mixed --rate 20
```

---

## Pre-Demo Setup

**Start this 5 minutes before the demo begins.**

### Step 0: SSH Key Setup

All SSH commands use the lab key. Set this in your shell:

```bash
export SSH_KEY=~/.ssh/id_rsa_plano_vms
export HPE=almalinux@192.168.9.23
export ARM=almalinux@192.168.9.16
export DPU=ubuntu@192.168.0.38
```

### Step 1: Establish SSH Tunnel (Laptop to DPU via ARM)

The DPU is only reachable from the 192.168.0.x subnet. The ARM server can reach it.
Create a tunnel so your laptop can hit the firewall API:

```bash
# From your laptop — tunnels localhost:18443 to DPU:8443 via ARM
ssh -i $SSH_KEY -L 18443:192.168.0.38:8443 $ARM -N &
```

Verify the tunnel:

```bash
curl -s http://localhost:18443/health | python3 -m json.tool
```

Expected output:
```json
{
    "status": "healthy",
    "uptime_seconds": 1234.5,
    "active_rules": 0,
    "default_policy": "deny-all"
}
```

### Step 2: Verify All Services Running

```bash
# Firewall daemon on DPU
curl -sf http://localhost:18443/health && echo "  [OK] Firewall"

# Traffic generator on HPE (from lab network)
curl -sf http://192.168.9.23:5001/api/stats && echo "  [OK] Traffic Gen"

# Receiver on HPE
curl -sf http://192.168.9.23:5002/api/stats && echo "  [OK] Receiver"

# Dashboard/Orchestrator on ARM
curl -sf http://192.168.9.16:8000/health && echo "  [OK] Dashboard"
```

If any service is down, start it:

```bash
# Start firewall daemon on DPU
ssh -i $SSH_KEY $ARM "ssh $DPU 'cd ~/prism-tc-firewall && \
  nohup uvicorn src.main:app --host 0.0.0.0 --port 8443 > /tmp/tc-fw.log 2>&1 &'"

# Start traffic-gen on HPE (ns-inet namespace)
ssh -i $SSH_KEY $HPE "sudo ip netns exec ns-inet \
  nohup python3 -m src.web --directory ~/prism-traffic-gen > /tmp/prism-gen.log 2>&1 &"

# Start receiver on HPE (ns-client namespace)
ssh -i $SSH_KEY $HPE "sudo ip netns exec ns-client \
  nohup python3 -m src.web --directory ~/prism-receiver > /tmp/prism-recv.log 2>&1 &"

# Start orchestrator on ARM
ssh -i $SSH_KEY $ARM "cd ~/prism-api-server && \
  nohup uvicorn src.main:app --host 0.0.0.0 --port 8000 > /tmp/prism-api.log 2>&1 &"
```

### Step 3: Flush Any Leftover Rules (Clean Slate)

```bash
curl -s -X POST http://localhost:18443/rules/flush | python3 -m json.tool
```

Expected: `{"status": "flushed", "rules_removed": 0}`

### Step 4: Initialize Deny-All Topology on DPU

```bash
ssh -i $SSH_KEY $ARM "ssh $DPU 'sudo ~/prism-tc-firewall/src/setup_topology.sh --deny-all'"
```

### Step 5: Arrange Windows

Open and position these windows so ALL are visible simultaneously:

| Window | Position | Content |
|--------|----------|---------|
| Browser | Top-left (60%) | `http://192.168.9.16:8000` — Dashboard |
| Terminal 1 | Bottom-left | HPE: TUI traffic monitor |
| Terminal 2 | Bottom-center | HPE: `btop` or `htop` |
| Terminal 3 | Top-right | HPE: iperf3 (run on demand) |
| Terminal 4 | Bottom-right | DPU: `watch tc -s filter show` |

Pre-connect each terminal:

```bash
# Terminal 1 (Traffic TUI) — leave ready but don't start yet
ssh -i $SSH_KEY $HPE

# Terminal 2 (CPU monitor)
ssh -i $SSH_KEY $HPE
htop  # or: btop

# Terminal 3 (iperf3 — ready to run)
ssh -i $SSH_KEY $HPE

# Terminal 4 (DPU tc counters)
ssh -i $SSH_KEY $ARM
ssh $DPU
sudo watch -n 0.5 'tc -s filter show dev pf0vf0 ingress'
```

### Step 6: Pre-Position Browser

Open `http://192.168.9.16:8000` in the browser. Verify you see:
- Dashboard showing "0 rules" and "deny-all" default policy
- No active traffic (all counters at zero)

---

## Demo Flow

**Total time: 8-10 minutes.**

---

### Act 1: Show the Problem (Deny-All Blocks Everything)

**Duration: ~1 minute**

**Narration:** "Right now, the DPU firewall is in deny-all mode. No traffic passes through the BlueField silicon. Let me prove it."

#### 1.1 Start Traffic Generator (Terminal 1)

```bash
# Terminal 1 — SSH to HPE, run in ns-inet namespace
sudo ip netns exec ns-inet python3 -m src.tui --profile mixed --rate 20
```

**What the audience sees:** The TUI shows connections being attempted but ALL failing. Every port shows red "BLOCKED" status. Connection rate is non-zero but success rate is 0%.

#### 1.2 Point to Dashboard (Browser)

The dashboard should show:
- Active rules: 0
- Default policy: deny-all
- Packets dropped counter incrementing (if the dashboard polls metrics)

#### 1.3 Show tc Counters on DPU (Terminal 4)

```bash
# Already running: watch -n 0.5 'tc -s filter show dev pf0vf0 ingress'
```

**What the audience sees:** No filter rules listed (or only the implicit deny). No packet counters incrementing.

**Key line:** "Notice there are ZERO tc-flower rules. With deny-all, absence of a rule means traffic is dropped before it even reaches the eSwitch pipeline."

---

### Act 2: Add Allow Rule — Traffic Flows (The Magic Moment)

**Duration: ~2 minutes**

**Narration:** "Now I'll add a single ALLOW rule. Watch what happens — traffic starts flowing INSTANTLY through the DPU hardware."

#### 2.1 Add Allow-All Rule via API

```bash
# From laptop terminal (or any machine with tunnel access)
curl -s -X POST http://localhost:18443/rules \
  -H "Content-Type: application/json" \
  -d '{
    "src_ip": "10.0.1.1",
    "dst_ip": "10.0.2.1",
    "protocol": "tcp",
    "action": "ALLOW",
    "priority": 100,
    "comment": "Allow all TCP from internet to client"
  }' | python3 -m json.tool
```

Expected response (note `in_hw: true`):
```json
{
    "id": "a1b2c3d4",
    "src_ip": "10.0.1.1",
    "dst_ip": "10.0.2.1",
    "protocol": "tcp",
    "action": "ALLOW",
    "priority": 100,
    "in_hw": true,
    "tc_handle_fwd": "0x1",
    "tc_handle_rev": "0x1"
}
```

#### 2.2 Observe Immediate Effect

**Terminal 1 (TUI):** Success rate jumps from 0% to ~100%. All port bars turn green. Connections/sec shows steady flow.

**Terminal 4 (DPU):** The `watch` output now shows:
```
filter protocol ip pref 100 flower chain 0 handle 0x1
  eth_type ipv4
  ip_proto tcp
  src_ip 10.0.1.1
  dst_ip 10.0.2.1
  in_hw in_hw_count 1
    action order 1: mirred (Egress Redirect to device pf0vf3) ...
    Sent 12345 bytes 200 pkt (sobbing incrementing)
```

**Key line:** "See `in_hw in_hw_count 1` — that confirms this rule is executing in BlueField silicon, not in software. The packet counter is incrementing in real-time."

**Dashboard:** Rules count becomes 1, traffic graph shows flow.

---

### Act 3: Hardware Offload Proof — 148 Gbps (The Money Shot)

**Duration: ~2 minutes**

**Narration:** "Let me show you the throughput this achieves. I'll run iperf3 through the exact same firewall path."

#### 3.1 Start iperf3 Server (on receiver side, already running or start it)

```bash
# Terminal 3 — SSH to HPE, run in ns-client namespace
ssh -i $SSH_KEY $HPE "sudo ip netns exec ns-client iperf3 -s -p 5201 -D"
```

#### 3.2 Run iperf3 Client

```bash
# Terminal 3 — SSH to HPE, run in ns-inet namespace
ssh -i $SSH_KEY $HPE "sudo ip netns exec ns-inet iperf3 -c 10.0.2.1 -p 5201 -t 10 -P 4"
```

**Expected output:**
```
[SUM]   0.00-10.00  sec   173 GBytes   148 Gbits/sec    0    sender
[SUM]   0.00-10.00  sec   173 GBytes   148 Gbits/sec         receiver
```

**Key line:** "148 gigabits per second. Through a stateful firewall. In a DPU that draws 75 watts. Try that with a traditional firewall appliance."

#### 3.3 Show CPU is Idle (Terminal 2 — htop/btop)

**What the audience sees:** CPU cores at ~0-2% utilization while 148 Gbps is flowing.

**Key line:** "Look at the CPU — it's doing nothing. The entire packet pipeline is in the BlueField eSwitch hardware. The host CPU is free to run applications."

If htop is not visually dramatic enough, run this one-liner:

```bash
ssh -i $SSH_KEY $HPE "top -b -n 1 | head -5"
```

Expected:
```
%Cpu(s):  0.3 us,  0.1 sy,  0.0 ni, 99.5 id,  0.0 wa,  0.0 hi,  0.1 si,  0.0 st
```

**That 99.5% idle while pushing 148 Gbps is the proof.**

---

### Act 4: Selective Deny — Block Port 80 (Surgical Precision)

**Duration: ~2 minutes**

**Narration:** "Now I'll add a DENY rule for just port 80. Watch how only HTTP traffic gets blocked — everything else keeps flowing."

#### 4.1 Add Deny Rule for Port 80

```bash
curl -s -X POST http://localhost:18443/rules \
  -H "Content-Type: application/json" \
  -d '{
    "src_ip": "10.0.1.1",
    "dst_ip": "10.0.2.1",
    "dst_port": 80,
    "protocol": "tcp",
    "action": "DENY",
    "priority": 50,
    "comment": "Block HTTP traffic"
  }' | python3 -m json.tool
```

Note: priority 50 is HIGHER priority than the allow-all at 100 (lower number = higher priority in tc).

#### 4.2 Observe Selective Block

**Terminal 1 (TUI):**
- Port :80 immediately turns red — "BLOCKED"
- Ports :443, :22, :5432 remain green — "PASS"
- Overall success rate drops from 100% to ~75% (1 of 4 ports blocked)

**Terminal 4 (DPU):** Shows a new filter rule with `action drop` and its own packet counter incrementing:
```
filter protocol ip pref 50 flower chain 0 handle 0x2
  eth_type ipv4
  ip_proto tcp
  src_ip 10.0.1.1
  dst_ip 10.0.2.1
  dst_port 80
  in_hw in_hw_count 1
    action order 1: gact action drop
    Sent 567 bytes 9 pkt (sobbing incrementing — these are DROPPED packets)
```

**Key line:** "The drop counter is incrementing in hardware. Those packets never reach the host — they're discarded in silicon at line rate. Zero CPU cost to drop traffic."

---

### Act 5: Remove Deny — Instant Recovery

**Duration: ~1 minute**

**Narration:** "And when I remove the deny rule, traffic resumes instantly."

#### 5.1 List Rules to Get the Deny Rule ID

```bash
curl -s http://localhost:18443/rules | python3 -m json.tool
```

Find the rule with `"action": "DENY"` and note its `id` field.

#### 5.2 Delete the Deny Rule

```bash
# Replace RULE_ID with the actual ID from step 5.1
curl -s -X DELETE http://localhost:18443/rules/RULE_ID | python3 -m json.tool
```

#### 5.3 Observe Instant Recovery

**Terminal 1 (TUI):** Port :80 immediately turns green again. Success rate returns to 100%.

**Key line:** "Sub-millisecond policy update. The rule was removed from silicon and traffic resumed before the next packet arrived."

---

### Act 6: Wrap-Up and Summary

**Duration: ~1 minute**

**Narration:** "Let me summarize what we just demonstrated."

Show the metrics endpoint:

```bash
curl -s http://localhost:18443/metrics | python3 -m json.tool
```

Talking points:
- tc-flower rules offloaded to BF3 eSwitch silicon (in_hw=true)
- 148 Gbps forwarding throughput
- 0% CPU utilization during forwarding
- Sub-millisecond policy updates (add/remove)
- Deny-all default = zero trust starting point
- Per-port, per-IP, per-protocol granularity
- Hardware packet counters for both forward and drop

---

## Hard Evidence Commands (Reference)

These are the commands that provide irrefutable proof of hardware offload. Use them during the demo at the indicated moments.

### Prove Rules Are in Hardware

```bash
# SSH to DPU and show filters with stats
ssh -i $SSH_KEY $ARM "ssh $DPU 'sudo tc -s filter show dev pf0vf0 ingress'"
```

Look for:
- `in_hw in_hw_count 1` — rule is in silicon
- `Sent X bytes Y pkt` — hardware packet counter (increments without CPU involvement)

### Prove Throughput

```bash
# iperf3 through the firewall (must have allow rule active)
ssh -i $SSH_KEY $HPE "sudo ip netns exec ns-inet iperf3 -c 10.0.2.1 -p 5201 -t 10 -P 4"
```

Look for: `148 Gbits/sec` in the SUM line.

### Prove CPU is Idle

```bash
# During iperf3 or traffic flow, check CPU on HPE
ssh -i $SSH_KEY $HPE "top -b -n 1 | grep '%Cpu'"
```

Look for: `99%+ idle`

### Prove Latency

```bash
# Ping through the firewall (must have ICMP allow rule or allow-all)
ssh -i $SSH_KEY $HPE "sudo ip netns exec ns-inet ping -c 5 10.0.2.1"
```

Look for: `< 0.3ms` RTT

### Prove Hardware Drop Counters

```bash
# After adding a DENY rule, watch the drop counter increment
ssh -i $SSH_KEY $ARM "ssh $DPU 'sudo watch -n 0.5 tc -s filter show dev pf0vf0 ingress'"
```

Look for: `gact action drop` followed by `Sent X bytes Y pkt` incrementing = packets dropped in silicon.

### Prove Bidirectional Rules

```bash
# Show rules on both ports (forward and reverse paths)
ssh -i $SSH_KEY $ARM "ssh $DPU 'sudo tc -s filter show dev pf0vf0 ingress; echo; \
  echo \"=== REVERSE PATH ===\"; tc -s filter show dev pf0vf3 ingress'"
```

---

## Complete API Reference (Copy-Paste Ready)

### Health Check

```bash
curl -s http://localhost:18443/health | python3 -m json.tool
```

### List All Rules

```bash
curl -s http://localhost:18443/rules | python3 -m json.tool
```

### Add Allow Rule (All TCP)

```bash
curl -s -X POST http://localhost:18443/rules \
  -H "Content-Type: application/json" \
  -d '{
    "src_ip": "10.0.1.1",
    "dst_ip": "10.0.2.1",
    "protocol": "tcp",
    "action": "ALLOW",
    "priority": 100,
    "comment": "Allow all TCP traffic"
  }' | python3 -m json.tool
```

### Add Allow Rule (HTTPS Only)

```bash
curl -s -X POST http://localhost:18443/rules \
  -H "Content-Type: application/json" \
  -d '{
    "src_ip": "10.0.1.1",
    "dst_ip": "10.0.2.1",
    "dst_port": 443,
    "protocol": "tcp",
    "action": "ALLOW",
    "priority": 100,
    "comment": "Allow HTTPS only"
  }' | python3 -m json.tool
```

### Add Deny Rule (Block Port 80)

```bash
curl -s -X POST http://localhost:18443/rules \
  -H "Content-Type: application/json" \
  -d '{
    "src_ip": "10.0.1.1",
    "dst_ip": "10.0.2.1",
    "dst_port": 80,
    "protocol": "tcp",
    "action": "DENY",
    "priority": 50,
    "comment": "Block HTTP"
  }' | python3 -m json.tool
```

### Add Deny Rule (Block SSH)

```bash
curl -s -X POST http://localhost:18443/rules \
  -H "Content-Type: application/json" \
  -d '{
    "src_ip": "10.0.1.1",
    "dst_ip": "10.0.2.1",
    "dst_port": 22,
    "protocol": "tcp",
    "action": "DENY",
    "priority": 50,
    "comment": "Block SSH"
  }' | python3 -m json.tool
```

### Delete a Rule

```bash
# Replace RULE_ID with the actual 8-char ID
curl -s -X DELETE http://localhost:18443/rules/RULE_ID | python3 -m json.tool
```

### Flush All Rules (Emergency Reset)

```bash
curl -s -X POST http://localhost:18443/rules/flush | python3 -m json.tool
```

### Get Metrics

```bash
curl -s http://localhost:18443/metrics | python3 -m json.tool
```

### Get Active Sessions

```bash
curl -s http://localhost:18443/sessions | python3 -m json.tool
```

### Get Topology

```bash
curl -s http://localhost:18443/topology | python3 -m json.tool
```

---

## Teardown

After the demo, clean up:

```bash
# 1. Stop traffic generator TUI (Ctrl+C in Terminal 1)

# 2. Flush all firewall rules
curl -s -X POST http://localhost:18443/rules/flush

# 3. Kill iperf3 server
ssh -i $SSH_KEY $HPE "sudo pkill iperf3"

# 4. Close SSH tunnel
kill %1  # or find the PID: ps aux | grep "ssh.*18443"

# 5. (Optional) Restore allow-all for next user
ssh -i $SSH_KEY $ARM "ssh $DPU 'sudo ~/prism-tc-firewall/src/setup_topology.sh --allow-all'"
```

---

## Troubleshooting

### "Firewall daemon not reachable"

```bash
# Check if uvicorn is running on DPU
ssh -i $SSH_KEY $ARM "ssh $DPU 'ps aux | grep uvicorn'"

# Restart it
ssh -i $SSH_KEY $ARM "ssh $DPU 'cd ~/prism-tc-firewall && \
  nohup uvicorn src.main:app --host 0.0.0.0 --port 8443 > /tmp/tc-fw.log 2>&1 &'"
```

### "in_hw flag not showing"

```bash
# Check eSwitch mode is set to switchdev
ssh -i $SSH_KEY $ARM "ssh $DPU 'devlink dev eswitch show pci/0000:03:00.0'"
# Should show: mode switchdev

# If not in switchdev, this requires a DPU reboot with proper mlx config
```

### "iperf3 shows 0 throughput"

The allow rule must be active. Check:

```bash
curl -s http://localhost:18443/rules | python3 -m json.tool
# Must have at least one ALLOW rule covering the iperf3 traffic
```

Also ensure iperf3 server is running:

```bash
ssh -i $SSH_KEY $HPE "sudo ip netns exec ns-client iperf3 -s -p 5201 -D"
```

### "TUI shows all connections failing even with allow rule"

1. Check the rule was actually created: `curl http://localhost:18443/rules`
2. Check `in_hw` is true — if false, the rule is in software and may not work
3. Check namespaces are correct:
   ```bash
   ssh -i $SSH_KEY $HPE "sudo ip netns exec ns-inet ip addr show"
   # Should show 10.0.1.1
   ssh -i $SSH_KEY $HPE "sudo ip netns exec ns-client ip addr show"
   # Should show 10.0.2.1
   ```

### "SSH tunnel dropped"

```bash
# Re-establish
ssh -i $SSH_KEY -L 18443:192.168.0.38:8443 $ARM -N &
# Verify
curl -s http://localhost:18443/health
```

### "Dashboard shows stale data"

Hard refresh the browser (Ctrl+Shift+R). The dashboard polls the orchestrator on ARM which in turn queries the firewall API.

---

## Quick Reference Card (Print This)

```
SSH TUNNEL:     ssh -i $SSH_KEY -L 18443:192.168.0.38:8443 almalinux@192.168.9.16 -N &
DASHBOARD:      http://192.168.9.16:8000
FIREWALL API:   http://localhost:18443  (via tunnel)

START TUI:      sudo ip netns exec ns-inet python3 -m src.tui --profile mixed --rate 20
START IPERF:    sudo ip netns exec ns-inet iperf3 -c 10.0.2.1 -p 5201 -t 10 -P 4
WATCH TC:       sudo watch -n 0.5 'tc -s filter show dev pf0vf0 ingress'
CHECK CPU:      htop  (or: top -b -n 1 | grep '%Cpu')

ADD ALLOW:      curl -X POST localhost:18443/rules -H "Content-Type: application/json" \
                  -d '{"src_ip":"10.0.1.1","dst_ip":"10.0.2.1","protocol":"tcp","action":"ALLOW","priority":100}'

ADD DENY:       curl -X POST localhost:18443/rules -H "Content-Type: application/json" \
                  -d '{"dst_port":80,"protocol":"tcp","action":"DENY","priority":50}'

DELETE RULE:    curl -X DELETE localhost:18443/rules/RULE_ID
FLUSH ALL:      curl -X POST localhost:18443/rules/flush
HEALTH:         curl localhost:18443/health
```
