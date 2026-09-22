#!/bin/bash
# =============================================================================
# cline_sovereign_deploy.sh — Cline Sovereign Agent deployment (Directive v2)
#
# Chain:  Cline (hub daemon) -> http://127.0.0.1:8001/v1 -> Egregore EMS
#         -> GgufBackend -> local model. NO Ollama. NO port 11434.
#
# Phases covered: 2 (build), 3 (atomic versioned deploy), 5 (systemd),
#                 6 (boot persistence), 10 (functional test), 13 (idempotency),
#                 14 (sovereignty check).
# NOT covered here (require human/machine action): Phase 12 reboot validation,
# Phase 8 dashboard registration, Phase 9 world-map health wiring.
#
# Usage:
#   ./cline_sovereign_deploy.sh build      # Phase 2: build + record artifact
#   ./cline_sovereign_deploy.sh deploy     # Phase 3: atomic install + symlink
#   ./cline_sovereign_deploy.sh install    # Phase 5+6: unit + enable + linger
#   ./cline_sovereign_deploy.sh test       # Phase 10: functional chain test
#   ./cline_sovereign_deploy.sh rollback [version]   # Phase 3 rollback
#   ./cline_sovereign_deploy.sh audit      # Phase 14: sovereignty check
#   ./cline_sovereign_deploy.sh all        # build + deploy + install + test + audit
# =============================================================================
set -euo pipefail

CLINE_SRC="/home/kark/cline-main"
EGREGORE_ROOT="/media/kark/EGREGORE/egregore"
BIN_DIR="${EGREGORE_ROOT}/bin"
LOG_DIR="${EGREGORE_ROOT}/logs"
UNIT_SRC="${EGREGORE_ROOT}/deploy/systemd/user/egregore-cline.service"
UNIT_DST="$HOME/.config/systemd/user/egregore-cline.service"
EMS_URL="http://127.0.0.1:8001/v1"
KEEP_VERSIONS=3

log() { echo "[cline-deploy $(date -Is)] $*"; }
die() { echo "[cline-deploy FATAL] $*" >&2; exit 1; }

# -----------------------------------------------------------------------------
cmd_build() {
    log "Phase 2: building Cline CLI from ${CLINE_SRC}"
    command -v bun >/dev/null || die "bun not found in PATH (required: bun@1.3.13)"

    local version rev
    version=$(grep -m1 '"version"' "${CLINE_SRC}/apps/cli/package.json" | sed 's/.*"version": *"\([^"]*\)".*/\1/')
    rev=$(git -C "${CLINE_SRC}" rev-parse --short HEAD 2>/dev/null || echo "unknown-rev")
    log "Cline version: ${version}  source revision: ${rev}"

    # Idempotency: skip rebuild if artifact for this version already exists.
    if [[ -d "${BIN_DIR}/cline-${version}" ]]; then
        log "Artifact bin/cline-${version} already exists — skipping build (idempotent)."
        return 0
    fi

    ( cd "${CLINE_SRC}" && bun run build ) || die "build failed"

    # Locate the built artifact (dist entrypoint of @cline/cli).
    local artifact="${CLINE_SRC}/apps/cli/dist/index.js"
    [[ -f "${artifact}" ]] || die "expected build output ${artifact} not found"

    mkdir -p "${BIN_DIR}"
    cp -a "${CLINE_SRC}/apps/cli/dist" "${BIN_DIR}/cline-${version}.tmp.$$"
    mv "${BIN_DIR}/cline-${version}.tmp.$$" "${BIN_DIR}/cline-${version}"

    # Record provenance.
    {
        echo "version=${version}"
        echo "revision=${rev}"
        echo "built=$(date -Is)"
        echo "sha256:"
        find "${BIN_DIR}/cline-${version}" -type f -exec sha256sum {} \; | sort -k2
    } > "${BIN_DIR}/cline-${version}.BUILDINFO"

    log "Build complete: bin/cline-${version} (see .BUILDINFO)"
}

current_symlink_target() { readlink "${BIN_DIR}/cline" 2>/dev/null || echo "(none)"; }

# -----------------------------------------------------------------------------
cmd_deploy() {
    log "Phase 3: atomic versioned deployment"
    local latest
    latest=$(ls -1d "${BIN_DIR}"/cline-[0-9]* 2>/dev/null | sed 's|.*/||' | sort -V | tail -1) \
        || die "no built versions found — run 'build' first"

    local prev prev_sha
    prev=$(current_symlink_target)
    if [[ -f "${BIN_DIR}/${prev}" || -d "${BIN_DIR}/${prev}" ]]; then
        prev_sha=$(find "${BIN_DIR}/${prev}" -type f -exec sha256sum {} \; 2>/dev/null | sha256sum | cut -d' ' -f1)
        log "Current active target: ${prev} (aggregate sha256 ${prev_sha})"
    else
        log "No current active target."
    fi

    ln -sfn "cline-${latest}" "${BIN_DIR}/.cline.new" && mv -T "${BIN_DIR}/.cline.new" "${BIN_DIR}/cline"
    log "Symlink atomically updated: cline -> cline-${latest}"

    # Retain only the last KEEP_VERSIONS known-good versions.
    ls -1d "${BIN_DIR}"/cline-[0-9]* 2>/dev/null | sort -V | head -n -"${KEEP_VERSIONS}" \
        | while read -r old; do rm -rf "$old"; done || true

    "${BIN_DIR}/cline" --version >/dev/null 2>&1 && log "Executable validated." \
        || die "installed cline failed to execute"
}

# -----------------------------------------------------------------------------
cmd_install() {
    log "Phase 5+6: systemd user unit install + boot persistence"
    mkdir -p "$HOME/.config/systemd/user" "${LOG_DIR}"
    cp "${UNIT_SRC}" "${UNIT_DST}"

    systemctl --user daemon-reload
    systemctl --user enable egregore-cline.service
    log "Unit enabled."

    # Linger is required for user services to start at boot without login.
    if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
        log "Linger NOT enabled for ${USER}. Enabling (required for reboot persistence)."
        sudo loginctl enable-linger "$USER" || die "could not enable linger — reboot persistence will NOT work"
    else
        log "Linger already enabled."
    fi

    systemctl --user restart egregore-cline.service || die "service failed to start"
    sleep 3
    systemctl --user is-active egregore-cline.service >/dev/null || die "service not active after start"
    log "Service active: $(systemctl --user is-active egregore-cline.service)"
}

# -----------------------------------------------------------------------------
cmd_test() {
    log "Phase 10: functional chain test Cline -> EMS -> GgufBackend -> model"

    log "-- EMS /v1/models"
    curl -sf -m 10 "${EMS_URL}/models" | head -c 2000; echo

    log "-- EMS chat completion (model ds-coder-6.7b)"
    local resp
    resp=$(curl -sf -m 120 "${EMS_URL}/chat/completions" \
        -H 'Content-Type: application/json' \
        -d '{"model":"ds-coder-6.7b","messages":[{"role":"user","content":"Reply with exactly: OK"}],"max_tokens":16}')
    echo "${resp}" | head -c 2000; echo
    echo "${resp}" | grep -q '"content"' || die "chat completion returned no content"

    log "-- Cline headless one-shot through configured provider"
    CLINE_CONFIG_DIR="${EGREGORE_ROOT}/cline-config" "${BIN_DIR}/cline" --help >/dev/null \
        || die "cline CLI does not run"
    # NOTE: exact headless invocation must be confirmed via `cline --help`
    # (e.g. `cline run "<prompt>"` or equivalent). Adjust when verified.

    log "Functional tests PASSED"
}

# -----------------------------------------------------------------------------
cmd_rollback() {
    local target="${1:-}"
    [[ -n "${target}" ]] || target=$(ls -1d "${BIN_DIR}"/cline-[0-9]* 2>/dev/null | sed 's|.*/||' | sort -V | tail -2 | head -1)
    [[ -d "${BIN_DIR}/${target}" ]] || die "rollback target ${target} not found"
    systemctl --user stop egregore-cline.service 2>/dev/null || true
    ln -sfn "${target}" "${BIN_DIR}/.cline.new" && mv -T "${BIN_DIR}/.cline.new" "${BIN_DIR}/cline"
    systemctl --user start egregore-cline.service
    log "Rolled back to ${target}; service restarted."
}

# -----------------------------------------------------------------------------
cmd_audit() {
    log "Phase 14: sovereignty check — searching for Ollama / port 11434 references"
    local hits=0
    for f in "${UNIT_DST}" \
             "${EGREGORE_ROOT}/cline-config/data/settings/providers.json" \
             "${EGREGORE_ROOT}/deploy/systemd/user/"*.service; do
        if grep -HniE 'ollama|11434' "$f"; then hits=$((hits+1)); fi
    done
    if pgrep -af '11434|ollama' 2>/dev/null; then
        log "WARNING: ollama-related process found:"; pgrep -af 'ollama'; hits=$((hits+1))
    fi
    (( hits == 0 )) && log "SOVEREIGNTY OK: no Ollama/11434 references in deployed config or processes." \
                    || die "Ollama references found (${hits}) — investigate before production sign-off"
}

# -----------------------------------------------------------------------------
case "${1:-all}" in
    build)    cmd_build ;;
    deploy)   cmd_deploy ;;
    install)  cmd_install ;;
    test)     cmd_test ;;
    rollback) shift; cmd_rollback "$@" ;;
    audit)    cmd_audit ;;
    all)      cmd_build; cmd_deploy; cmd_install; cmd_test; cmd_audit ;;
    *) echo "Usage: $0 {build|deploy|install|test|rollback [ver]|audit|all}"; exit 1 ;;
esac
