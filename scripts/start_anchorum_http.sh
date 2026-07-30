#!/bin/bash
# Egregore ANCHORUM plain-HTTP entry point.
# Serves the same Plane-2 app as the HTTPS dashboard but on port 8080 with no TLS,
# making the ANCHORUM website easy to reach from the local network.

cd /opt/egregore || exit 1

# Load .env so user-managed keys are available.
set -a
[ -f .env ] && source .env
set +a

export EGREGORE_API_KEYS="$(cat secrets/api_key.hex):test:admin:admin${EGREGORE_API_KEYS:+,}${EGREGORE_API_KEYS}"
export EGREGORE_ZARC_SIGNING_KEY_HEX="$(cat secrets/signing_key.pem)"
export PYTHONPATH=src

exec /opt/egregore/.venv/bin/uvicorn \
    egregore.interface.bootstrap:create_app \
    --factory \
    --host 0.0.0.0 \
    --port 8080 \
    --log-level info
