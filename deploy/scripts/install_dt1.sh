#!/bin/bash
# DT1 Unified Installation & Management Script
set -euo pipefail

K8S_DIR="$(dirname "$0")/kubernetes"
PHASES=(infra nats edge core obs security)

function usage() {
  echo "Usage: $0 [install|infra|nats|edge|core|obs|security|status|validate|uninstall]"
  exit 1
}

function check_prereqs() {
  command -v kubectl >/dev/null || { echo "kubectl not found"; exit 1; }
  kubectl version --client
}

function apply_phase() {
  local phase=$1
  case $phase in
    infra)
      kubectl apply -f "$K8S_DIR/00-namespaces.yaml"
      kubectl apply -f "$K8S_DIR/01-configmaps.yaml"
      kubectl apply -f "$K8S_DIR/02-secrets.yaml"
      kubectl apply -f "$K8S_DIR/03-storage.yaml"
      ;;
    nats)
      kubectl apply -f "$K8S_DIR/10-nats-jetstream.yaml"
      ;;
    edge)
      kubectl apply -f "$K8S_DIR/11-edge-ingress.yaml"
      kubectl apply -f "$K8S_DIR/12-edge-scheduler.yaml"
      kubectl apply -f "$K8S_DIR/13-edge-blade-workers.yaml"
      ;;
    core)
      kubectl apply -f "$K8S_DIR/14-gpu-blade-workers.yaml"
      kubectl apply -f "$K8S_DIR/15-core-cpu-workers.yaml"
      ;;
    obs)
      kubectl apply -f "$K8S_DIR/20-observability.yaml"
      ;;
    security)
      kubectl apply -f "$K8S_DIR/30-network-policies.yaml"
      kubectl apply -f "$K8S_DIR/40-rbac.yaml"
      ;;
    *)
      echo "Unknown phase: $phase"; exit 1
      ;;
  esac
}

function wait_ready() {
  local ns=$1
  echo "Waiting for pods in $ns to be ready..."
  kubectl wait --for=condition=Ready pods --all -n "$ns" --timeout=180s || true
}

function install_all() {
  for phase in "${PHASES[@]}"; do
    echo "[+] Installing phase: $phase"
    apply_phase $phase
  done
  for ns in dt1-system edge-system core-system gpu-fabric core-observability; do
    wait_ready $ns
  done
}

function status() {
  for ns in dt1-system edge-system core-system gpu-fabric core-observability; do
    echo "--- $ns ---"
    kubectl get pods -n $ns || true
  done
}

function validate() {
  status
  # Add more validation logic as needed
}

function uninstall() {
  for phase in security obs core edge nats infra; do
    echo "[+] Deleting phase: $phase"
    case $phase in
      infra)
        kubectl delete -f "$K8S_DIR/03-storage.yaml" --ignore-not-found
        kubectl delete -f "$K8S_DIR/02-secrets.yaml" --ignore-not-found
        kubectl delete -f "$K8S_DIR/01-configmaps.yaml" --ignore-not-found
        kubectl delete -f "$K8S_DIR/00-namespaces.yaml" --ignore-not-found
        ;;
      *)
        apply_phase $phase
        kubectl delete -f "$K8S_DIR/$(printf "%02d" $((${!PHASES[@]}+1)))-*.yaml" --ignore-not-found
        ;;
    esac
  done
}

check_prereqs

case "${1:-}" in
  install) install_all ;;
  infra|nats|edge|core|obs|security) apply_phase $1 ;;
  status) status ;;
  validate) validate ;;
  uninstall) uninstall ;;
  *) usage ;;
esac
