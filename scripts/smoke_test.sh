#!/usr/bin/env bash
# =============================================================================
# Egregore Smoke Test
# =============================================================================
# Exercises the running stack through the API gateway (npm workflow) or
# directly against the core (Docker workflow). Verifies:
#   1. The gateway and core HTTP listeners are up (/health)
#   2. The core can reach Postgres, Redis, and NATS (/ready)
#   3. A workflow can be created and its status read back end-to-end.
#
# Usage (npm):
#   ./scripts/smoke_test.sh
#
# Usage (Docker):
#   GATEWAY_URL=http://localhost:18000 CORE_URL=http://localhost:18000 \
#     WORKFLOW_PATH=/workflows/test-health ./scripts/smoke_test.sh
# =============================================================================
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://localhost:3000}"
CORE_URL="${CORE_URL:-http://localhost:8002}"
WORKFLOW_PATH="${WORKFLOW_PATH:-/api/workflows/test-health}"

red='\033[0;31m'
green='\033[0;32m'
reset='\033[0m'

fail() {
    echo -e "${red}FAIL${reset}: $1" >&2
    exit 1
}

pass() {
    echo -e "${green}PASS${reset}: $1"
}

echo "=== Egregore Smoke Test ==="
echo "Gateway: ${GATEWAY_URL}"
echo "Core:    ${CORE_URL}"

# --- 1. Liveness -------------------------------------------------------------
GW_HEALTH_STATUS=$(curl -s -o /dev/null -w '%{http_code}' "${GATEWAY_URL}/health" || true)
if [[ "${GW_HEALTH_STATUS}" != "200" ]]; then
    fail "gateway /health returned HTTP ${GW_HEALTH_STATUS:-<no response>}"
fi
pass "gateway /health returns 200"

CORE_HEALTH_STATUS=$(curl -s -o /dev/null -w '%{http_code}' "${CORE_URL}/health" || true)
if [[ "${CORE_HEALTH_STATUS}" != "200" ]]; then
    fail "core /health returned HTTP ${CORE_HEALTH_STATUS:-<no response>}"
fi
pass "core /health returns 200"

# --- 2. Readiness (DB / Redis / NATS) ----------------------------------------
READY_JSON=$(curl -s "${CORE_URL}/ready" || true)
READY_STATUS=$(curl -s -o /dev/null -w '%{http_code}' "${CORE_URL}/ready" || true)
if [[ "${READY_STATUS}" != "200" ]]; then
    echo "  /ready response: ${READY_JSON:-<none>}" >&2
    fail "/ready returned HTTP ${READY_STATUS:-<no response>}"
fi
pass "/ready returns 200 (${READY_JSON})"

# --- 3. End-to-end workflow create + read-back -------------------------------
CORRELATION_ID="smoke-$(date +%s)-$$"
WORKFLOW_PAYLOAD="{\"input\":{\"smoke\":true},\"idempotency_key\":\"${CORRELATION_ID}\",\"correlation_id\":\"${CORRELATION_ID}\"}"

CREATE_JSON=$(curl -s -X POST \
    -H "Content-Type: application/json" \
    -d "${WORKFLOW_PAYLOAD}" \
    "${GATEWAY_URL}${WORKFLOW_PATH}" || true)

WORKFLOW_ID=$(echo "${CREATE_JSON}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || true)
if [[ -z "${WORKFLOW_ID}" ]]; then
    echo "  create response: ${CREATE_JSON:-<none>}" >&2
    fail "failed to create workflow"
fi
pass "created workflow ${WORKFLOW_ID}"

# Poll the workflow status for up to 10 seconds.
for _ in {1..20}; do
    STATUS_JSON=$(curl -s "${GATEWAY_URL}/api/workflows/${WORKFLOW_ID}" || true)
    STATUS=$(echo "${STATUS_JSON}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)
    if [[ "${STATUS}" == "completed" ]]; then
        pass "workflow completed"
        break
    elif [[ "${STATUS}" == "failed" ]]; then
        echo "  final status: ${STATUS_JSON}" >&2
        fail "workflow failed"
    fi
    sleep 0.5
done

if [[ "${STATUS}" != "completed" ]]; then
    echo "  final status: ${STATUS_JSON}" >&2
    fail "workflow did not complete in time (status=${STATUS})"
fi

echo ""
echo -e "${green}=== ALL SMOKE TESTS PASSED ===${reset}"
