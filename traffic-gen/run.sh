#!/usr/bin/env bash
# Run the Prism traffic generator locally.
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

export PRISM_GEN_HOST="${PRISM_GEN_HOST:-0.0.0.0}"
export PRISM_GEN_PORT="${PRISM_GEN_PORT:-5001}"

if [[ "${1:-}" == "--debug" ]]; then
    export PRISM_GEN_DEBUG=1
fi

echo "Starting Prism Traffic Generator on ${PRISM_GEN_HOST}:${PRISM_GEN_PORT}"
exec python -m src.web
