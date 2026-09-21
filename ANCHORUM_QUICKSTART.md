# ANCHORUM Quickstart — Forensic Evidence Analysis

ANCHORUM is Egregore's forensic engine: it ingests an evidence directory,
extracts entities and timelines, detects anomalies, and produces a signed,
court-grade report with a tamper-evident `.zarc` provenance chain.

This guide gets a legal-tech buyer or pilot user from zero to a signed
forensic report in one sitting.

## What ANCHORUM produces

For each batch run you get:

- A **JSON report** — artifacts, extracted entities, fused timeline, anomaly
  findings with severities (`critical`, `high`, `medium`, `low`, `info`).
- A **`.zarc` provenance chain** — Ed25519-signed, hash-chained journal of
  every ingestion and analysis step (verifiable offline).
- A **signed final report** via `DagSigner` when a signing key is supplied.
- An optional **LLM case narrative** generated through the Egregore runtime
  at temperature 0 with a fixed seed (deterministic, replayable).

If the Egregore runtime or a signing key is unavailable, the runner falls
back to standalone JSON-only mode and says so in the output
(`mode=standalone` vs `mode=runtime`).

## Prerequisites

- The repo installed per `GETTING_STARTED.md` (Python 3.11/3.12, `.venv`)
- An evidence directory (documents, emails, spreadsheets — anything the
  extractors support; oversized files are skipped, not fatal)
- An Ed25519 signing key (hex) for signed output

## 1. Run a forensic batch (CLI)

Entry point: `src/anchorum/forensic/core/batch_runner.py`

```bash
source .venv/bin/activate
PYTHONPATH=src python -m anchorum.forensic.core.batch_runner \
  --input /path/to/evidence \
  --output report/case_001_report.json \
  --case-id CASE-001 \
  --operator "J. Analyst" \
  --signing-key "$ANCHORUM_SIGNING_KEY" \
  --zarc-path report/case_001.zarc \
  --deep-revision \
  --verbose
```

Key flags:

| Flag | Purpose |
|------|---------|
| `--input` | Evidence directory (required) |
| `--output` | JSON report path (required) |
| `--case-id` / `--operator` | Case and operator identity, recorded in provenance |
| `--signing-key` | Ed25519 key hex (or `ANCHORUM_SIGNING_KEY` env) |
| `--zarc-path` | Provenance output (or `ANCHORUM_ZARC_PATH` env) |
| `--enforce-readonly` | Refuse non-read-only source files (evidence hygiene) |
| `--deep-revision` | Option B deep revision recovery + hidden-content inspection |
| `--llm-model-id` | Egregore model for the case narrative (`ANCHORUM_LLM_MODEL_ID` env) |
| `--llm-temperature` / `--llm-seed` | Default 0.0 / 42 — deterministic narrative |

On success the runner prints a one-line summary:

```
ANCHORUM_BATCH_DONE: artifacts=128 entities=342 anomalies=17 critical=2 high=5 mode=runtime report=...
```

## 2. Understand the pipeline

| Stage | Code | What it does |
|-------|------|--------------|
| Ingestion | `src/anchorum/forensic/core/ingestion.py` | Container detection, artifact hashing, extractor dispatch |
| Extraction | `src/anchorum/forensic/core/extraction/`, `odt_extractor.py`, `ooxml_extractor.py` | Text and metadata extraction per file type |
| Canonicalization | `src/anchorum/forensic/core/canonicalization.py` | Entity extraction and merge to canonical JSON |
| Timeline fusion | `src/anchorum/forensic/core/timeline_fusion.py` | Cross-artifact chronological reconstruction |
| Analysis & report | `src/anchorum/forensic/core/batch_runner.py` | Anomaly detection, severity scoring, signed report |
| Provenance | `src/anchorum/forensic/core/provenance.py` | `.zarc` emission into the Egregore runtime |

## 3. Verify the provenance chain

```bash
PYTHONPATH=src python - <<'PY'
from pathlib import Path
from egregore.kernel.provenance import Provenance
p = Provenance(
    Path("report/case_001.zarc"),
    signing_key_hex=open("secrets/signing_key.pem").read().strip()[:64],
)
assert p.verify_chain(), "chain verification failed"
print("chain_ok: True")
PY
```

Verification is fail-closed: `verify_chain()` recomputes every hash link and
signature and returns `False` on any discrepancy.

## 4. Desktop client (optional)

For interactive review without a browser, the native Tkinter client talks to
the local ANCHORUM API on `127.0.0.1:8080`:

```bash
.venv/bin/python anchorum_desktop.py
```

It requires the ANCHORUM site service running and reads its API key from
`secrets/api_key.hex`. Entry point: `anchorum_desktop.py` (repo root).

## 5. Operating notes

- **Determinism:** with `--llm-temperature 0` and a fixed `--llm-seed`, the
  same evidence directory produces a byte-identical report. This is the
  property that makes ANCHORUM output replayable in court.
- **Litigation hold:** when integrated with the runtime, a
  `LitigationHoldTrigger` fires before the batch; held cases cannot be
  modified or deleted.
- **Retention:** forensic `.zarc` artifacts follow the schedule in
  `docs/data_governance.md` (7 years post-case closure for legal dossiers).

## Reference

- Runbook and health checks: `docs/anchorum/runbook.md`
- HTTP API reference: `docs/anchorum/api_reference.md`
- Integrity gate: `src/egregore/governance/anchorum_integrity_gate.py`
- Incident response: `docs/runbook.md` (ANCHORUM section)
