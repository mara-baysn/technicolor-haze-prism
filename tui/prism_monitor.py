#!/usr/bin/env python3
"""PRISM Firewall PoC — Rich TUI Traffic Monitor.

Polls traffic-gen, receiver, and firewall APIs to display a live-updating
dashboard of packet flow, firewall decisions, and throughput.

Requirements: rich, requests (see requirements.txt)
Run: python3 tui/prism_monitor.py [--fw-url ...] [--gen-url ...] [--recv-url ...]
Exit: Ctrl+C
"""

from __future__ import annotations

import argparse
import time
from typing import Any

import requests
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ─── Constants ────────────────────────────────────────────────────────────────

PORT_LABELS = {
    80: "HTTP",
    443: "HTTPS",
    22: "SSH",
    5432: "PostgreSQL",
}

BAR_FULL = "█"
BAR_EMPTY = "░"
BAR_WIDTH = 30


# ─── API helpers ──────────────────────────────────────────────────────────────


def fetch_json(url: str, timeout: float = 0.4) -> dict[str, Any] | None:
    """Fetch JSON from a URL, returning None on any failure."""
    try:
        resp = requests.get(url, timeout=timeout, verify=False)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def format_bytes(n: int | float) -> str:
    """Human-readable byte size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def format_bps(bps: float) -> str:
    """Human-readable bits-per-second."""
    if bps < 1_000:
        return f"{bps:.0f} bps"
    elif bps < 1_000_000:
        return f"{bps / 1_000:.1f} Kbps"
    elif bps < 1_000_000_000:
        return f"{bps / 1_000_000:.1f} Mbps"
    else:
        return f"{bps / 1_000_000_000:.2f} Gbps"


def make_bar(current: int, total: int) -> str:
    """Render a text-based progress bar."""
    if total == 0:
        return BAR_EMPTY * BAR_WIDTH
    ratio = min(current / total, 1.0)
    filled = int(ratio * BAR_WIDTH)
    return BAR_FULL * filled + BAR_EMPTY * (BAR_WIDTH - filled)


# ─── Display builders ─────────────────────────────────────────────────────────


def build_header(elapsed: float) -> Panel:
    """Top banner with title and elapsed time."""
    title = Text()
    title.append("  PRISM FIREWALL MONITOR", style="bold white")
    padding = " " * 40
    title.append(padding)
    title.append(" ● LIVE", style="bold green")
    title.append(f"   elapsed: {int(elapsed)}s", style="dim white")
    return Panel(title, style="bold yellow", expand=True)


def build_traffic_source(data: dict[str, Any] | None) -> Panel:
    """Traffic generator panel.

    Expected API shape (GET /api/stats):
      {
        "aggregate": {"total_attempted": N, "total_succeeded": N,
                      "total_failed": N, "connections_per_sec": F, ...},
        "per_port": [{"port": 80, "attempted": N, "succeeded": N, "failed": N}, ...]
      }
    """
    if data is None:
        content = Text("  API unreachable", style="bold red")
        return Panel(content, title="[yellow] TRAFFIC SOURCE (VF0 → 10.0.2.1) [/]",
                     border_style="red", expand=True)

    table = Table(show_header=False, show_edge=False, box=None, expand=True, padding=(0, 1))
    table.add_column(ratio=1)

    # Summary row from aggregate
    agg = data.get("aggregate", {})
    attempted = agg.get("total_attempted", 0)
    succeeded = agg.get("total_succeeded", 0)
    failed = agg.get("total_failed", attempted - succeeded)
    rate = agg.get("connections_per_sec", 0)

    summary = Text()
    summary.append(f"  Rate: {rate:.1f} conn/s", style="white")
    summary.append(f" │ Attempted: {attempted:,}", style="white")
    summary.append(f" │ Succeeded: {succeeded:,}", style="green")
    summary.append(f" │ Failed: {failed:,}", style="red" if failed > 0 else "white")
    table.add_row(summary)
    table.add_row(Text(""))

    # Per-port bars (per_port is a list of dicts)
    per_port = data.get("per_port", [])
    for entry in sorted(per_port, key=lambda x: x.get("port", 0)):
        port = entry.get("port", 0)
        sent = entry.get("attempted", 0)
        ok = entry.get("succeeded", 0)
        pct = (ok / sent * 100) if sent > 0 else 0
        bar = make_bar(ok, sent)

        row = Text()
        row.append(f"  :{port:<5}", style="cyan")
        row.append(bar, style="green" if pct > 80 else "yellow")
        row.append(f"  {ok:,}/{sent:,}", style="white")
        row.append(f"  ({pct:.0f}%)", style="green" if pct > 90 else "yellow")
        table.add_row(row)

    return Panel(table, title="[yellow] TRAFFIC SOURCE (VF0 → 10.0.2.1) [/]",
                 border_style="bright_black", expand=True)


def build_firewall(metrics: dict[str, Any] | None, rules: list[dict] | None,
                   throughput_bps: float) -> Panel:
    """DPU firewall panel.

    Expected API shapes:
      GET /metrics -> {"packets_forwarded": N, "packets_dropped": N,
                       "bytes_forwarded": N, "bytes_dropped": N, ...}
      GET /rules   -> {"rules": [{"action": "ALLOW", "dst_port": 443,
                        "protocol": "tcp", "in_hw": true, "packets_fwd": N, ...}], ...}
    """
    if metrics is None and rules is None:
        content = Text("  API unreachable", style="bold red")
        return Panel(content, title="[yellow] DPU FIREWALL (BF3 eSwitch, tc-flower) [/]",
                     border_style="red", expand=True)

    table = Table(show_header=False, show_edge=False, box=None, expand=True, padding=(0, 1))
    table.add_column(ratio=1)

    forwarded = (metrics or {}).get("packets_forwarded", 0)
    dropped = (metrics or {}).get("packets_dropped", 0)
    tp_str = format_bps(throughput_bps) if throughput_bps > 0 else "calculating..."

    summary = Text()
    summary.append(f"  Forwarded: {forwarded:,}", style="green")
    summary.append(f"    Dropped: {dropped:,}", style="red" if dropped > 0 else "white")
    summary.append(f"    Throughput: ~{tp_str}", style="cyan")
    table.add_row(summary)

    # Rules
    rule_list = rules or []
    in_hw_count = sum(1 for r in rule_list if r.get("in_hw", False))
    rule_info = Text()
    rule_info.append(f"  Active Rules: {len(rule_list)}", style="white")
    if rule_list:
        hw_label = f" ({in_hw_count} in_hw)" if in_hw_count > 0 else " (0 in_hw)"
        rule_info.append(hw_label, style="green" if in_hw_count == len(rule_list) else "yellow")
    table.add_row(rule_info)

    # Default policy
    default_policy = (metrics or {}).get("default_policy", "")
    if default_policy:
        pol = Text()
        pol.append(f"  Default policy: {default_policy}", style="dim white")
        table.add_row(pol)

    table.add_row(Text(""))

    for rule in rule_list:
        action = rule.get("action", "unknown").upper()
        # dst_port may be null for wildcard rules
        port = rule.get("dst_port") or rule.get("port") or "*"
        proto = rule.get("protocol", "tcp")
        in_hw = rule.get("in_hw", False)
        packets = rule.get("packets_fwd", 0) + rule.get("packets_rev", 0)

        row = Text()
        if action in ("ALLOW", "ACCEPT"):
            row.append("  ● ", style="green")
            row.append(f"ALLOW  {proto}:{port}", style="green")
        else:
            row.append("  ● ", style="red")
            row.append(f"DENY   {proto}:{port}", style="red")

        hw_style = "green" if in_hw else "yellow"
        row.append(f"   {'in_hw' if in_hw else 'sw'}", style=hw_style)
        row.append(f"  {packets:,} pkt", style="white")

        if action in ("DENY", "DROP", "REJECT"):
            row.append("  ← BLOCKED", style="bold red")
        table.add_row(row)

    return Panel(table, title="[yellow] DPU FIREWALL (BF3 eSwitch, tc-flower) [/]",
                 border_style="bright_black", expand=True)


def build_receiver(data: dict[str, Any] | None) -> Panel:
    """Receiver / destination panel.

    Expected API shape (GET /api/stats):
      {
        "ports": [{"port": 80, "connections": N, "bytes_received": N, ...}, ...],
        "bind_ip": "10.0.2.1", ...
      }
    """
    if data is None:
        content = Text("  API unreachable", style="bold red")
        return Panel(content, title="[yellow] DESTINATION (VF3 ← 10.0.2.1) [/]",
                     border_style="red", expand=True)

    table = Table(show_header=False, show_edge=False, box=None, expand=True, padding=(0, 1))
    table.add_column(ratio=1)

    # Compute totals from the ports list
    ports_list = data.get("ports", [])
    total_conn = sum(p.get("connections", 0) for p in ports_list)
    total_bytes = sum(p.get("bytes_received", 0) for p in ports_list)

    summary = Text()
    summary.append(f"  Total Connections: {total_conn:,}", style="white")
    summary.append(f" │ Total Bytes: {format_bytes(total_bytes)}", style="white")
    table.add_row(summary)
    table.add_row(Text(""))

    for entry in sorted(ports_list, key=lambda x: x.get("port", 0)):
        port = entry.get("port", 0)
        conns = entry.get("connections", 0)
        label = PORT_LABELS.get(port, f"port-{port}")

        row = Text()
        row.append(f"  :{port:<5}", style="cyan")
        row.append(f"({label})", style="dim white")
        padding = " " * max(1, 14 - len(label))
        row.append(padding)
        row.append(f"{conns:,} conn", style="white")
        row.append("  ●", style="green" if conns > 0 else "dim")
        table.add_row(row)

    return Panel(table, title="[yellow] DESTINATION (VF3 ← 10.0.2.1) [/]",
                 border_style="bright_black", expand=True)


# ─── Main loop ────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="PRISM Firewall PoC — TUI Monitor")
    parser.add_argument("--gen-url", default="http://localhost:5001",
                        help="Traffic generator API base URL")
    parser.add_argument("--recv-url", default="http://localhost:5002",
                        help="Receiver API base URL")
    parser.add_argument("--fw-url", default="http://192.168.0.38:8443",
                        help="Firewall API base URL")
    parser.add_argument("--interval", type=float, default=0.5,
                        help="Poll interval in seconds")
    args = parser.parse_args()

    console = Console()
    start_time = time.time()

    # For throughput calculation
    prev_bytes: int | None = None
    prev_time: float | None = None
    throughput_bps: float = 0.0

    # Suppress urllib3 InsecureRequestWarning for self-signed certs
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    console.print("[bold yellow]PRISM Monitor[/] starting... (Ctrl+C to quit)\n")

    with Live(console=console, refresh_per_second=2, screen=False) as live:
        try:
            while True:
                elapsed = time.time() - start_time

                # Poll APIs
                gen_data = fetch_json(f"{args.gen_url}/api/stats")
                recv_data = fetch_json(f"{args.recv_url}/api/stats")
                fw_metrics = fetch_json(f"{args.fw_url}/metrics")
                fw_rules_resp = fetch_json(f"{args.fw_url}/rules")

                # Extract rules list from response
                fw_rules: list[dict] | None = None
                if isinstance(fw_rules_resp, dict):
                    fw_rules = fw_rules_resp.get("rules", [])
                elif isinstance(fw_rules_resp, list):
                    fw_rules = fw_rules_resp

                # Calculate throughput from firewall byte counters
                current_bytes = (fw_metrics or {}).get("bytes_forwarded", 0)
                current_bytes += (fw_metrics or {}).get("bytes_dropped", 0)
                now = time.time()
                if prev_bytes is not None and prev_time is not None and current_bytes > 0:
                    dt = now - prev_time
                    if dt > 0:
                        byte_delta = current_bytes - prev_bytes
                        if byte_delta >= 0:
                            throughput_bps = (byte_delta * 8) / dt
                prev_bytes = current_bytes
                prev_time = now

                # Build display
                display = Group(
                    build_header(elapsed),
                    Text(""),
                    build_traffic_source(gen_data),
                    Text(""),
                    build_firewall(fw_metrics, fw_rules, throughput_bps),
                    Text(""),
                    build_receiver(recv_data),
                )
                live.update(display)

                time.sleep(args.interval)

        except KeyboardInterrupt:
            pass

    console.print("\n[bold yellow]PRISM Monitor[/] stopped.")


if __name__ == "__main__":
    main()
