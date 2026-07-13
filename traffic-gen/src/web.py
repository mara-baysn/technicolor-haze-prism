"""Flask web UI for the Prism traffic generator.

Exposes a dashboard on port 5001 with start/stop controls and live stats.
Runs inside ns-inet on the HPE server (10.0.1.1 -> 10.0.2.1).
"""

from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request

from .generator import Profile, get_generator

app = Flask(__name__, template_folder="templates")


@app.route("/")
def index():
    """Serve the main dashboard."""
    gen = get_generator()
    return render_template(
        "index.html",
        profiles=[p.value for p in Profile],
        current_profile=gen.profile.value,
        rate=gen.rate_pps,
        running=gen.running,
    )


@app.route("/api/stats")
def api_stats():
    """Return current generator statistics as JSON."""
    gen = get_generator()
    return jsonify(gen.get_stats())


@app.route("/api/start", methods=["POST"])
def api_start():
    """Start traffic generation."""
    gen = get_generator()
    data = request.get_json(silent=True) or {}

    if "profile" in data:
        gen.profile = data["profile"]
    if "rate" in data:
        gen.rate_pps = int(data["rate"])

    gen.start()
    return jsonify({"status": "started", "profile": gen.profile.value, "rate": gen.rate_pps})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """Stop traffic generation."""
    gen = get_generator()
    gen.stop()
    return jsonify({"status": "stopped"})


@app.route("/api/status")
def api_status():
    """Return current generator status (backward compat)."""
    gen = get_generator()
    return jsonify(gen.get_stats())


def main():
    """Entry point for the traffic generator web UI."""
    host = os.environ.get("PRISM_GEN_HOST", "0.0.0.0")
    port = int(os.environ.get("PRISM_GEN_PORT", "5001"))
    debug = os.environ.get("PRISM_GEN_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
