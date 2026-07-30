# Egregore Build-Time Pipeline — M1/M2 Contract

**Status:** Prototype-phase contract  
**Scope:** Command-line pipeline that enforces module-level CBI-0 gates and emits signed `.zarc` bundles.  
**Goal:** Provide a deterministic, replay-correct build gate that the runtime can trust.

---

## 1. Pipeline overview

The pipeline runs in two phases:

- **M1 — Module gate:** checks a single module for plane/layer import boundaries and manifest completeness.
- **M2 — Graph gate:** checks a set of modules for a consistent, acyclic dependency graph.

Both phases emit human-readable JSON reports **and** append a signed entry to a `.zarc` chain.

---

## 2. M1 contract

### 2.1 CLI

```bash
python -m egregore.tooling.module_pipeline check \
  --module-dir src/egregore/<module> \
  --class {fast,standard} \
  --out-dir pipeline_outputs/ \
  [--signing-key-hex $EGREGORE_SIGNING_KEY_HEX]
```

### 2.2 Inputs

| Input | Description |
|-------|-------------|
| `module_dir` | Directory or file path under `src/egregore/`. |
| `pipeline_class` | `fast` runs M1 only; `standard` runs M1+M2+M5. |
| `out_dir` | Root directory for outputs. |
| `signing_key_hex` | Ed25519 signing key (hex). Falls back to env var or a test key. |

### 2.3 Process

1. Resolve module path → `module_id` (e.g., `egregore.shared`).
2. Load `egregore-module.json` if present; otherwise infer a manifest from the directory.
3. Walk all `*.py` files (skip `test_*.py`).
4. Collect imports, capabilities, and source snippets.
5. Run checkers:
   - **M1:** plane/layer boundary enforcement.
   - **M2:** dependency declaration, version pinning, capability declaration.
   - **M5:** cell-awareness stub (non-fatal).
6. Compute pass/fail.
7. Canonicalize manifest and report with `egregore.shared.canonical.canonical_dumps`.
8. Sign the canonical payload with Ed25519.
9. Write outputs and append to `bundle.zarc`.

### 2.4 Outputs

```
<out_dir>/<module_id>/
├── egregore-module.json   # inferred/loaded manifest
├── audit_report.json       # M1/M2/M5 results
└── bundle.zarc             # signed provenance entry
```

### 2.5 Pass criteria

- `fast`: M1 status == `PASS`
- `standard`: M1 status == `PASS` AND M2 status in {`PASS`, `WARN`}
- M5 never fails

CLI exit code is `0` on pass, `1` on fail.

---

## 3. M2 contract

### 3.1 CLI

```bash
python -m egregore.tooling.module_pipeline graph \
  --modules src/egregore/domain src/egregore/application ... \
  --out-dir pipeline_outputs/ \
  [--signing-key-hex $EGREGORE_SIGNING_KEY_HEX]
```

### 3.2 Inputs

| Input | Description |
|-------|-------------|
| `modules` | One or more module directories. |
| `out_dir` | Root directory for outputs. |
| `signing_key_hex` | Ed25519 signing key (hex). |

### 3.3 Process

1. Run M1 for every module. Abort if any M1 fails.
2. Load the `egregore-module.json` for each module.
3. Build a dependency graph from `cbi0.m2_dependencies`.
4. Detect:
   - **Undeclared edge:** module A imports B but B is not in A’s declared dependencies.
   - **Version mismatch:** two modules pin the same dependency to different versions.
   - **Hash mismatch:** the same module version has different declared hashes.
   - **Cycle:** a cycle exists in the declared dependency graph.
5. Produce a graph report.
6. Sign and append to `graph.zarc`.

### 3.4 Outputs

```
<out_dir>/
├── m2_graph_report.json
└── graph.zarc
```

### 3.5 Pass criteria

- No undeclared edges
- No version mismatches
- No hash mismatches
- No cycles

CLI exit code is `0` on pass, `1` on fail.

---

## 4. Sandbox orchestration

The `scripts/pipeline_sandbox.py` entrypoint turns the M1/M2 CLI into a
compulsory, CI-gated sandbox for the whole `src/egregore/` tree.

```bash
make sandbox
```

### 4.1 Behavior

1. Discover every top-level directory under `src/egregore/` that contains at
   least one non-test `.py` file.
2. For each module:
   - If `egregore-module.json` exists, run `module_pipeline check --class standard`.
   - Otherwise, run `module_pipeline check --class fast` and emit a warning that
     M2 is skipped until a manifest is committed.
3. Collect all modules that have manifests and run `module_pipeline graph`.
4. Write an aggregate report (`sandbox_outputs/aggregate_report.json`) and a
   signed aggregate provenance entry (`sandbox_outputs/sandbox.zarc`).
5. Exit `0` if every M1 check and the graph audit pass; otherwise exit `1`.

### 4.2 Configuration

| Variable | Purpose |
|----------|---------|
| `EGREGORE_SIGNING_KEY_HEX` | Ed25519 signing key. Falls back to a deterministic test key. |
| `EGREGORE_SANDBOX_OUT_DIR` | Output directory (default: `sandbox_outputs`). |
| `EGREGORE_SANDBOX_STRICT` | If set, fail any module without a manifest. |
| `EGREGORE_SANDBOX_SRC_ROOT` | Source root for monorepo/testing (default: repo root). |

### 4.3 Outputs

```
sandbox_outputs/
├── egregore/<module>/
│   ├── egregore-module.json
│   ├── audit_report.json
│   └── bundle.zarc
├── aggregate_report.json
└── sandbox.zarc
```

---

## 5. Data flow

```
source files
    │
    ▼
AST import scan + capability scan
    │
    ▼
ModuleManifest (inferred or loaded)
    │
    ▼
canonical_dumps(manifest) ──► sha256_hex ──► Ed25519 sign
    │
    ▼
AuditReport
    │
    ▼
canonical_dumps(report) ──► sha256_hex ──► Ed25519 sign
    │
    ▼
ProvenanceEvent(engine="module_pipeline", event="module_audited")
    │
    ▼
Provenance.append(...) ──► bundle.zarc
```

**Canonicalization rule:** every payload written to `.zarc` is serialized with `egregore.shared.canonical.canonical_dumps`. No direct `json.dumps` is used on provenance-bound data.

**Signing rule:** the signature covers the canonical bytes of the unsigned entry (`ts_ns`, `engine`, `event`, `payload`, `prev_hash`). This matches the existing `egregore.kernel.provenance.Provenance.append` behavior.

---

## 6. Manifest schema

The manifest file `egregore-module.json` follows the existing `ModuleManifest` Pydantic model:

```json
{
  "module_id": "egregore.shared",
  "version": "0.6.0",
  "cell": null,
  "cbi0": {
    "m1_plane": "shared",
    "m1_layer": "shared",
    "m1_interface_concrete": false,
    "m2_dependencies": [
      {"module": "egregore.domain.models", "version": "0.6.0", "hash": "sha256:..."}
    ],
    "m2_capabilities": {
      "read": [],
      "write": [],
      "execute": [],
      "network": []
    },
    "m2_ports": {
      "implements": [],
      "requires": []
    },
    "m5_cell_aware": false
  }
}
```

`m2_capabilities` entries contain line-level usage snippets, not just booleans.

---

## 7. Report schema

The `audit_report.json` follows the existing `AuditReport` Pydantic model:

```json
{
  "module_id": "egregore.shared",
  "timestamp_ns": 1234567890000000000,
  "pipeline_class": "standard",
  "m1": {"status": "PASS", "violations": []},
  "m2": {"status": "PASS", "violations": [], "metadata": {}},
  "m3": {"status": "NOT_VERIFIED", "note": "Manual audit required for non-reentry"},
  "m4": {"status": "DIVERGED", "note": "No spec file provided; equivalence not checked"},
  "m5": {"status": "NOT_ENFORCED", "violations": [], "metadata": {}}
}
```

---

## 8. Future phases

| Phase | Scope | When |
|-------|-------|------|
| M3 | Terminal non-reentry audit | Manual / runtime gate |
| M4 | Spec/runtime equivalence | Cell-level harness |
| M5 | Full cell-awareness gate | After M1/M2 pipeline is stable |
| Dashboard | Web UI for reports and graph | Day 9–10 |

---

## 9. Dependencies

- `egregore.shared.canonical` — deterministic JSON serialization.
- `egregore.kernel.ed25519_signer` — Ed25519 signing/verification.
- `egregore.kernel.provenance` — `.zarc` chain appending and verification.
- `egregore.infrastructure.zarc_provenance_sink` — optional adapter for `IProvenanceSink`.
