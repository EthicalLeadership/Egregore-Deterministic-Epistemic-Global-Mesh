#!/usr/bin/env bash
# Robust pidfile-based launcher for the Egregore chat / HTTP API server.
#
# Defaults:
#   Host: 0.0.0.0
#   Port: 8443 (override with BLACKSTAR_CHAT_PORT)
#   SSL:  certs/dashboard.key + certs/dashboard.crt
#
# The script prevents duplicate uvicorn processes by tracking the PID in
# .pids/chat_server.pid and killing a previous instance before starting a new one.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_DIR="${REPO_ROOT}/.pids"
PID_FILE="${PID_DIR}/chat_server.pid"
LOG_DIR="${REPO_ROOT}/logs"
LOG_FILE="${LOG_DIR}/chat_server.log"
PORT="${BLACKSTAR_CHAT_PORT:-8443}"
HOST="${BLACKSTAR_CHAT_HOST:-0.0.0.0}"
APP="egregore.interface.bootstrap:create_app"

mkdir -p "${PID_DIR}" "${LOG_DIR}"

# Load API keys / env if present.
if [ -f "${REPO_ROOT}/.env" ]; then
  # shellcheck source=/dev/null
  set -a
  # shellcheck source=/dev/null
  . "${REPO_ROOT}/.env"
  set +a
fi

# Stop an existing instance tracked by the pidfile.
if [ -f "${PID_FILE}" ]; then
  OLD_PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [ -n "${OLD_PID}" ] && kill -0 "${OLD_PID}" 2>/dev/null; then
    echo "Stopping existing chat server (PID ${OLD_PID})..."
    kill "${OLD_PID}" 2>/dev/null || true
    # Wait briefly for graceful shutdown.
    for _ in $(seq 1 10); do
      kill -0 "${OLD_PID}" 2>/dev/null || break
      sleep 1
    done
    kill -9 "${OLD_PID}" 2>/dev/null || true
  fi
  rm -f "${PID_FILE}"
fi

# Also refuse to start if something else is already bound to the port.
if ss -tln 2>/dev/null | grep -qE "\b${PORT}\b"; then
  echo "ERROR: port ${PORT} is already in use. Choose another with BLACKSTAR_CHAT_PORT." >&2
  exit 1
fi

cd "${REPO_ROOT}"

echo "Starting Egregore chat server on https://${HOST}:${PORT} ..."
echo "Logs: ${LOG_FILE}"

nohup .venv/bin/uvicorn \
  "${APP}" \
  --factory \
  --host "${HOST}" \
  --port "${PORT}" \
  --ssl-keyfile "${REPO_ROOT}/certs/dashboard.key" \
  --ssl-certfile "${REPO_ROOT}/certs/dashboard.crt" \
  --log-level info \
  > "${LOG_FILE}" 2>&1 &

NEW_PID=$!
echo "${NEW_PID}" > "${PID_FILE}"

# Verify it started.
sleep 2
if kill -0 "${NEW_PID}" 2>/dev/null; then
  echo "Chat server running (PID ${NEW_PID})."
else
  echo "ERROR: chat server failed to start. See ${LOG_FILE}" >&2
  rm -f "${PID_FILE}"
  exit 1
fi
