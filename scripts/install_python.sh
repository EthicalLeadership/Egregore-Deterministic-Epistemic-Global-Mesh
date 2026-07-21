#!/usr/bin/env bash
# =============================================================================
# Bootstrap the Python virtual environment and install the Egregore package.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-python3}"

if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment (.venv) ..."
    "$PYTHON" -m venv .venv
fi

.venv/bin/pip install --upgrade pip setuptools wheel
.venv/bin/pip install -e ".[dev,messaging,persistence,telemetry]"

echo "Python environment ready."
