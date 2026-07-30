#!/usr/bin/env bash
set -euo pipefail

ERRORS=0

echo "=== EGREGORE STRUCTURAL HEALTH VERIFICATION ==="

# 1. Check all required files exist
for f in pyproject.toml Dockerfile docker-compose.yml .env.example .gitignore .dockerignore package.json frontend/package.json; do
    if [ ! -f "$f" ]; then
        echo "FAIL: Missing required file: $f"
        ERRORS=$((ERRORS + 1))
    else
        echo "PASS: $f exists"
    fi
done

# 2. Check zero orphan .py files at root
ORPHANS=$(git ls-files -o --exclude-standard '*.py' 2>/dev/null | grep '^[^/]*\.py$' || true)
if [ -n "$ORPHANS" ]; then
    echo "FAIL: Orphan .py files at root:"
    echo "$ORPHANS"
    ERRORS=$((ERRORS + 1))
else
    echo "PASS: Zero orphan .py files at root"
fi

# 3. Check zero __pycache__ directories outside .venv
PYCACHE=$(find . -type d -name '__pycache__' -not -path './.venv/*' 2>/dev/null || true)
if [ -n "$PYCACHE" ]; then
    echo "FAIL: __pycache__ directories found:"
    echo "$PYCACHE"
    ERRORS=$((ERRORS + 1))
else
    echo "PASS: Zero __pycache__ directories"
fi

# 4. Check no .sixth/ or .aider/ artifacts
if [ -d ".sixth" ] || [ -d ".aider" ]; then
    echo "FAIL: .sixth/ or .aider/ artifacts found"
    ERRORS=$((ERRORS + 1))
else
    echo "PASS: No .sixth/ or .aider/ artifacts"
fi

# 5. Check architecture enforcement tests
if PYTHONPATH=src python -m pytest tests/test_arch_enforcement.py -q 2>/dev/null; then
    echo "PASS: Architecture enforcement tests"
else
    echo "FAIL: Architecture enforcement tests"
    ERRORS=$((ERRORS + 1))
fi

# 6. Check all tests pass (skip the external-fixture canon yaml test)
if PYTHONPATH=src python -m pytest tests/ -q --ignore=tests/test_canon_yaml_schema.py 2>/dev/null; then
    echo "PASS: All tests pass"
else
    echo "FAIL: Some tests failed"
    ERRORS=$((ERRORS + 1))
fi

# 7. Check Docker build works
if docker build -t egregore-verify . 2>/dev/null; then
    echo "PASS: Docker build succeeds"
    docker rmi egregore-verify >/dev/null 2>&1 || true
else
    echo "FAIL: Docker build failed"
    ERRORS=$((ERRORS + 1))
fi

# 8. Smoke-test the running (or freshly started) compose stack
STACK_WAS_RUNNING=false
if docker compose ps --services --filter status=running 2>/dev/null | grep -q '^egregore$'; then
    STACK_WAS_RUNNING=true
    echo "PASS: Compose stack is already running"
else
    if docker compose up -d --wait --wait-timeout 120 2>/dev/null; then
        echo "PASS: Compose stack started for smoke test"
    else
        echo "FAIL: Compose stack failed to start for smoke test"
        ERRORS=$((ERRORS + 1))
    fi
fi

if [ $ERRORS -eq 0 ] && docker compose ps --services --filter status=running 2>/dev/null | grep -q '^egregore$'; then
    # The compose stack publishes the API directly on API_HOST_PORT (default 18000).
    if GATEWAY_URL="http://localhost:${API_HOST_PORT:-18000}" \
       CORE_URL="http://localhost:${API_HOST_PORT:-18000}" \
       WORKFLOW_PATH=/workflows/test-health \
       ./scripts/smoke_test.sh; then
        echo "PASS: Smoke tests against running stack"
    else
        echo "FAIL: Smoke tests against running stack"
        ERRORS=$((ERRORS + 1))
    fi
fi

if [ "$STACK_WAS_RUNNING" = false ]; then
    docker compose down >/dev/null 2>&1 || true
fi

# 9. Report
echo ""
echo "=== VERIFICATION COMPLETE ==="
if [ $ERRORS -eq 0 ]; then
    echo "STATUS: PASS (100% structural health)"
    echo "C4 gap: CLOSED"
    echo "C4+1 boundary: CLEAN"
    exit 0
else
    echo "STATUS: FAIL ($ERRORS errors)"
    exit 1
fi
