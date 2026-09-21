# Egregore runtime skeleton (spec-integrity first)

This repository currently contains a **CPU-only, deterministic** runtime skeleton aligned with the spec-driven intent found in `../egregore_ops_layer/*`.

## What’s implemented

- `.zarc` provenance writer:
  - canonical JSON
  - SHA256 hash chain (`prev_hash`)
  - Ed25519 signature (PyNaCl)
  - `verify_chain()` for integrity checking
- `.zarc` → dfih `ExecutionTrace` bridge with strict field mapping
- Governance adapters (injected callables, no hard dependencies):
  - `DagSigner`
  - `AnchorumBridge` (.zarc → injected vault ingest callable)
  - `LitigationHoldTrigger` (delegation wrapper)
- Powertrain logic:
  - deterministic `Gearbox` (G0/G2/G5 with hysteresis + cooldown)
  - `ThermalGovernorTestMode` emits `.zarc` events on G5
- Telemetry & messaging:
  - `PulseAdapter` publishes JSON payloads to `obs.pulse.<node_id>`
  - `bootstrap_jetstream` idempotently bootstraps JetStream streams via injected `add_stream`

## Tests

All tests are CPU-only and use injected mocks.
Run:

```bash
pytest -q
```

## Interface Synod Dashboard

After running `make sandbox`, start the governance dashboard with:

```bash
make dashboard
```

Open the URL printed by the launcher to inspect module terminal posture,
attestations, decommissioning manifests, and dependency graphs. The launcher
falls back to the next free port if the default is occupied. Use
`make dashboard DASHBOARD_PORT=8765` to request a specific port.
See `docs/pipeline.md` for details.

## ANCHORUM offline ingest + comparison (repo-local)

This repo includes **offline, deterministic** CLI tools that run `AnchorumBridge.sync()` against a **local `.zarc` JSONL** file and compare two ingests.

### 1) Run ingestion against a single `.zarc`

```bash
python3 anchorum_ingest.py \
  --zarc path/to/run.zarc \
  --last-n 100 \
  --outdir runs/anchorum_ingest_report \
  --record-limit 10000
```

Output files in `--outdir`:

- `anchorum_ingest_report.json`
- `anchorum_ingest_report.md`

### 2) Compare two `.zarc` sources

```bash
python3 anchorum_compare.py \
  --left path/to/left.zarc \
  --right path/to/right.zarc \
  --last-n 100 \
  --outdir runs/anchorum_compare_report \
  --record-limit 10000
```

Output files in `--outdir`:

- `anchorum_ingest_comparison.json`
- `anchorum_ingest_comparison.md`

### Notes about verification fields

The governance-layer ingest runner in this repo is **architecture-restricted** and does **not** call `Provenance.verify_chain()`; therefore `verify_chain_ok` is currently `null` in reports. Payload/key diffs are computed from the ingest batch content.

## Notes

- Optional real integrations (GPU/NVML, NATS, ANCHORUM, dfih) are intentionally not imported directly; adapters are injection-based so CI does not depend on those systems.

## Local AI Models On Disk (vertical-aware)

- Use the manifest template in [docs/local_model_catalog.example.json](docs/local_model_catalog.example.json).
- Configure one GGUF per vertical (for example: legal, operations, dt1) and pin `policy_versions` per model.
- Hash pinning is fail-closed: if `expected_sha256` does not match the model on disk, routing fails.

Example usage:

```python
from egregore.infrastructure.local_model_catalog import LocalModelCatalog

catalog = LocalModelCatalog.from_manifest_file("docs/local_model_catalog.example.json")
adapter = catalog.build_adapter(
  vertical="legal",
  policy_version="policy_v1",
  speed_tier="fast",
)
