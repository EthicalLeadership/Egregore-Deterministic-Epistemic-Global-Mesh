#!/usr/bin/env bash
# DT1 deployment helper.
# Runs the full deployment order required by the DT1 spec.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DT1_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DT1_MANIFEST_DIR="${DT1_MANIFEST_DIR:-${DT1_DIR}/deployment}"
DT1_OBSERVABILITY_DIR="${DT1_OBSERVABILITY_DIR:-${DT1_DIR}/dt1-system/observability}"
DT1_DASHBOARD_FILE="${DT1_DASHBOARD_FILE:-${DT1_DIR}/dt1-deploy/observability/dashboards/executive-health.json}"

usage() {
  cat <<'EOF'
Usage: deploy.sh [--dry-run]

Deployment order:
  1. namespaces
  2. network policies
  3. edge
  4. core
  5. observability
  6. NATS resources
  7. observability rules and dashboards

Environment overrides:
  DT1_MANIFEST_DIR
  DT1_OBSERVABILITY_DIR
  DT1_DASHBOARD_FILE

The script is intentionally simple and only depends on kubectl.
EOF
}

dry_run=false
if [ "${1:-}" = "--dry-run" ]; then
  dry_run=true
fi

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

kubectl_apply() {
  if [ "$dry_run" = true ]; then
    kubectl apply --dry-run=client -f "$1"
  else
    kubectl apply -f "$1"
  fi
}

apply_tree() {
  local path="$1"
  if [ -d "$path" ]; then
    kubectl_apply "$path"
  else
    echo "Missing required path: $path" >&2
    exit 1
  fi
}

echo "== DT1 deploy: namespaces =="
kubectl_apply "${DT1_MANIFEST_DIR}/00-namespaces.yaml"

echo "== DT1 deploy: network policies =="
kubectl_apply "${DT1_MANIFEST_DIR}/01-network-policies.yaml"

echo "== DT1 deploy: edge =="
kubectl_apply "${DT1_MANIFEST_DIR}/13-edge-blade-workers.yaml"

echo "== DT1 deploy: core =="
kubectl_apply "${DT1_MANIFEST_DIR}/15-core-cpu-workers.yaml"

echo "== DT1 deploy: observability =="
kubectl_apply "${DT1_MANIFEST_DIR}/20-observability.yaml"

echo "== DT1 deploy: NATS resources =="
kubectl_apply "${DT1_MANIFEST_DIR}/10-nats-jetstream.yaml"

echo "== DT1 deploy: observability rules and dashboards =="
kubectl_apply "${DT1_OBSERVABILITY_DIR}/recording-rules.yaml"
kubectl_apply "${DT1_OBSERVABILITY_DIR}/alert-rules.yaml"
kubectl_apply "${DT1_DASHBOARD_FILE}"

echo "DT1 deployment complete."
