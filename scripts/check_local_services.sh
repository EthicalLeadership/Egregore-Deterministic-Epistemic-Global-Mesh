#!/usr/bin/env bash
# =============================================================================
# Verify that Postgres, Redis, and NATS are reachable on localhost standard ports.
# =============================================================================
set -euo pipefail

red='\033[0;31m'
green='\033[0;32m'
yellow='\033[1;33m'
reset='\033[0m'

ERRORS=0

fail() {
    echo -e "${red}MISSING${reset}: $1" >&2
    ERRORS=$((ERRORS + 1))
}

pass() {
    echo -e "${green}OK${reset}: $1"
}

echo "=== Checking local services for npm-based Egregore development ==="

# --- Postgres ----------------------------------------------------------------
if command -v pg_isready >/dev/null 2>&1; then
    if pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
        pass "Postgres on localhost:5432"
    else
        fail "Postgres is not accepting connections on localhost:5432"
        echo "   Hint: pg_ctl start -D /var/lib/postgresql/data  (or use your distro's service)"
    fi
else
    echo -e "${yellow}WARN${reset}: pg_isready not found; skipping Postgres check"
fi

# --- Redis -------------------------------------------------------------------
if command -v redis-cli >/dev/null 2>&1; then
    if redis-cli -h localhost -p 6379 ping 2>/dev/null | grep -q PONG; then
        pass "Redis on localhost:6379"
    else
        fail "Redis is not responding to PING on localhost:6379"
        echo "   Hint: redis-server"
    fi
else
    echo -e "${yellow}WARN${reset}: redis-cli not found; skipping Redis check"
fi

# --- NATS --------------------------------------------------------------------
if command -v nc >/dev/null 2>&1; then
    if nc -z localhost 4222 >/dev/null 2>&1; then
        pass "NATS on localhost:4222"
    else
        fail "NATS is not listening on localhost:4222"
        echo "   Hint: nats-server"
    fi
else
    # Fallback to bash /dev/tcp if nc is unavailable.
    if timeout 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/4222; echo >&3; head -1 <&3' >/dev/null 2>&1; then
        pass "NATS on localhost:4222"
    else
        fail "NATS is not listening on localhost:4222"
        echo "   Hint: nats-server"
    fi
fi

if [ "$ERRORS" -eq 0 ]; then
    echo ""
    echo -e "${green}All required local services are reachable.${reset}"
    exit 0
else
    echo ""
    echo -e "${red}Some required services are missing. Start them and rerun 'npm run services:check'.${reset}"
    exit 1
fi
