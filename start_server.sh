#!/bin/bash
# Start Egregore server with a clean environment so .env is the source of truth.
cd /opt/egregore || exit 1

# Load .env
set -a
[ -f .env ] && source .env
set +a

# Ensure the API key and signing key are available even if .env is not present.
export BLACKSTAR_API_KEYS="$(cat secrets/api_key.hex):test:admin:admin${BLACKSTAR_API_KEYS:+,}${BLACKSTAR_API_KEYS}"
export BLACKSTAR_ZARC_SIGNING_KEY_HEX="$(cat secrets/signing_key.pem)"
export PYTHONPATH=src

exec env -i \
  HOME="$HOME" \
  PATH="/opt/egregore/.venv/bin:/usr/bin:/bin" \
  PYTHONPATH=src \
  BLACKSTAR_API_KEYS="$BLACKSTAR_API_KEYS" \
  BLACKSTAR_ZARC_SIGNING_KEY_HEX="$BLACKSTAR_ZARC_SIGNING_KEY_HEX" \
  BLACKSTAR_CHAT_MODEL="${BLACKSTAR_CHAT_MODEL:-qwen2.5-7b-instruct}" \
  /opt/egregore/.venv/bin/uvicorn \
    egregore.interface.bootstrap:create_app \
    --factory \
    --host 0.0.0.0 \
    --port 8443 \
    --ssl-keyfile certs/dashboard.key \
    --ssl-certfile certs/dashboard.crt \
    --log-level info
