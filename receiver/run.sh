#!/usr/bin/env bash
# Run the Prism traffic receiver locally.
# Usage: ./run.sh [--debug]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Create venv if it doesn't exist
if [ ! -d .venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q -e ".[dev]"

export PRISM_RCV_HOST="${PRISM_RCV_HOST:-0.0.0.0}"
export PRISM_RCV_PORT="${PRISM_RCV_PORT:-5002}"

if [[ "${1:-}" == "--debug" ]]; then
    export PRISM_RCV_DEBUG=1
fi

echo "Starting Prism Traffic Receiver on ${PRISM_RCV_HOST}:${PRISM_RCV_PORT}"
exec python -m src.web
