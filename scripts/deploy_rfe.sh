#!/usr/bin/env bash
# Deploy the Reproducible Fusion Engine (RFE) on Pioneer 1.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"
RFE_PORT="${RFE_PORT:-8080}"
RFE_HOST="${RFE_HOST:-0.0.0.0}"

cd "${REPO_ROOT}"

echo "[RFE deploy] Repository root: ${REPO_ROOT}"

# 1. Ensure virtual environment exists.
if [[ ! -d "${VENV_DIR}" ]]; then
    echo "[RFE deploy] Creating virtual environment..."
    python3 -m venv "${VENV_DIR}"
fi

# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

# 2. Install dependencies.
echo "[RFE deploy] Installing dependencies..."
pip install -q -r requirements.txt
pip install -q -r requirements-dev.txt

# 3. Ensure data directory exists for .zarc store.
mkdir -p "${REPO_ROOT}/data"

# 4. Run RFE tests.
echo "[RFE deploy] Running RFE red-team and idempotency tests..."
pytest tests/test_rfe_replay.py tests/test_rfe_vim.py tests/test_rfe_api.py tests/redteam/ -m redteam -v --tb=short

# 5. Start the API.
echo "[RFE deploy] Starting Egregore API with RFE router on ${RFE_HOST}:${RFE_PORT}..."
exec uvicorn egregore.http_api.http.main:app --host "${RFE_HOST}" --port "${RFE_PORT}"
