#!/bin/bash
# Start Egregore server with a clean environment so .env is the source of truth.
cd "$(dirname "$0")" || exit 1

# Load .env
set -a
[ -f .env ] && source .env
set +a

# Ensure the API key and signing key are available even if .env is not present.
export EGREGORE_API_KEYS="$(cat secrets/api_key.hex):test:admin:admin${EGREGORE_API_KEYS:+,}${EGREGORE_API_KEYS}"
export EGREGORE_ZARC_SIGNING_KEY_HEX="$(cat secrets/signing_key.pem)"
export PYTHONPATH=src

exec env -i \
  HOME="$HOME" \
  PATH="$PWD/.venv/bin:/usr/bin:/bin" \
  PYTHONPATH=src \
  EGREGORE_API_KEYS="$EGREGORE_API_KEYS" \
  EGREGORE_ZARC_SIGNING_KEY_HEX="$EGREGORE_ZARC_SIGNING_KEY_HEX" \
  EGREGORE_CHAT_MODEL="${EGREGORE_CHAT_MODEL:-my-coder-ft}" \
  EGREGORE_DEFAULT_BACKEND="${EGREGORE_DEFAULT_BACKEND:-egregore}" \
  .venv/bin/uvicorn \
    egregore.interface.bootstrap:create_app \
    --factory \
    --host 0.0.0.0 \
    --port 8443 \
    --ssl-keyfile certs/dashboard.key \
    --ssl-certfile certs/dashboard.crt \
    --log-level info
