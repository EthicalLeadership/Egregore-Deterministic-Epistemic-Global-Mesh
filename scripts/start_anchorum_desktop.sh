#!/bin/bash
# Launch the ANCHORUM native desktop app.
# Derive the repo root from this script's location (no hardcoded home paths).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1
export DISPLAY="${DISPLAY:-:0}"
exec "$REPO_ROOT/.venv/bin/python" anchorum_desktop.py
