#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$REPO_ROOT/.venv}"
PY="$VENV/bin/python"
DROP_CACHES="${DROP_CACHES:-1}"
TIMEOUT="${TIMEOUT:-300}"
MODULES="${MODULES:-torch transformers sentence_transformers egregore.application.chat_interpreter}"

[[ -x "$PY" ]] || { echo "error: python not found at $PY" >&2; exit 1; }

now_ns() {
  if [[ -n "${EPOCHREALTIME:-}" ]]; then
    local s="${EPOCHREALTIME%.*}" f="${EPOCHREALTIME#*.}"
    printf '%d\n' "$(( s * 1000000000 + 10#${f}000 ))"
  else
    date +%s%N
  fi
}

drop_page_cache() {
  sudo -n true 2>/dev/null || return 1
  sync || return 1
  echo 3 | sudo -n tee /proc/sys/vm/drop_caches >/dev/null 2>&1
}

time_once() {
  local label="$1" module="$2" start end ms status rc
  start=$(now_ns)
  timeout "$TIMEOUT" "$PY" -c "import $module" >/dev/null 2>&1
  rc=$?
  end=$(now_ns)
  ms=$(( (end - start) / 1000000 ))
  case "$rc" in
    0)   status=ok ;;
    124) status="TIMEOUT(${TIMEOUT}s)" ;;
    *)   status=FAILED ;;
  esac
  printf '  %-40s %7d ms  [%s]\n' "$label" "$ms" "$status"
}

printf 'repo:   %s\n' "$REPO_ROOT"
printf 'venv:   %s\n' "$VENV"
printf 'python: %s\n' "$("$PY" -c 'import sys; print(sys.version.split()[0])')"

if stat -f -c %T "$REPO_ROOT" >/dev/null 2>&1; then
  printf 'mount:  %s\n' "$(stat -f -c %T "$REPO_ROOT")"
fi
echo

echo "== environment =="
for var in COVERAGE_PROCESS_START COVERAGE_PROCESS_CONFIG PYTHONPATH; do
  if [[ -v "$var" ]]; then printf '  %-24s %s\n' "$var" "${!var}"
  else printf '  %-24s (unset)\n' "$var"; fi
done
echo

if [[ "$DROP_CACHES" == "1" ]] && drop_page_cache; then
  echo "== cold pass (caches dropped before each module; timeout ${TIMEOUT}s) =="
  for m in $MODULES; do
    drop_page_cache >/dev/null 2>&1 || true
    time_once "cold  $m" "$m"
  done
  echo
else
  echo "== cold pass skipped (no passwordless sudo / cannot drop caches) =="
  echo
fi

echo "== warm pass =="
for m in $MODULES; do
  time_once "warm  $m" "$m"
done
echo

cat <<'NOTES'
== how to read this ==

Each line:  <label>  <elapsed_ms>  [ok|TIMEOUT|FAILED]

cold >> warm for a module
    Disk-bound. Confirms slow-mount cold reads, not a code defect.

warm also slow (> 10s)
    That module's import chain does real work. Profile with:
        python -X importtime -c 'import <module>' 2>&1 | sort -t'|' -k2 -n | tail -30

[TIMEOUT(Ns)] on cold pass
    Mount is slow enough that cold import exceeds the timeout.
    Environmental. Check the "mount:" line above.

[FAILED]
    Import did not succeed. Time is meaningless; investigate first.
NOTES
