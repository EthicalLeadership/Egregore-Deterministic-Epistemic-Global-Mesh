# Egregore Deployment Pipeline

## Scope

This document describes the build, gate, integration, deploy, and smoke stages
for Egregore. It supports control **C4.3 — Deployment Pipeline**.

The primary development workflow is npm-first with a project-managed Python
virtual environment (`.venv`). A Docker Compose stack is available as an
alternative but uses non-standard host ports to avoid collision with the npm
workflow. See `DEPLOYMENT.md` for environment setup.

---

## Stage / Gate Structure

```
1. Build
   └─ npm install + pip install
2. Gate: test_arch_enforcement.py
3. Gate: test_cbi_0_enforcement.py
4. Gate: module-level CBI-0 pipeline (M1/M2/M5)
5. Gate: test_gate5_invariants.py
6. Integration: test_integration_adapters.py + test_nats_broker.py
7. Gate: test_lang_determinism.py
8. Deploy
   ├─ systemd (Pioneer 1)
   └─ Docker (Client Red Dart)
9. Smoke
   ├─ /ready
   └─ obs.pulse.<node_id>
```

### 1. Build

```bash
cd ~/egregore
npm install
source .venv/bin/activate
pip install -e ".[messaging,persistence,telemetry]"
```

Inputs:

- `pyproject.toml`
- `package.json`
- `package-lock.json`
- `src/`

Outputs:

- Installed Node modules
- Installed Python package in `.venv`

### 2. Gate: Architecture purity

```bash
python -m pytest tests/test_arch_enforcement.py -q
```

Purpose: enforce the layer dependency matrix, domain purity, and closed import
surfaces. A failure here blocks all later stages.

### 3. Gate: CBI-0

```bash
python -m pytest tests/test_cbi_0_enforcement.py -q
```

Purpose: verify M1 projection access, M2 registry completeness, M3 terminal
non-reentry, and M4 spec/runtime equivalence.

### 4. Gate: Module-level CBI-0 pipeline (M1/M2/M3/M5 sandbox)

Run the full sandbox on every module under `src/egregore/`:

```bash
make sandbox
```

The sandbox discovers each top-level module, runs the appropriate pipeline class,
and writes signed provenance to `sandbox_outputs/`:

- `sandbox_outputs/egregore/<module>/egregore-module.json` — inferred or existing manifest
- `sandbox_outputs/egregore/<module>/audit_report.json` — M1/M2/M3/M4/M5 results

#### M3 — Terminal Non-Reentry

The manifest may declare a module as terminal:

```json
{
  "module_id": "egregore.application.heavyweight_state",
  "cbi0": {
    "m3": {"terminal": true}
  }
}
```

A terminal module asserts that it is non-reentrant and that a certified
decommissioning plan exists. The manifest must include a `decom_manifest`
with either a signed Dependency Safety Board attestation or a documented
bootstrap waiver:

```json
{
  "module_id": "egregore.application.heavyweight_state",
  "cbi0": {
    "m3": {
      "terminal": true,
      "decom_manifest": {
        "dependencies": ["egregore.interface.api"],
        "procedure": "docs/decom/egregore.application.heavyweight_state.md",
        "test_log": "logs/decom/egregore.application.heavyweight_state.log",
        "attestation": {
          "signature": "...",
          "signer_id": "dsb-chair",
          "timestamp": "2026-07-19T00:00:00Z"
        }
      }
    }
  }
}
```

The standard pipeline class **fails** a terminal module that lacks a decom
manifest or a valid attestation. Cascade-prone teardown patterns (`__del__`
destructors or `atexit` hooks) are reported as warnings but do not block the
build once the attestation is present. See `docs/governance/bootstrap_waiver.md`
for the temporary waiver protocol.
- `sandbox_outputs/egregore/<module>/bundle.zarc` — signed per-module bundle
- `sandbox_outputs/aggregate_report.json` — human-readable summary
- `sandbox_outputs/sandbox.zarc` — signed aggregate provenance

#### Pipeline classes

- **Fast** (default): runs M1 layer-dependency checks only. Used for modules that
  have not yet committed a `egregore-module.json`.
- **Standard**: runs M1 + M2 dependency/capability checks + M3 terminal
  non-reentry stub + M5 cell-awareness stub. Automatically selected once a module
  contains `egregore-module.json`.

A module graduates from fast to standard by committing a manifest:

```bash
python -m egregore.tooling.module_pipeline init-manifest \
  --module-dir src/egregore/<module> \
  --out src/egregore/<module>/egregore-module.json
```

#### Per-module CLI (still available)

```bash
# Fast class: M1 only
python -m egregore.tooling.module_pipeline check \
  --module-dir src/egregore/<module> \
  --class fast \
  --out-dir audit_outputs

# Standard class: M1 + M2 + M3 + M5 stub
python -m egregore.tooling.module_pipeline check \
  --module-dir src/egregore/<module> \
  --class standard \
  --out-dir audit_outputs
```

Purpose: enforce per-module plane/layer import boundaries (M1), manifest
completeness for dependencies and capabilities (M2), terminal non-reentry
declaration (M3), and record a non-fatal cell-awareness stub (M5) for modules
that use model/agent infrastructure. This is a build-time gate that runs
**before** the runtime CBI-0 chain.

A module passes the standard class when:
- M1 status is `PASS`
- M2 status is `PASS` or `WARN`
- M3 status is `PASS`, `WARN`, or `NOT_ENFORCED` (`FAIL` blocks deployment)
- M5 status is `NOT_ENFORCED` (it never fails)

#### Configuration

- `EGREGORE_SIGNING_KEY_HEX` — Ed25519 key for `.zarc` signing. Falls back to a
  deterministic test key when unset (e.g., PR builds).
- `EGREGORE_SANDBOX_STRICT=1` — fail if any module lacks `egregore-module.json`.
  Useful for release branches.
- `EGREGORE_SANDBOX_OUT_DIR` — output directory (default: `sandbox_outputs`).
- `EGREGORE_SANDBOX_SRC_ROOT` — source root for monorepo/testing (default: repo root).

#### Interface Synod Dashboard

The dashboard turns the signed sandbox output into a governable view for the
Human Assembly, AI Conclave, and Interface Synod. It reads the enriched
`aggregate_report.json` and shows module terminal posture, attestation badges,
decommissioning manifests, dependency graphs, and waiver countdowns.

```bash
# 1. Produce the report (required before first dashboard load)
make sandbox

# 2. Start the dashboard
make dashboard
```

Open the URL printed by the launcher. The dashboard tries port `8000` by
default and automatically falls back to the next free port if it is occupied.
You can also override the preferred port:

```bash
make dashboard DASHBOARD_PORT=8765
```

The report path can be overridden with `EGREGORE_SANDBOX_OUTPUT`.

Dashboard features:

- Summary banner: total, terminal, attested, waiver, missing, and expiring-waiver counts.
- Module table: filterable by layer and searchable by name.
- Attestation badges: `SIGNED`, `WAIVER`, `MISSING`, `NOT TERMINAL`.
- Module detail: full manifest, decom manifest, attestation details, aggregated violations, D3 dependency graph.
- Governance action panel: heuristic waiver expiry countdown and placeholder "Initiate Review" action.

### 5. Gate: Gate 5 invariants

```bash
python -m pytest tests/test_gate5_invariants.py -q
```

Purpose: confirm replay bounded invariance and reasoning-guard correctness.

### 5. Integration

```bash
python -m pytest tests/test_integration_adapters.py tests/test_nats_broker.py -q
```

Purpose: validate end-to-end adapter flow, provenance chain verification,
Anchorum bridge ingest, pulse adapter publish, and JetStream idempotent
bootstrap.

### 6. Gate: Language determinism

```bash
python -m pytest tests/test_lang_determinism.py -q
```

Purpose: property-based round-trip, AST determinism, grammar version lock, and
AST bounds for the `myth.lang` DSL.

> This gate is skipped if `hypothesis` or `myth.lang` is not installed.

### 7. Deploy

#### Pioneer 1 (systemd)

Path: `deploy/systemd/`

```bash
sudo systemctl daemon-reload
sudo systemctl restart egregore-api
sudo systemctl status egregore-api
```

Environment variables required:

- `EGREGORE_DB_URL`
- `REDIS_URL`
- `NATS_URL`
- `EGREGORE_SIGNING_KEY_PATH`
- `EGREGORE_CLUSTER_KEK_PATH`
- `NODE_ID`

#### Client Red Dart (Docker)

```bash
docker compose build
docker compose up -d --wait
```

Default host ports are non-standard to avoid collision with npm local services:

| Service | Host port | Container port |
|---------|-----------|----------------|
| HTTP API | 18000 | 8000 |
| Prometheus metrics | 19000 | 9000 |
| Postgres | 15432 | 5432 |
| Redis | 16379 | 6379 |
| NATS client | 14222 | 4222 |
| NATS monitoring | 18222 | 8222 |

See `docker-compose.yml` and `Dockerfile` for full configuration.

### 8. Smoke

```bash
curl -fsS http://localhost:8002/ready | jq .
```

Expected response:

```json
{
  "status": "ready",
  "checks": {
    "db": "ok",
    "redis": "ok",
    "nats": "ok"
  }
}
```

Verify pulse subject:

```bash
# Subscribe to node telemetry (requires nats CLI)
nats --server $NATS_URL sub "obs.pulse.<node_id>"
```

---

## Artifact Provenance

Every deploy must record:

| Field | Source | Storage |
|-------|--------|---------|
| Git SHA | `git rev-parse HEAD` | CI artifact / deploy log |
| Test results | `pytest --tb=short` | CI artifact |
| M4 record | `cbi0_governance.py` audit log | `zarc_journal.py` / provenance chain |
| Build provenance | npm + pip install logs | CI artifact |
| Deploy target | `NODE_ID`, `CLUSTER_NAME` | Runtime env |

Minimum retention: 7 years, aligned with the legal dossier `.zarc` retention
schedule in `docs/data_governance.md`.

---

## Rollback

### Immediate kill switch

Before reverting code, use `feature_flag_registry.py` to disable the failing
capability:

```python
from egregore.application.feature_flag_registry import FeatureFlagRegistry, FeatureFlag

registry = FeatureFlagRegistry()
registry.register(FeatureFlag(name="chat_completions", enabled=False))
registry.register(FeatureFlag(name="vertical_inference_<name>", enabled=False))
```

Kill switch flags:

| Capability | Flag name example |
|------------|-------------------|
| Chat completions | `chat_completions` |
| Vertical inference | `vertical_inference_<vertical>` |
| Intake upload | `intake_upload` |
| Workflow test-health | `workflow_test_health` |

### Deployment rollback

1. Identify last known good git SHA from artifact provenance.
2. Run the full gate suite on the target SHA.
3. Deploy using the Pioneer 1 systemd or Client Red Dart Docker path.
4. Run smoke tests (`/ready`, `obs.pulse.<node_id>`).
5. Notify on-call and update incident log.

Detailed rollback steps: `DEPLOYMENT.md`.

---

## CI/CD Notes

- GitHub Actions: `.github/workflows/ci.yml` exists and runs the gate stages
  above (architecture purity, CBI-0, Gate 5 invariants) on every push.
  The rework budget for any failed gate is 2 remediation attempts; a third
  failure escalates to the architecture board before merge is reconsidered
  (aligned with the factory QC rework budget in `config/factory_policy.json`).
- Where CI coverage is missing for a stage, run it manually or via the npm
  scripts in `package.json` (`npm run test:arch`, `npm test`).
- The gate tests are intentionally fast and dependency-light so they can run in
  any environment where `src/` is importable.

---

## Reference Files

- `DEPLOYMENT.md`
- `docker-compose.yml`
- `Dockerfile`
- `src/egregore/application/feature_flag_registry.py`
- `tests/test_arch_enforcement.py`
- `tests/test_cbi_0_enforcement.py`
- `tests/test_gate5_invariants.py`
- `tests/test_integration_adapters.py`
- `tests/test_nats_broker.py`
- `tests/test_lang_determinism.py`
- `config/prometheus.yml`
- `src/egregore/http_api/http/app.py`
