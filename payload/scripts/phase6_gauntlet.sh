#!/bin/bash
# Phase 6 done-criteria gauntlet: 20 consecutive factory runs on :8002.
# Logs per-run HTTP status, QC terminal state, and VRAM after each run.
API_KEY=$(cat /home/kark/blackstar/secrets/api_key.hex)
OUT=/home/kark/blackstar/report/phase6_gauntlet.log
: > "$OUT"

INFRA_BLOCKS=0
for i in $(seq 1 20); do
  START=$(date +%s)
  RESP=$(curl -s -m 280 -X POST -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
    http://127.0.0.1:8002/api/v1/factory \
    -d "{\"input\":\"write python function number $i that returns its square\",\"max_tokens\":96}")
  RC=$?
  ELAPSED=$(($(date +%s) - START))
  STATE=$(echo "$RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print((d.get('qc') or {}).get('terminal_state','?'))" 2>/dev/null || echo "PARSE_FAIL")
  VRAM=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits)
  echo "run $i: rc=$RC http_time=${ELAPSED}s qc=$STATE vram_free=${VRAM}MB" >> "$OUT"
  if [ "$STATE" = "BLOCKED" ]; then
    VIO=$(echo "$RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); print(','.join(v['constraint_id'] for v in (d.get('qc') or {}).get('violations',[])))" 2>/dev/null)
    echo "  violations: $VIO" >> "$OUT"
    case "$VIO" in *vram_insufficient*|*gate_error*|*critic_error*|*critic_timeout*) INFRA_BLOCKS=$((INFRA_BLOCKS+1));; esac
  fi
done
echo "DONE: infra_blocks=$INFRA_BLOCKS / 20" >> "$OUT"
