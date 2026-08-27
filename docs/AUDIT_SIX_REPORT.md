# AUDIT-SIX — Integration Blueprint: CLI ↔ Anchorum

## VERDICT

**The blueprint does not bind to the present codebase.** The spec assumes:

1. A CLI agent layer (`src/legal_agent/`, `BaseAgent` with `.process()`) — **does not exist**.
2. Anchorum exposes `POST /v1/deterministic/evaluate` and `POST /v1/epistemic/consistency` — **do not exist**.
3. Types `Proposition`, `EpistemicStatus` (`ASSERTED`/`CONTRADICTED`/`INFERRED`), `AnchorumAdapterInterface`, `RawAnchorumOutput` — **do not exist**.
4. The translation `EpistemicStatus(anchorum_json["status"])` — a literal enum-value map that would raise `ValueError` because Anchorum emits no such tokens.

The "1:1 string replacement on the `_call` paths" advice in the original audit is **not salvageable**: the contract mismatch is structural (batch engine vs. HTTP service, different enums, nonexistent agent layer), not cosmetic (path strings).

---

## 1. What the REAL Anchorum is

Anchorum is a **forensic narrative-trace pipeline**. It ingests counterparty communications + documentary evidence, cross-references claims against evidence, detects contradictions / burden shifts / date inconsistencies, and emits a tribunal-ready *Demolition Timeline*. There is **no rule-evaluate endpoint and no consistency endpoint**.

### Real entry points (verified by reading the source)

| Layer | Location | Signature / I/O |
|---|---|---|
| **Batch engine** | `anchorum/pipeline/narrative_trace/analysis/tracer.py` | `NarrativeTracer.trace(communications_glob: str, evidence_glob: str, output_path: str \| None = None, case_id: str = "molson-001") -> DemolitionTimeline` |
| **Ingestor** | `anchorum/pipeline/narrative_trace/ingestion/ingestor.py` | `FileIngestor.ingest(path, category_hint=None) -> CommunicationRecord \| EvidenceRecord` |
| **Types** | `anchorum/pipeline/narrative_trace/analysis/models.py` | `CommunicationRecord`, `EvidenceRecord`, `TimelineEntry`, `DemolitionTimeline`, `SourceProvenance` |
| **HTTP module** | `src/modules/anchorum/interface/routes.py` | `POST /modules/anchorum/ingest`, `POST /modules/anchorum/analyze`, `GET /modules/anchorum/chronology/{case_id}` |
| **ASDS domain** | `src/asds/domain/{models,ports}.py`, `src/asds/application/anchorum_adapter.py` | `Evidence`, `OutputVersion`, `IAnchorumIngestionPort`, `AnchorumAdapter` |

### The real epistemic enum — this is the crux

`anchorum/pipeline/narrative_trace/analysis/models.py` defines:

```python
class EpistemicTag(str, Enum):
    FACT      = "FACT"       # Directly observable from primary source
    DERIVED   = "DERIVED"    # Logical inference from multiple FACTs
    MODEL     = "MODEL"      # Pattern match against known adversarial tactics
    UNCERTAIN = "UNCERTAIN"  # Insufficient evidence to determine

class BurdenShiftTag(str, Enum):  # YES | NO | IMPLICIT
class Severity(str, Enum):        # CRITICAL | HIGH | MEDIUM | LOW
```

The spec's `EpistemicStatus` (`ASSERTED` / `CONTRADICTED` / `INFERRED`) **does not exist**. The nearest real concept is `EpistemicTag`, but its values differ entirely, and it is attached to `TimelineEntry.verdict` — a per-entry tag on a whole-case timeline, not a per-proposition status.

---

## 2. What the REAL "CLI Agents" are

There is **no `src/legal_agent/`**, no `BaseAgent` Python class, no `.process()` method. The "agents" are:

| Path | What it actually is |
|---|---|
| `agents/claude-agent.json`, `agents/example-agent.json` | JSON **config scaffolds** only: `{"description": "...", "timeout": 60, "allowed_roles": [...]}` — no executable logic |
| `cells/anchorum_forensic/executor.py` | An Egregore **cell executor**: `run_forensic_analysis(context, input_path, case_id, operator, llm_model_id)` → `{verdict, highest_severity, summary, report, artifact_details, output_path, case_id, executed_at}` |
| `src/egregore/cli/admin.py` | CLI admin commands — not an agent with `.process()` |
| `src/egregore/aegis_hive/` | Module entry (`schemas.py`, `tools.py`) — not `BaseAgent` |

### ⚠️ Dead/broken import found

`cells/anchorum_forensic/executor.py` line 23:

```python
from anchorum.forensic.core.batch_runner import run_batch
```

**`anchorum.forensic.core.batch_runner` does not exist in this repo.** The top-level `anchorum/` package contains only `pipeline/narrative_trace/`. Unless `anchorum.forensic` is supplied by a separate installed package, this cell executor will `ImportError` at runtime. This needs to be resolved independently of this blueprint.

---

## 3. Spec vs. Reality — Mapping Table

| Spec element | Spec assumes | Reality | Gap |
|---|---|---|---|
| Agent layer | `src/legal_agent/`, `BaseAgent.process()` | No such package/class; agents are JSON scaffolds + cell executors | **Structural** |
| Deterministic endpoint | `POST /v1/deterministic/evaluate` → `{proposition_text, status: ASSERTED\|CONTRADICTED, source_id}` | No such endpoint. Closest: batch `trace()` or `POST /modules/anchorum/analyze` (manipulation report) | **Nonexistent** |
| Epistemic endpoint | `POST /v1/epistemic/consistency` → `{proposition_text, inference_chain, supports}` | No such endpoint. Closest: `NarrativeTracer.trace()` → `DemolitionTimeline` | **Nonexistent** |
| Epistemic enum | `EpistemicStatus`: `ASSERTED`/`CONTRADICTED`/`INFERRED` | `EpistemicTag`: `FACT`/`DERIVED`/`MODEL`/`UNCERTAIN` | **Different values** |
| Proposition type | `Proposition(text, status, evidence_ids, inference_chain)` | No `Proposition`; real atoms are `TimelineEntry` / `EvidenceRecord` | **Nonexistent** |
| Adapter base | `AnchorumAdapterInterface`, `RawAnchorumOutput` in `.base_adapter` | Not found anywhere | **Nonexistent** |

---

## 4. Corrected Integration Contract (Target)

The audit's *goal* — a CLI layer that asks Anchorum two questions — is legitimate, but it must bind to **real** Anchorum. Two viable target contracts:

### Option A — Bind to the real batch engine (recommended)

Call `NarrativeTracer.trace()` directly. Map the real output to a `Proposition`-like form using a **wrapper** that translates `EpistemicTag` → your desired status vocabulary.

```python
from anchorum.pipeline.narrative_trace.analysis.models import (
    DemolitionTimeline, TimelineEntry, EpistemicTag,
)

# Target vocabulary. Values are YOUR contract, not Anchorum's wire enum.
# You define this; it is NOT taken from Anchorum source.
from enum import Enum

class EpistemicStatus(str, Enum):
    ASSERTED      = "ASSERTED"       # Real: EpistemicTag.FACT or DERIVED with strong score
    CONTRADICTED  = "CONTRADICTED"   # Real: evidence directly refutes a claim
    INFERRED      = "INFERRED"       # Real: EpistemicTag.DERIVED or MODEL
    UNCERTAIN     = "UNCERTAIN"      # Real: EpistemicTag.UNCERTAIN

def translate_timeline_entry(entry: TimelineEntry) -> dict:
    """Map a real Anchorum TimelineEntry to the spec's Proposition shape."""
    status = {
        EpistemicTag.FACT: EpistemicStatus.ASSERTED,
        EpistemicTag.DERIVED: EpistemicStatus.INFERRED,
        EpistemicTag.MODEL: EpistemicStatus.INFERRED,
        EpistemicTag.UNCERTAIN: EpistemicStatus.UNCERTAIN,
    }[entry.verdict]

    return {
        "proposition_text": entry.counterparty_claim,
        "status": status.value,
        "inference_chain": [entry.demolition_note],
        "supporting_evidence_ids": list(entry.evidence_source_ids),
        "contradicting_evidence_ids": list(entry.evidence_source_ids)
            if status is EpistemicStatus.CONTRADICTED else [],
        "source_id": entry.communication_source_id,
        "severity": entry.severity.value,
        "burden_shift": entry.burden_shift.value,
    }

def trace_to_propositions(timeline: DemolitionTimeline) -> list[dict]:
    return [translate_timeline_entry(e) for e in timeline.entries]
```

**Note on the mapping:** the real engine does **not** expose an explicit `CONTRADICTED` flag. `CONTRADICTED` must be *derived* — e.g. when a claim matches evidence with a high contradiction-score, or the `demolition_note` contains a `DATE INCONSISTENCY`/`BURDEN SHIFT` marker. This is a **semantic decision you must make and document**; it cannot be read off the source.

### Option B — Bind to the real HTTP module

`src/modules/anchorum/interface/routes.py` is a FastAPI router mounted at `/modules/anchorum`. Real endpoints:

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/modules/anchorum/ingest` | `{workspace_id, content_ref, case_id?}` | `{evidence, output}` |
| POST | `/modules/anchorum/analyze` | `{evidence_id}` | `{evidence_id, report, output}` |
| GET | `/modules/anchorum/chronology/{case_id}` | — | `{case_id, timeline, output}` |

An `httpx` client for **these** paths is legitimate. An `httpx` client for `/v1/deterministic/evaluate` and `/v1/epistemic/consistency` is **not** — those routes do not exist and would 404.

---

## 5. What Should NOT Be Created

Do **not** create `src/legal_agent/adapters/anchorum_client.py` that:

- Imports `.base_adapter` → `AnchorumAdapterInterface`, `RawAnchorumOutput` (modules/types don't exist), and
- POSTs to `/v1/deterministic/evaluate` / `/v1/epistemic/consistency` (routes don't exist).

That file would be dead code that raises `ImportError` at import time and `404` at call time. It would be a fabrication, not an implementation.

---

## 6. Recommended Next Steps (Phase 2, corrected)

1. **Decide the target binding** — Option A (direct batch engine) is the only one that maps to Anchorum's real deterministic/epistemic-like semantics. Option B (HTTP module) handles evidence forensics, not rule/consistency evaluation.
2. **Define the status vocabulary explicitly.** Since `EpistemicTag` ≠ `EpistemicStatus`, you must own the mapping (`ASSERTED`/`CONTRADICTED`/`INFERRED`/`UNCERTAIN`) and document the derivation rule for `CONTRADICTED`.
3. **Define the agent layer you actually want.** Today the "agents" are JSON scaffolds and cell executors. There is no `BaseAgent`; introduce one at the correct location (e.g. alongside `src/egregore/` cells), and only then write an adapter against it.
4. **Fix the dead import** in `cells/anchorum_forensic/executor.py` (`anchorum.forensic.core.batch_runner.run_batch`) — either wire it to `anchorum.pipeline.narrative_trace.analysis.tracer.NarrativeTracer.trace()` or remove it.
5. **Place the adapter at the real integration point** — e.g. `src/egregore/cells/anchorum_forensic/` or a new `anchorum/client.py` that wraps `NarrativeTracer`, rather than a nonexistent `src/legal_agent/` package.

---

## 7. Summary

- **The blueprint does not bind.** The endpoints, agent layer, and types it references do not exist.
- **Real Anchorum** = `NarrativeTracer.trace()` demo/forensics engine (`EpistemicTag`) + HTTP `ingest/analyze/chronology` module (ASDS evidence storage).
- **Real "agents"** = JSON scaffolds + cell executors; `src/legal_agent/`, `BaseAgent`, `Proposition`, `EpistemicStatus`, `AnchorumAdapterInterface`, `RawAnchorumOutput` are absent.
- **Corrected contract** = wrap `NarrativeTracer.trace()` and translate `TimelineEntry`/`EpistemicTag` → your target `Proposition`/status vocabulary, with an explicit, documented derivation rule for `CONTRADICTED`.
- **Dead code to avoid** = the literal `anchorum_client.py` from the original audit.

---

## 8. Deterministic Engine — Specific Answer (follow-up)

The user asked for the precise location/signature/schema/enums of the **deterministic engine** that answers "Does this specific rule apply?", and explicitly forbade inventing an `evaluate_rule`. The verified answer:

### 8.1 The module that literally says "deterministic"

| Item | Value |
|---|---|
| **Absolute path** | `/home/kark/egregore/src/egregore/rfe/engine.py` |
| | Support: `/home/kark/egregore/src/egregore/rfe/models.py` (output Pydantic models) |
| | Support: `/home/kark/egregore/src/egregore/tooling/deterministic_verification.py` (canonical hashing / replay determinism) |
| **Function name** | `reproducible_fusion` |
| **Signature** | `def reproducible_fusion(manifest: dict[str, Any], config: dict[str, Any] \| None = None) -> dict[str, Any]` |
| **Module docstring** | "Pure deterministic fusion function." |

RFE = **Reproducible Fusion Engine**. It is NOT `evaluate_rule(rule_id, facts)` and NOT `check_compliance(jurisdiction, document)`.

### 8.2 Output schema

Returns a dict:

```python
{
    "report": Report,            # Pydantic model (model_dump()'d)
    "report_hash": str,          # SHA-256 over canonical report
    "decision_log_hash": str,    # SHA-256 over canonical decision log
    "version_id": str,
}
```

`Report` (from `src/egregore/rfe/models.py`) has fields:

```python
case_id: str
generated_at: str
engine_version: str
policy_version: str
reasoning_version_id: str
language: str
sections: list[ReportSection]   # summary / timeline / analysis / conclusion / perspectives / ...
decision_log: DecisionLog
report_hash: str
decision_log_hash: str
version_id: str
```

It does **NOT** return a `Boolean`, `RuleResult`, or `ComplianceReport`.

### 8.3 Rule-outcome Enums

**None exist.** There is no `RuleOutcome` enum with `PASS` / `FAIL` / `NOT_APPLICABLE`. The only verdict-like concept is a **string** `"PASS"` / `"FAIL"`, produced by cell executors, e.g. `cells/anchorum_forensic/executor.py`:

```python
"verdict": "PASS" if highest_severity in {"none", "low", "info"} else "FAIL"
```

The RFE itself does not emit `PASS`/`FAIL`. It emits scored streams and conclusion strings of the form `"{subject}: supported"` / `"{subject}: opposed"`.

### 8.4 CRITICAL: A rule/compliance deterministic engine is NOT implemented

**There is no `evaluate_rule`, no `check_compliance`, no `RuleOutcome` enum, no `RuleResult`, no `ComplianceReport` model anywhere verified in the codebase.** `reproducible_fusion` is deterministic **evidence fusion / scoring** (idempotent, byte-identical output), not deterministic **rule evaluation** in the jurisdiction / "does this rule apply to these facts" sense.

Therefore the CLI Agent's requirement — *"Deterministic: Does this specific rule apply?"* — has **no backing engine** today. Either:
- (a) Build a real rule-evaluation engine (jurisdiction + rule_id + facts → outcome), or
- (b) Re-scope the requirement to `reproducible_fusion` (which answers a different question: *"Given these claim streams, what is the fused score and supported/opposed conclusion?"*).

I did **not** invent an `evaluate_rule`. As the user instructed, it does not exist and will not be fabricated.

---

## 9. CORRECTION — the deterministic rule engine DOES exist (supersedes §8.4)

My §8.4 conclusion ("no rule/compliance deterministic engine is implemented") was **wrong** and is superseded. I subsequently located it. It is **not** `reproducible_fusion`; it is the **`LegalReasoningEngine`** — a 4-stage, deterministic, jurisdiction-aware rule pipeline.

| Item | Value |
|---|---|
| Module | `src/egregore/application/legal_reasoning_engine.py` |
| Class / entry | `LegalReasoningEngine.analyze(ir: CanonicalSemanticIR, case_id: str) -> LegalAnalysisOutput` |
| Stages | `_bind_facts` → `_map_rules` → `_build_inference_graph` → `_compose_output` |
| Rule source (Port) | `IRuleRegistry` Protocol (`src/egregore/interface/legal_agent_ports.py`) |
| Static impl | `StaticRuleRegistry` (`src/egregore/domain/legal_agent/rule_registry.py`) |
| Quebec impl | `QuebecCivilProcedureRuleRegistry` (`src/egregore/domain/legal_agent/quebec_rule_registry.py`) — YAML-loaded via `RuleRegistrySource` |
| Output | `LegalAnalysisOutput` (`src/egregore/domain/legal_agent/legal_models.py`) |
| Guarantees | Pure + deterministic (same IR + version → identical output); fail-closed (`LegalReasoningError`, never partial); `prohibited_conclusions` always `()` |

**This IS the "does this rule apply?" engine.** It is deterministic and jurisdiction-aware (Quebec). It does **not** return a PASS/FAIL Boolean — it returns a confidence-scored `LegalAnalysisOutput`:

```python
LegalAnalysisOutput(
    case_id, issues_identified: tuple[str],
    applicable_rules: tuple[RuleMatch],
    supporting_evidence_ids: tuple[str],
    inference_chain: tuple[InferenceNode],
    confidence_scores: dict[str, float],
    uncertainty_flags: tuple[str],
    reasoning_version, agent_version,
    prohibited_conclusions=(),  # structural invariant
)
RuleMatch(rule_id, rule_text, jurisdiction, matched_fact_ids: tuple[str], confidence: float)
```

Input `CanonicalSemanticIR` statements: `FactStatement` (w→1.0), `EvidenceInterpretationStatement` (w→0.7), `HypothesisStatement` (w→0.4), `ClassificationStatement` (excluded as routing metadata).

---

## 10. Full Anchorum HTTP API schema (verified)

### A. Main Anchorum API — `src/egregore/interface/anchorum_router.py` (`/api/v1/anchorum`)

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/api/v1/anchorum/batch` | `BatchRequest{input_path, case_id, operator="web_ui", fuse=False, llm_model_id?}` | `{job_id, case_id, status, created_at, output_path, message}` |
| POST | `/api/v1/anchorum/batch/sync` | `BatchRequest` | `_job_response` |
| POST | `/api/v1/anchorum/batch/fuse` | `BatchRequest` | `{status, anchorum_result, report, report_hash, version_id}` |
| GET | `/api/v1/anchorum/jobs` | — | `{jobs:[...]}` |
| GET | `/api/v1/anchorum/jobs/{job_id}` | — | `_job_response` |
| DELETE | `/api/v1/anchorum/jobs/{job_id}` | — | `{job_id, status, deleted, note?}` |
| GET | `/api/v1/anchorum/cases` | — | `list[str]` |
| POST | `/api/v1/anchorum/cases` (201) | `CaseCreateRequest{case_id, operator="desktop_app"}` | `{case_id, created, report_path}` |
| DELETE | `/api/v1/anchorum/cases/{case_id}` | — | `{case_id, deleted, files_removed, workdir_removed, index_removed, note}` |
| GET | `/api/v1/anchorum/cases/{case_id}` | — | full canonical report |
| GET | `/api/v1/anchorum/cases/{case_id}/summary` | — | `{case_id, report_id, generated_at, artifact_count, entity_count, anomaly_count, critical_count, high_count, medium_count, low_count}` |
| GET | `/api/v1/anchorum/cases/{case_id}/anomalies` | — | `{critical, high, medium, low, info}` |
| GET | `/api/v1/anchorum/cases/{case_id}/timeline` | — | `{timeline:[...]}` |
| POST | `/api/v1/anchorum/cases/{case_id}/rag/index` | `RagIndexRequest{extra_dirs?}` | indexing stats |
| POST | `/api/v1/anchorum/cases/{case_id}/rag/query` | `RagQueryRequest{query, top_k=4}` | `{case_id, chunks}` |
| GET | `/api/v1/anchorum/cases/{case_id}/sources` | — | `{sources}` |
| POST | `/api/v1/anchorum/cases/{case_id}/sources/attach` | `AttachSourcesRequest{extra_dirs}` | `{case_id, extra_dirs}` |
| GET | `/api/v1/anchorum/tools` | — | `{tools}` |
| POST | `/ingest` | `IngestionEvent{event_id?, source, purpose, source_legality, scope_status, entity_kind, entity_value, payload}` | `IngestReceipt{receipt_id, epistemic_state="accepted", accepted, event_id}` |
| GET | `/api/v1/anchorum/fs/partitions` | — | mountpoints |
| GET | `/api/v1/anchorum/fs/list?path=...` | — | `{name, path, type, size, children}` |
| POST | `/api/v1/anchorum/fs/probe` | `_FsFetchRequest` | `{file_count, total_bytes, errors}` |
| POST | `/api/v1/anchorum/fs/fetch` | `_FsFetchRequest{paths, case_id, consent}` | `{staged, staging_dir, file_count, ...}` |
| GET | `/api/v1/anchorum/imap/ledger` | — | `{ok, entries, broken_at, message}` |
| POST | `/api/v1/anchorum/imap/connect` | `_ImapConnectRequest{config}` | `{folders}` |
| POST | `/api/v1/anchorum/imap/probe` | `_ImapFetchRequest` | `{file_count, total_bytes, per_folder, ...}` |
| POST | `/api/v1/anchorum/imap/fetch` | `_ImapFetchRequest{config, folders, case_id, consent}` | `{staged, staging_dir, message_count, ...}` |

### B. Plain-HTTP site + chat — `src/egregore/interface/anchorum_http.py`

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/api/v1/anchorum/chat` | `ChatIn{message, mode="legal", case_id?}` | `{ok, content, usage, governance, sources, model, rag_telemetry}` |
| GET | `/api/v1/anchorum/models` | — | `{chat_model, ems_url, models, error?}` |
| GET | `/api/v1/anchorum/cases/{case_id}/index` | — | index stats |
| GET | `/api/v1/anchorum/cases/{case_id}/index/health` | — | `{...stats, store_size_bytes, query_latency_ms}` |
| POST | `/api/v1/anchorum/cases/{case_id}/reindex` | — | indexing stats |

### C. ASDS forensics module — `src/modules/anchorum/interface/routes.py` (`/modules/anchorum`)

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/modules/anchorum/ingest` | `IngestRequest{workspace_id, content_ref, case_id?}` | `{evidence, output}` |
| POST | `/modules/anchorum/analyze` | `AnalyzeRequest{evidence_id}` | `{evidence_id, report, output}` |
| GET | `/modules/anchorum/chronology/{case_id}` | — | `{case_id, timeline, output}` |

### D. RFE fusion — `src/egregore/rfe/engine.py` (`reproducible_fusion`)

Served under `/api/v1/rfe/*` per `src/egregore/rfe/models.py` (`GenerateResponse`, `ConfigResponse`, `HealthResponse`, `VersionsResponse`).

---

## 11. Persistence model (verified)

**Hybrid — filesystem JSON + signed provenance chain + ChromaDB + SQLite/Postgres; NOT a single relational DB.**

| Concern | Mechanism | Location | Durable? |
|---|---|---|---|
| ASDS evidence / outputs | Immutable canonical JSON | `data_dir/workspaces/{ws}/evidence\|outputs\|blobs/*.json` (default `/opt/egregore/asds_data`, env `ASDS_DATA_DIR`) | Yes |
| ASDS bus events | Append-only JSONL | `data_dir/bus_events.jsonl` | Yes |
| Anchorum case reports | Canonical JSON report + summary | `ANCHORUM_REPORT_DIR/{case_id}_report.json` (default `/opt/egregore/ANCHORUM_reports`); read-only fallback dirs | Yes |
| Anchorum work / artifacts | JSON report + `.artifacts/*.json` | `{case_id}_work/anchorum_output/` | Yes |
| Anchorum staged ingest events | Canonical JSON | `{report_dir}/{event_id}.ingest.json` | Yes |
| RFE provenance | **Append-only signed `.zarc` chain** | `config.zarc_path` (`egregore.kernel.provenance.Provenance`) | Yes |
| Case RAG | **ChromaDB** per-case vector store | `case_rag.case_index_dir(case_id)` | Yes (on-disk) |
| Anchorum **jobs** | **In-memory** `_JobStore` (thread-safe, non-durable) | process memory | No (cleared on restart) |
| Freeze state / session grants | **In-memory** (`StubFreezeController`, `_session_grants`) | process memory | No |
| Dossier / matter store | **SQLite** + **Postgres** adapters (separate) | `tests/test_sqlite_dossier_adapter.py`, `tests/test_postgresql_dossier_adapter.py` | Yes |

Summary: matters/evidence/propositions are stored as **immutable canonical JSON on disk** (ASDS, Anchorum reports, staged events) + a **signed append-only `.zarc` provenance chain** for audit traceability + **ChromaDB per-case vector indexes** for RAG. A separate **SQLite/Postgres dossier adapter** persists dossiers/matters. Jobs, session grants, and freeze state are intentionally **in-memory and non-durable**.

---

## 12. Legacy test map (relevant to migrate)

| Test file | Coverage |
|---|---|
| `test_legal_agent_pipeline.py` | LegalReasoningEngine 4-stage pipeline + determinism + fail-closed — **migrate first** |
| `test_legal_agent_biok_integration.py` | Legal agent ↔ BIOK boundary / `validate_legal_analysis_output` |
| `test_anchorum_router.py` | `/api/v1/anchorum/*` router |
| `test_anchorum_http.py` | anchorum HTTP site (chat/models/cases) |
| `test_anchorum_ingest_flow.py` | `/ingest` Stage-4 flow |
| `test_rfe_api.py` | RFE `/api/v1/rfe/generate` etc. |
| `test_rfe_replay.py` | RFE replay determinism (byte-identical) — **critical** |
| `test_rfe_vim.py` | RFE VIM |
| `test_engine.py` | RFE/engine |
| `test_case_rag.py` | per-case RAG index/query |
| `test_document_intake.py`, `test_email_ingest.py`, `test_imap_connector.py`, `test_file_fetch.py` | intake / IMAP / FS fetch |
| `test_sqlite_dossier_adapter.py`, `test_postgresql_dossier_adapter.py` | dossier persistence |
| `test_lang_determinism.py`, `test_lang_benchmark.py` | deterministic serialization |
| `tests/test_asds/`, `tests/test_modules/` | ASDS domain + modules |

Run via the repo venv, e.g. `.venv/bin/python -m pytest tests/test_legal_agent_pipeline.py tests/test_rfe_replay.py -x`.
