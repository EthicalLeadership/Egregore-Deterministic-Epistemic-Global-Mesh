# Egregore Incident Response Runbook

## Scope

This runbook covers production incidents for the Egregore Python+FastAPI core,
npm-managed gateway, and supporting services (Postgres, Redis, NATS). It is
intended for the primary on-call engineer and the escalation chain defined in
`docs/oncall.md`.

All rollback decisions must first consult `feature_flag_registry.py` kill
switches before reverting code or data.

---

## Severity Levels

| Level | Name | Egregore-specific symptom | Business impact | Response window |
|-------|------|---------------------------|-----------------|-----------------|
| **SEV1** | Critical | `CBI0Governance` raises `CBI0BlockedError`; M1/M2/M3/M4 fail-closed halt execution; no dossiers commit. | Core function unavailable; legal workflow stop. | Immediate |
| **SEV2** | Major | `.zarc` chain signature verification fails in `ZarcJournal` / `anchorum_integrity_gate.py`; provenance fork detected. | Data integrity questioned; replay non-determinism risk. | < 15 min |
| **SEV3** | Significant | NATS broker unreachable (`nats_broker.py` bootstrap fails) or `obs.pulse.<node_id>` stops emitting. | Async transport down; telemetry blind spot. | < 1 h |
| **SEV4** | Minor | Performance degradation on `/ready`, `/v1/dossiers/generate`, or chat completions; p95 latency spike. | Degraded UX; no data loss. | Next business day |

---

## Escalation Matrix

| Role | Primary | Secondary | SLA | Contact |
|------|---------|-----------|-----|---------|
| On-call Engineer | `[ASK USER: name]` | `[ASK USER: name]` | `[ASK USER: minutes]` | `[ASK USER: contact]` |
| SRE / Platform Lead | `[ASK USER: name]` | `[ASK USER: name]` | `[ASK USER: minutes]` | `[ASK USER: contact]` |
| Security / Governance Owner | `[ASK USER: name]` | `[ASK USER: name]` | `[ASK USER: minutes]` | `[ASK USER: contact]` |
| Product / Legal Stakeholder | `[ASK USER: name]` | `[ASK USER: name]` | `[ASK USER: minutes]` | `[ASK USER: contact]` |

Escalation path: On-call → SRE → Security/Governance → Product/Legal.

---

## Per-Component Runbook

### `anchorum_integrity_gate.py`

**Owner:** Security / Governance  
**Failure signature:** `AnchorumIntegrityFailure` from `run_anchorum_check()`; one or more checks in `report["checks"]` = `FAIL`.

**Checks and actions:**

| Check | Likely cause | Action |
|-------|--------------|--------|
| `files` | Critical source file missing or empty (`CRITICAL_PATHS`) | Verify repo checkout; restore from git or backup. Do not run with missing files. |
| `imports` | Python module import failure | Run `PYTHONPATH=src python3 -c "import egregore.domain.models.dossier"` to isolate. |
| `postgresql` | Postgres adapter smoke test fails | Verify `EGREGORE_DB_URL`; run `npm run services:check`. |
| `kek` | Cluster KEK not readable | Check `secrets/cluster_kek.bin` permissions and `EGREGORE_CLUSTER_KEK_PATH`. |
| `gguf` | Local model catalog unhealthy | Verify `EGREGORE_LOCAL_MODEL_MANIFEST` and model files. |
| `ufw` / `fail2ban` | Host hardening disabled | Re-enable via host playbook; treat as SEV2 if exposed. |
| `backup` | Backup missing or stale > 48h | Trigger manual backup; investigate scheduler. |

**Freeze controller:** If `freeze_controller` is supplied, a failure calls
`fork_detected()` with `detection_source="anchorum_integrity_gate"`. Confirm
`zarc_journal.py` chain state before clearing the freeze.

---

### `nats_broker.py`

**Owner:** SRE / Platform  
**Failure signature:** JetStream streams fail to bootstrap; `bootstrap_jetstream()` raises; `/ready` reports `nats: error: ...`; `obs.pulse.<node_id>` not published.

**Triage:**

1. Verify NATS process: `systemctl status nats` or `docker compose ps nats`.
2. Verify `NATS_URL` env var matches running server.
3. Test connectivity: `nats --server $NATS_URL pub test.hello world`.
4. Review `bootstrap_jetstream()` configs in callers for stream name / subject collisions.

**Recovery:**

- Idempotent re-run: `bootstrap_jetstream()` ignores `badstreamerror` / "already exists" / "duplicate" markers by default.
- For non-idempotent errors, capture exception text and escalate.
- If streams are corrupt, consider deleting and re-creating after SEV2 approval.

---

### `dossier_generate_service.py`

**Owner:** Application Engineering  
**Failure signature:** `generate()` returns 500 or raises; `DossierGenerateService` cannot produce a `CommandAck`; replay invariants fail in `test_gate5_invariants.py`.

**Triage:**

1. Confirm `CorePlaneGenerateDossierExecutor` dependencies: `authz`, `case_store`, `idempotency_store`, `transactional_persistence`.
2. Check `timestamp_ns` resolution path:
   - Caller-supplied?
   - `request.timestamp_ns`?
   - Deterministically derived via `derive_timestamp_ns_deterministically()`?
3. Verify deterministic engine policy is used in tests; real inference path requires `EGREGORE_LOCAL_MODEL_MANIFEST`.

**Recovery:**

- If non-deterministic vertical inference diverges, fall back to deterministic policy or disable the vertical via `feature_flag_registry.py`.
- Replay the `.zarc` chain with `ZarcJournal` to confirm no fork.

---

### `app.py`

**Owner:** SRE / Platform  
**Failure signature:** FastAPI routes 500; `/health` or `/ready` fail; gateway returns 502/503.

**Triage:**

1. Liveness: `curl http://localhost:8002/health`.
2. Readiness: `curl http://localhost:8002/ready` — checks DB, Redis, NATS.
3. Review router registration in `create_app()`: `dossiers`, `workflows`, `ws_chat`, `auth`, `intake`, `chat`.
4. Check `app_patch.py` has been applied if `chat_router` is expected.

**Recovery:**

- If a specific router is failing, disable it through `feature_flag_registry.py` kill switches and restart uvicorn.
- Static file serving failures do not block API traffic; investigate `static/` path.

---

## Post-Mortem Template

```markdown
# Post-Mortem: <incident title>

## Metadata
- Incident ID: <auto-generated>
- Severity: SEV1 / SEV2 / SEV3 / SEV4
- Start: <ISO timestamp>
- End: <ISO timestamp>
- Detection source: <alert / manual / test>

## Summary
<2-3 sentence description of what happened and user impact>

## Timeline
| Time | Event |
|------|-------|
|      |       |

## Root Cause
<technical and process root cause>

## M4 Equivalent/Diverged Field
- Spec state: <canonical representation of expected state>
- Runtime state: <actual observed state>
- Result: **EQUIVALENT** / **DIVERGED**
- Evidence: <hash diff, replay output, or CBI-0 audit log>
- If **DIVERGED**: include `cbi0_governance.py` `M4SpecRuntimeEquivalence` audit record
  and `zarc_journal.py` chain verification result.

## Impact
- Components affected: <list>
- Data integrity: <confirmed / under review / compromised>
- User/legal impact: <none / partial / severe>

## Remediation
- Immediate fix:
- Long-term fix:

## Rollback / Kill Switches
- `feature_flag_registry.py` flags toggled: <list>
- Git revert / deploy revert: <SHA or PR>

## Action Items
| Owner | Task | Due |
|-------|------|-----|
|       |      |     |
```

---

## Rollback Triggers

Before any code or infrastructure rollback, evaluate these triggers against
`feature_flag_registry.py`:

| Trigger | Flag action | When to use |
|---------|-------------|-------------|
| New chat completions endpoint unstable | Disable chat feature flag | `/v1/chat/completions` returns 5xx or non-deterministic outputs |
| Vertical inference diverges | Disable vertical feature flag | `DossierGenerateService` produces different results for same input |
| RBAC authz provider fails open | Disable RBAC flag | `http_v1_facades.py` allows unauthorized dossier generation |
| Experimental intake pipeline broken | Disable intake feature flag | `/v1/intake/upload` corrupts documents |

If a flag does not exist for the failing capability, create one in
`featureFlagRegistry.register(FeatureFlag(name="...", enabled=False))` and
restart the service. Always document the flag change in the incident channel
and the post-mortem.

---

## Reference Files

- `src/egregore/governance/anchorum_integrity_gate.py`
- `src/egregore/governance/cbi0_governance.py`
- `src/egregore/application/feature_flag_registry.py`
- `src/egregore/infrastructure/zarc_journal.py`
- `src/egregore/http_api/http/app.py`
- `tests/test_gate5_invariants.py`
- `tests/test_cbi_0_enforcement.py`

---

## ANCHORUM Forensic Engine Runbook

For ANCHORUM-specific workflows, incident checks, and health commands, see
[`docs/anchorum/runbook.md`](./anchorum/runbook.md) and the
[`docs/anchorum/api_reference.md`](./anchorum/api_reference.md).
