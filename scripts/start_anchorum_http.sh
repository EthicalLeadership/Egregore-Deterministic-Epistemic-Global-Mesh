#!/bin/bash
# Egregore ANCHORUM plain-HTTP entry point.
# Serves the ANCHORUM customer site on port 8080 with no TLS.
# This lightweight app does NOT load the LLM; it only serves dashboard pages.

# Derive the repo root from this script's location so the correct answer is
# automatic on any node, regardless of symlinks (~/blackstar, /opt/egregore).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

# Load .env so user-managed keys are available.
set -a
[ -f .env ] && source .env
set +a

export EGREGORE_API_KEYS="$(cat secrets/api_key.hex):test:admin:admin${EGREGORE_API_KEYS:+,}${EGREGORE_API_KEYS}"
export EGREGORE_ZARC_SIGNING_KEY_HEX="$(cat secrets/signing_key.pem)"
export PYTHONPATH=src

exec "$REPO_ROOT/.venv/bin/uvicorn" \
    egregore.interface.anchorum_http:create_app \
    --factory \
    --host 0.0.0.0 \
    --port 8080 \
    --log-level info
