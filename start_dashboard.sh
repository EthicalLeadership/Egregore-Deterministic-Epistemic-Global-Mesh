#!/bin/bash
cd /opt/egregore || exit 1

# Load .env so user-managed keys (e.g. operator keys) are available.
set -a
[ -f .env ] && source .env
set +a

# Load environment variables
export EGREGORE_API_KEYS="$(cat secrets/api_key.hex):test:admin:admin${EGREGORE_API_KEYS:+,}${EGREGORE_API_KEYS}"
export EGREGORE_ZARC_SIGNING_KEY_HEX="$(cat secrets/signing_key.pem)"
export PYTHONPATH=src

# Start uvicorn with production settings + TLS
exec /opt/egregore/.venv/bin/uvicorn \
    egregore.interface.bootstrap:create_app \
    --factory \
    --host 0.0.0.0 \
    --port 8443 \
    --ssl-keyfile /opt/egregore/certs/dashboard.key \
    --ssl-certfile /opt/egregore/certs/dashboard.crt \
    --log-level info
