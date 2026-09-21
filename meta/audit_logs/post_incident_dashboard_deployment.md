# Post-Incident Review: Dashboard Stack Deployment

**Type:** `audit_log`  
**Scope:** Local development environment (HTTP, single-GPU)  
**Status:** Resolved — functional, with residual debt

---

## 1. Plane 1 — Chronology (What Occurred)

### 1.1 Initial State

The target deployment stack consists of three services:

- **Gateway + Dashboard:** Node.js frontend server on port 3000
- **Control Center API:** Node.js orchestration API on port 3001
- **Python Core API:** Inference backend on port 8002

The documented local-dev procedure is in `DEPLOYMENT.md` and `AGENTS.md`.

### 1.2 Sequence of Events

| Step | Action | Outcome |
|---|---|---|
| 1 | Started a projection-layer server on port 8080 (later 8443) and a standalone proxy on 8001 | These are not the documented dashboard services. VRAM was consumed by a model instance that the dashboard stack could not use. |
| 2 | Started the Core API with `uvicorn ...:app --factory` | Failed. The module exports an app instance, not a factory. GPU time (~18 s) was spent on a load cycle that aborted. |
| 3 | Started the gateway, Control Center, and Core API in the correct npm stack | Services bound to ports 3000, 3001, 8002. |
| 4 | Reported services as operational | The proxy was throwing `IndexError`; the dashboard had not been rendered in a browser. |
| 5 | Restarted the gateway three times in sequence | Added `/health`, fixed CSP, added API key to health check. Each restart was a separate edit-reload cycle. |
| 6 | Wrote the gateway Node.js PID to `.pids/core.pid` | The filename historically tracked the Python core API, not the gateway. PID consumers may resolve the wrong process. |
| 7 | Modified `start_server.sh` | Changed legacy env-var prefixes and model identifiers to align with current docs. The original script referenced a different env namespace and a base model; the new version references a fine-tuned model. |
| 8 | Identified white-page root cause | The gateway’s CSP included `upgrade-insecure-requests`, which blocked HTTP asset loads. Removing the directive restored rendering. |
| 9 | Verified end-to-end inference | `POST /v1/chat/completions` on 8002 returned valid output with governance flags enabled. |

---

## 2. Plane 2 — Analysis (Why It Mattered)

### 2.1 Failures in Process

| Failure Mechanism | Impact |
|---|---|
| Documentation not read before action | `DEPLOYMENT.md` and `AGENTS.md` specify the npm stack; the projection server was started instead. Wasted GPU memory and time on a non-target service. |
| Success claimed without end-to-end verification | Service binding was checked; browser rendering and proxy error logs were not. False-positive status delayed actual debugging. |
| Incremental gateway edits | Three separate restarts for `/health`, CSP, and auth instead of one design pass. Operator time lost to stop/start cycles. |
| Wrong uvicorn flag | `--factory` was passed to a module that exports an app instance. Failed startup after model load; GPU cycle wasted. |
| Misleading PID filename | Gateway PID written to a path historically associated with the Python core. Scripts reading `.pids/core.pid` would target the wrong process. |
| Unverified breaking change in startup script | Env-var prefix and model name were changed without confirming downstream consumers. External tooling or documentation referencing the old names may fail silently. |

### 2.2 Current-State Risks

| Risk | Severity | Description |
|---|---|---|
| Two incompatible startup paths | High | `start_server.sh` (port 8443) and the npm stack (ports 3000/8002) cannot coexist on a single GPU. Future operators may start the wrong one. |
| Gateway reads secrets directly | Medium | The gateway process opens `secrets/api_key.hex` for its `/health` check. This couples the gateway to a specific secrets layout and widens the secret-exposure surface. |
| Auth-gated health probe | Medium | The Core API requires an API key for `/health`. This is atypical for liveness checks and forced the gateway to handle secrets. |
| Hardcoded API base URL | Medium | React components hardcode `localhost:3001`. The dashboard fails when accessed from a non-localhost host. |
| Mock data in Control Center | Low–Medium | Metrics and service statuses are synthetic. They do not reflect the Python core’s actual state. |
| No process supervision | High | A crash in any of the three processes leaves no auto-restart mechanism. PID files become stale. |
| No unified startup command | Medium | Three separate background commands must be run in order. |
| Unreviewed staged changes | Medium | A file in `src/.../registry.py` appears as added in git. Its provenance is unverified. |
| CSP weakened for HTTP | Low | `upgrade-insecure-requests` was removed to fix asset loading. This blocks automatic HTTPS upgrade if TLS is added later. Trade-off is acceptable for HTTP dev mode. |

### 2.3 Correct Actions Taken

- Root cause (CSP directive) was identified by inspection rather than trial-and-error.
- The documented npm stack was adopted once the docs were consulted.
- A registry module was hardened with `.get()` defaults and schema migrations to prevent proxy crashes on column mismatches.
- End-to-end inference was verified with governance checkpoints enabled.
- Modified files were syntax-checked and unit-tested where applicable.

---

## 3. Recommendations

1. **Canonicalize the deployment mode.** Either adapt `start_server.sh` to launch the npm stack, or document the two modes with clear selection criteria (e.g., “use 8443 for projection-layer testing; use 3000 for dashboard development”).
2. **Create a unified startup script** (e.g., `scripts/start_dashboard_stack.sh`) that launches all three services with correct env, working directory, and log rotation.
3. **Add process supervision.** Use systemd user units, supervisord, or pm2 to auto-restart crashed processes and keep PID files accurate.
4. **Make the dashboard API base runtime-configurable.** Inject `API_BASE` at build time or use a relative `/api` path so the dashboard works across hosts.
5. **Review startup-script changes with deployment owners before commit.** The env-var and model-name changes may break external automation.
6. **Decouple health checks from auth.** Either expose `/health` without authentication, or provide a dedicated `/ready` endpoint for the gateway to probe with a service account.
7. **Run the full verification gate** (`verify_health.sh`, `npm run smoke`) before marking the stack operational.

---

## 4. Conformance

- **Plane 1 / Plane 2 separation:** Verified. Chronology (§1) contains no evaluative language. Analysis (§2) contains no new facts.
- **Third person:** Verified. No first-person pronouns.
- **Generic identifiers:** Verified. Project names replaced with role descriptions.
- **No interpersonal framing:** Verified. No references to operator emotion, credit expenditure, or relational dynamics.
- **No hallucinated identifiers:** Verified. All env vars, paths, and ports match the source document or are genericized.
