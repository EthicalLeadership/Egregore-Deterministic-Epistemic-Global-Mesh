# Site and Workspace Boundary Inventory

Status: Step 1 inventory required before the next case/evidence/audit extraction.

This inventory records code evidence found in the repository. `EVIDENCE-BACKED`
means the path was inspected and the ownership claim is visible in code.
`UNKNOWN` means the repository does not yet establish a single owner or a
complete read/write contract.

## Cases

| Concern | Defined | Written | Read | Finding |
| --- | --- | --- | --- | --- |
| Case identity and report shape | `src/anchorum/forensic/core/types.py` (`InvestigationReport.case_id`); `src/egregore/interface/anchorum_router.py` (`CaseSummary`) | `src/egregore/interface/anchorum_router.py` writes `{case_id}_report.json` after batch execution | `src/egregore/interface/anchorum_router.py` loads report and summary files; `anchorum_desktop.py` calls the case endpoints | EVIDENCE-BACKED definitions and report path; UNKNOWN canonical case aggregate |
| Case CRUD/API boundary | `src/egregore/interface/anchorum_router.py` | Router mutation endpoints write report files | Router and desktop read endpoints | EVIDENCE-BACKED API path; UNKNOWN whether all writers route through it |
| Workspace ownership of cases | No dedicated case entity found in `src/asds/domain` | No canonical workspace case writer found | `src/asds/application/anchorum_adapter.py` carries `workspace_id` and optional `case_id` on evidence | UNKNOWN; case ownership is not yet single-sourced |

## Evidence Ingest

| Concern | Defined | Written | Read | Finding |
| --- | --- | --- | --- | --- |
| Forensic artifact model | `src/anchorum/forensic/core/types.py` (`Artifact`, metadata planes) | `src/anchorum/forensic/core/cli/batch_extract.py` emits extracted JSONL/report artifacts | Forensic pipeline and report readers consume the records | EVIDENCE-BACKED forensic model and batch path |
| Workspace evidence model | `src/asds/domain/models.py` (`Evidence`) | `src/asds/application/anchorum_adapter.py` writes blobs and immutable JSON records under `ASDS_DATA_DIR/workspaces/<workspace>/evidence` | Same adapter scans evidence records and resolves `content_ref` | EVIDENCE-BACKED adapter path; UNKNOWN canonical relationship to forensic `Artifact` |
| Analysis outputs | `src/asds/domain/models.py` (`OutputVersion`) | `src/asds/application/anchorum_adapter.py` writes versioned output JSON and metadata | Adapter returns records; downstream read API not established | EVIDENCE-BACKED write path; UNKNOWN site read model |

## Audit

| Concern | Defined | Written | Read | Finding |
| --- | --- | --- | --- | --- |
| Adapter attribution audit | `src/egregore/interface/anchorum_router.py` (`_audit_record`) | `_audit_record` is the concrete writer: it appends `audit/egregore_adapter.jsonl`; its call sites include the ANCHORUM jobs, case, source, ingest, and tool endpoints in the same router | Dashboard/API consumers are not shown in the inspected adapter path | EVIDENCE-BACKED writer and call-site family; UNKNOWN canonical audit reader |
| Tamper-evident forensic events | `src/anchorum/forensic/core/provenance.py` (`emit_zarc_event`); `src/egregore/kernel/provenance.py` (`ZarcChain.append`); `docs/schemas/approval_artifact.md` distinguishes mutable SQLite cache from `.zarc` trail | `src/anchorum/forensic/core/ingestion.py:351` calls `emit_zarc_event`; `src/anchorum/forensic/core/provenance.py:87` writes per-event JSON; `src/egregore/rfe/provenance_store.py` appends RFE report events through the Egregore chain | `src/anchorum/forensic/core/provenance.py` reads event paths; `src/egregore/kernel/provenance.py` iterates chain entries; bridge/comparator modules consume `.zarc` lines | EVIDENCE-BACKED multiple writers; UNKNOWN unified origin and cross-chain ordering |
| In-memory audit middleware | `src/egregore/interface/bootstrap.py` (`AuditLogMiddleware`) | Middleware records request audit entries in its process-local log | `AuditLogMiddleware.get_audit_log` reads that process-local list | EVIDENCE-BACKED separate writer/read path; UNKNOWN persistence and relationship to `.zarc` |
| ASDS bus journal | `src/asds/application/anchorum_adapter.py` (`emit_bus_event`) | Appends `bus_events.jsonl` | No canonical consumer identified | EVIDENCE-BACKED writer; UNKNOWN event contract and retention |

## Auth and Permissions

| Concern | Defined | Written | Read | Finding |
| --- | --- | --- | --- | --- |
| Application authorization | `src/egregore/governance/permissions.py` (`PermissionService`) | No permission persistence in this service | Callers evaluate `UserIdentity` and roles/grants | EVIDENCE-BACKED decision service; UNKNOWN single enforcement point for ANCHORUM writes |
| API key authentication | `src/egregore/http_api/http/middleware/api_key_middleware.py` and ANCHORUM router tests using `X-API-Key` | Key material is external/configured; no case-scope binding shown | Middleware/router boundary | EVIDENCE-BACKED authentication mechanism; UNKNOWN read-scope attribution |
| Case/workspace authorization | No explicit case-to-identity policy found in inspected paths | No centralized write command gate identified | Router accepts operator/case fields | UNKNOWN and blocking for the next write-path extraction |

## UI State

| Concern | Defined | Written | Read | Finding |
| --- | --- | --- | --- | --- |
| Desktop case/chat selection | `anchorum_desktop.py` (`_active_case`, `_chat_case`, widget state) | Tk event handlers mutate in-memory state; API calls mutate server data | Same desktop callbacks and widgets | EVIDENCE-BACKED shell state; intentionally not domain state |
| Timeline projection | `timeline_kernel.py` defines normalized events; `entity_timeline.py` renders them with Tk | No domain writes | Desktop timeline reads API event payloads | EVIDENCE-BACKED projection split; adapter wiring verified by call site, Tk behavior remains UNVERIFIED |
| Site/web projection | `src/egregore/interface/anchorum_router.py` provides JSON responses; `src/egregore/interface/legal_chat_router.py` builds chat context | No site-owned shared-kernel write contract established | HTTP consumers | EVIDENCE-BACKED read paths; UNKNOWN whether any site code writes outside router-owned report/staging paths |

## Gate Result

authorization, and one audit event contract.
The inventory is sufficient to define the next design decision, but not to
start case/evidence/audit extraction. The unresolved blockers are the canonical
case aggregate, the relationship between `Artifact` and `Evidence`,
case-scoped authorization, and reconciliation of the four observed audit
families: adapter JSONL, forensic `.zarc`, RFE `.zarc`, and process-local
middleware/ASDS journals.
