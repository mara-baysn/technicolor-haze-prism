"""Post-test report generation -- HTML with charts, exportable as PDF."""

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ReportConfig:
    title: str = "Prism PoC-3 Results"
    output_dir: str = "results/"
    include_charts: bool = True


class ReportGenerator:
    """Generates self-contained HTML reports from test results.

    All CSS is inlined (no external dependencies) for air-gapped lab use.
    Charts are rendered as inline SVG.
    """

    def __init__(self, config: Optional[ReportConfig] = None):
        self.config = config or ReportConfig()
        Path(self.config.output_dir).mkdir(parents=True, exist_ok=True)

    def generate_html(self, test_results: list, metrics_history: list) -> str:
        """Generate full HTML report.

        Args:
            test_results: List of TestResult objects (or dicts with to_dict()).
            metrics_history: List of MetricsSnapshot objects (or dicts).

        Returns:
            Complete HTML string ready to write to file.
        """
        # Convert objects to dicts if needed
        results_data = []
        for r in test_results:
            if hasattr(r, "to_dict"):
                results_data.append(r.to_dict())
            elif isinstance(r, dict):
                results_data.append(r)

        metrics_data = []
        for m in metrics_history:
            if hasattr(m, "to_dict"):
                metrics_data.append(m.to_dict())
            elif isinstance(m, dict):
                metrics_data.append(m)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        summary_table = self.generate_summary_table(results_data)
        details_html = self._generate_details(results_data)
        chart_html = self._generate_metrics_chart(metrics_data) if self.config.include_charts else ""

        # Compute summary stats
        total = len(results_data)
        passed = sum(1 for r in results_data if r.get("status") == "passed")
        failed = sum(1 for r in results_data if r.get("status") == "failed")
        errors = sum(1 for r in results_data if r.get("status") == "error")

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{self.config.title}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    line-height: 1.6;
    color: #1a1a2e;
    background: #f8f9fa;
    padding: 2rem;
  }}
  .container {{ max-width: 1200px; margin: 0 auto; }}
  h1 {{
    font-size: 1.8rem;
    margin-bottom: 0.5rem;
    color: #16213e;
  }}
  h2 {{
    font-size: 1.3rem;
    margin: 2rem 0 1rem;
    color: #0f3460;
    border-bottom: 2px solid #e94560;
    padding-bottom: 0.3rem;
  }}
  .meta {{ color: #666; font-size: 0.9rem; margin-bottom: 2rem; }}
  .stats {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 1rem;
    margin-bottom: 2rem;
  }}
  .stat-card {{
    background: white;
    border-radius: 8px;
    padding: 1.2rem;
    text-align: center;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
  }}
  .stat-card .value {{ font-size: 2rem; font-weight: bold; }}
  .stat-card .label {{ font-size: 0.85rem; color: #666; }}
  .stat-card.passed .value {{ color: #27ae60; }}
  .stat-card.failed .value {{ color: #e74c3c; }}
  .stat-card.error .value {{ color: #f39c12; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    background: white;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    margin-bottom: 2rem;
  }}
  th, td {{ padding: 0.75rem 1rem; text-align: left; }}
  th {{ background: #16213e; color: white; font-weight: 500; }}
  tr:nth-child(even) {{ background: #f8f9fa; }}
  .badge {{
    padding: 0.2rem 0.6rem;
    border-radius: 4px;
    font-size: 0.8rem;
    font-weight: 500;
  }}
  .badge-passed {{ background: #d4edda; color: #155724; }}
  .badge-failed {{ background: #f8d7da; color: #721c24; }}
  .badge-error {{ background: #fff3cd; color: #856404; }}
  .badge-pending {{ background: #e2e3e5; color: #383d41; }}
  .details {{ margin-bottom: 1.5rem; }}
  .details summary {{
    cursor: pointer;
    font-weight: 500;
    padding: 0.5rem;
    background: #eee;
    border-radius: 4px;
  }}
  .details pre {{
    background: #1a1a2e;
    color: #e0e0e0;
    padding: 1rem;
    border-radius: 4px;
    overflow-x: auto;
    font-size: 0.85rem;
    margin-top: 0.5rem;
  }}
  .chart-container {{
    background: white;
    border-radius: 8px;
    padding: 1.5rem;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    margin-bottom: 2rem;
  }}
  svg {{ max-width: 100%; height: auto; }}
  footer {{
    margin-top: 3rem;
    text-align: center;
    color: #999;
    font-size: 0.8rem;
  }}
</style>
</head>
<body>
<div class="container">
  <h1>{self.config.title}</h1>
  <p class="meta">Generated: {now} | Tests: {total} | Duration: {self._total_duration(results_data):.1f}s</p>

  <div class="stats">
    <div class="stat-card passed"><div class="value">{passed}</div><div class="label">Passed</div></div>
    <div class="stat-card failed"><div class="value">{failed}</div><div class="label">Failed</div></div>
    <div class="stat-card error"><div class="value">{errors}</div><div class="label">Errors</div></div>
    <div class="stat-card"><div class="value">{total}</div><div class="label">Total</div></div>
  </div>

  <h2>Results Summary</h2>
  {summary_table}

  {chart_html}

  <h2>Test Details</h2>
  {details_html}

  <footer>
    Prism Virtual Firewall PoC | Report generated by prism-api-server
  </footer>
</div>
</body>
</html>"""
        return html

    def generate_summary_table(self, test_results: list) -> str:
        """Generate results summary table HTML.

        Args:
            test_results: List of test result dicts.

        Returns:
            HTML table string.
        """
        rows = ""
        for r in test_results:
            status = r.get("status", "pending")
            badge_class = f"badge-{status}"
            duration = r.get("duration_seconds", 0) or 0
            rows += f"""  <tr>
    <td>{r.get('test_id', 'N/A')}</td>
    <td><span class="badge {badge_class}">{status.upper()}</span></td>
    <td>{duration:.2f}s</td>
    <td>{len(r.get('assertion_results', []))} assertions</td>
  </tr>\n"""

        return f"""<table>
<thead><tr><th>Test ID</th><th>Status</th><th>Duration</th><th>Assertions</th></tr></thead>
<tbody>
{rows}</tbody>
</table>"""

    def save_report(self, html: str, filename: Optional[str] = None) -> str:
        """Save HTML report to file.

        Args:
            html: The rendered HTML content.
            filename: Optional filename (auto-generated with timestamp if None).

        Returns:
            Absolute path to the saved report.
        """
        if filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"report_{ts}.html"

        output_path = Path(self.config.output_dir) / filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
        logger.info("Report saved to %s", output_path)
        return str(output_path.resolve())

    def save_json(self, test_results: list, filename: Optional[str] = None) -> str:
        """Save raw results as JSON.

        Args:
            test_results: List of TestResult objects or dicts.
            filename: Optional filename (auto-generated if None).

        Returns:
            Absolute path to the saved JSON file.
        """
        if filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"results_{ts}.json"

        results_data = []
        for r in test_results:
            if hasattr(r, "to_dict"):
                results_data.append(r.to_dict())
            elif isinstance(r, dict):
                results_data.append(r)

        output_path = Path(self.config.output_dir) / filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(results_data, indent=2, default=str), encoding="utf-8"
        )
        logger.info("JSON results saved to %s", output_path)
        return str(output_path.resolve())

    def list_reports(self) -> list[dict]:
        """List all generated reports in the output directory.

        Returns:
            List of dicts with filename, path, size, and created timestamp.
        """
        output_dir = Path(self.config.output_dir)
        if not output_dir.exists():
            return []

        reports = []
        for f in sorted(output_dir.iterdir()):
            if f.suffix in (".html", ".json"):
                stat = f.stat()
                reports.append({
                    "filename": f.name,
                    "path": str(f.resolve()),
                    "size_bytes": stat.st_size,
                    "created_at": stat.st_mtime,
                })
        return reports

    def _generate_details(self, results_data: list) -> str:
        """Generate collapsible per-test detail sections."""
        html = ""
        for r in results_data:
            test_id = r.get("test_id", "N/A")
            status = r.get("status", "pending")
            error_msg = r.get("error_message", "")

            steps_html = ""
            for step in r.get("step_results", []):
                step_status = "PASS" if step.get("passed") else "FAIL"
                steps_html += (
                    f"[{step_status}] Step {step.get('step_index', '?')} on "
                    f"{step.get('target', '?')}: {step.get('command', '?')}\n"
                    f"       exit={step.get('exit_code', '?')} "
                    f"duration={step.get('duration_ms', 0):.0f}ms\n"
                )
                if step.get("stdout"):
                    stdout_preview = step["stdout"][:300]
                    steps_html += f"       stdout: {stdout_preview}\n"
                steps_html += "\n"

            assertions_html = ""
            for a in r.get("assertion_results", []):
                a_status = "PASS" if a.get("passed") else "FAIL"
                assertions_html += (
                    f"[{a_status}] {a.get('description', '?')}\n"
                    f"       actual={a.get('actual_value', 'N/A')} "
                    f"expected={a.get('expected', 'N/A')}\n\n"
                )

            error_section = f"\nERROR: {error_msg}\n" if error_msg else ""

            html += f"""<details class="details">
<summary>{test_id} - {status.upper()}</summary>
<pre>{error_section}{steps_html}{assertions_html}</pre>
</details>\n"""

        return html

    def _generate_metrics_chart(self, metrics_data: list) -> str:
        """Generate an inline SVG chart of throughput over time."""
        if not metrics_data or len(metrics_data) < 2:
            return ""

        # Generate simple SVG line chart for tx_gbps and rx_gbps
        width = 800
        height = 250
        padding = 50
        chart_w = width - 2 * padding
        chart_h = height - 2 * padding

        # Extract data points (last 300 samples max for rendering perf)
        samples = metrics_data[-300:]
        n = len(samples)
        if n < 2:
            return ""

        tx_values = [s.get("tx_gbps", 0) for s in samples]
        rx_values = [s.get("rx_gbps", 0) for s in samples]
        max_val = max(max(tx_values), max(rx_values), 1)
        min_val = 0

        def to_svg_point(index: int, value: float) -> tuple[float, float]:
            x = padding + (index / (n - 1)) * chart_w
            y = padding + chart_h - ((value - min_val) / (max_val - min_val)) * chart_h
            return x, y

        tx_points = " ".join(f"{to_svg_point(i, v)[0]:.1f},{to_svg_point(i, v)[1]:.1f}" for i, v in enumerate(tx_values))
        rx_points = " ".join(f"{to_svg_point(i, v)[0]:.1f},{to_svg_point(i, v)[1]:.1f}" for i, v in enumerate(rx_values))

        svg = f"""<div class="chart-container">
<h2>Throughput Over Time</h2>
<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
  <!-- Axes -->
  <line x1="{padding}" y1="{padding}" x2="{padding}" y2="{padding + chart_h}" stroke="#333" stroke-width="1"/>
  <line x1="{padding}" y1="{padding + chart_h}" x2="{padding + chart_w}" y2="{padding + chart_h}" stroke="#333" stroke-width="1"/>

  <!-- Grid lines -->
  <line x1="{padding}" y1="{padding}" x2="{padding + chart_w}" y2="{padding}" stroke="#eee" stroke-width="0.5"/>
  <line x1="{padding}" y1="{padding + chart_h/2:.0f}" x2="{padding + chart_w}" y2="{padding + chart_h/2:.0f}" stroke="#eee" stroke-width="0.5"/>

  <!-- Y-axis labels -->
  <text x="{padding - 5}" y="{padding + 5}" text-anchor="end" font-size="10" fill="#666">{max_val:.0f} Gbps</text>
  <text x="{padding - 5}" y="{padding + chart_h/2 + 5:.0f}" text-anchor="end" font-size="10" fill="#666">{max_val/2:.0f} Gbps</text>
  <text x="{padding - 5}" y="{padding + chart_h + 5}" text-anchor="end" font-size="10" fill="#666">0</text>

  <!-- TX line -->
  <polyline points="{tx_points}" fill="none" stroke="#e94560" stroke-width="1.5" stroke-linejoin="round"/>
  <!-- RX line -->
  <polyline points="{rx_points}" fill="none" stroke="#0f3460" stroke-width="1.5" stroke-linejoin="round"/>

  <!-- Legend -->
  <rect x="{padding + chart_w - 120}" y="{padding + 5}" width="12" height="12" fill="#e94560"/>
  <text x="{padding + chart_w - 104}" y="{padding + 15}" font-size="11" fill="#333">TX Gbps</text>
  <rect x="{padding + chart_w - 120}" y="{padding + 22}" width="12" height="12" fill="#0f3460"/>
  <text x="{padding + chart_w - 104}" y="{padding + 32}" font-size="11" fill="#333">RX Gbps</text>
</svg>
</div>"""
        return svg

    def _total_duration(self, results_data: list) -> float:
        """Sum up total test duration."""
        total = 0.0
        for r in results_data:
            d = r.get("duration_seconds")
            if d:
                total += d
        return total
