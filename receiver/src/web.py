"""Flask web UI for the Prism traffic receiver.

Exposes a dashboard on port 5002 showing per-port packet/byte counters
and active/blocked state for each port.
"""

from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request

from .listener import get_receiver

app = Flask(__name__, template_folder="templates")


@app.route("/")
def index():
    """Serve the main receiver dashboard."""
    receiver = get_receiver()
    return render_template("index.html", running=receiver.running)


@app.route("/api/status")
def api_status():
    """Return current receiver status as JSON."""
    receiver = get_receiver()
    return jsonify(receiver.get_stats())


@app.route("/api/start", methods=["POST"])
def api_start():
    """Start all port listeners."""
    receiver = get_receiver()
    receiver.start()
    return jsonify({"status": "started"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """Stop all port listeners."""
    receiver = get_receiver()
    receiver.stop()
    return jsonify({"status": "stopped"})


def main():
    """Entry point for the traffic receiver web UI."""
    host = os.environ.get("PRISM_RCV_HOST", "0.0.0.0")
    port = int(os.environ.get("PRISM_RCV_PORT", "5002"))
    debug = os.environ.get("PRISM_RCV_DEBUG", "0") == "1"

    # Auto-start listeners on boot
    receiver = get_receiver()
    receiver.start()

    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
