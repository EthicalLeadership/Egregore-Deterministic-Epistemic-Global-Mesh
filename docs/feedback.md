# Egregore Feedback Loops

## Scope

This document defines how user feedback, incidents, and architecture reviews
flow back into the Egregore codebase. It supports control **C4+.3 — Feedback
Loops**.

---

## User Feedback Channel

| Channel | Owner | SLA | Escalation |
|---------|-------|-----|------------|
| `[ASK USER: channel]` | `[ASK USER: owner]` | `[ASK USER: SLA]` | `[ASK USER: escalation]` |

Feedback categories:

- Bug / unexpected output from `/v1/dossiers/generate` or `/v1/chat/completions`
- Incorrect or non-deterministic model output
- UI / gateway issue
- Legal / compliance concern
- Feature request

All feedback tickets must include:

- Request ID / causality ID (from `CommandAck` or response header)
- Organization and case ID if available
- Timestamp and node ID
- Severity / impact

---

## Retro Cadence

| Retro type | Cadence | Attendees | Output |
|------------|---------|-----------|--------|
| Incident retro | Within 48 h of SEV1/SEV2 | Primary, Secondary, SRE, Governance | Post-mortem + action items |
| Sprint retro | `[ASK USER: cadence]` | Engineering, Product | Retro board notes |
| Architecture retro | `[ASK USER: cadence]` | Architecture board, SRE, Security | ADR or fitness-function update |
| Quarterly review | `[ASK USER: cadence]` | All stakeholders | Roadmap + OKR adjustments |

Retro notes are stored in `[ASK USER: location]`.

---

## Incident-to-Feature Pipeline

Every post-mortem must produce one of the following outcomes within one week:

```
Post-mortem
    │
    ├─→ ADR (architecture change)
    │       └─→ Approved / Rejected / Deferred
    │               └─→ Implement → test_arch_enforcement.py gate
    │
    ├─→ Code fix
    │       └─→ PR → tests/test_cbi_0_enforcement.py → tests/test_gate5_invariants.py
    │
    └─→ Process change
            └─→ Update docs/runbook.md or docs/oncall.md
```

### ADR path

1. Create `docs/adr/YYYY-MM-DD-<title>.md` using the template:
   - Status
   - Context
   - Decision
   - Consequences
   - CBI-0 impact
   - M4 equivalence check
2. Run architecture gate: `pytest tests/test_arch_enforcement.py`.
3. Board review and sign-off.

### Code fix path

1. Branch from latest green `main`.
2. Add or update unit tests covering the regression.
3. Mandatory gates before merge:
   - `tests/test_arch_enforcement.py` — layer dependency purity
   - `tests/test_cbi_0_enforcement.py` — CBI-0 M1-M4
   - `tests/test_gate5_invariants.py` — replay invariants
4. Merge only after all gates pass and a secondary reviewer approves.

### Process change path

1. Update the relevant runbook or on-call doc.
2. Announce change in the on-call channel.
3. Add a checklist item to `docs/oncall.md` handoff if applicable.

---

## Architecture Drift Detection

Three automated fitness functions detect drift from the intended architecture:

### 1. `tests/test_arch_enforcement.py`

Enforces:

- Domain layer does not import `egregore.infrastructure` or `egregore.application`.
- Application layer does not import `egregore.infrastructure` (with documented exceptions).
- Domain purity: no `pathlib`, `os`, `sys`, `subprocess`, `socket`, network, or `open()` usage.
- Canonical JSON / SHA-256 single source of truth in `egregore/shared/canonical.py`.
- Closed import surfaces for `LegalReasoningEngine`, `ExecutionAuthority`, and the layer dependency matrix.

**Run:**

```bash
python -m pytest tests/test_arch_enforcement.py -q
```

### 2. `tests/test_gate5_invariants.py`

Enforces:

- Reasoning guard rejects forbidden evidence-to-conclusion phrasing.
- Replay bounded invariance holds under representational drift (e.g., `list` vs `tuple`).
- π_O equivalence is preserved across committed and derived snapshots.

**Run:**

```bash
python -m pytest tests/test_gate5_invariants.py -q
```

### 3. `tests/test_cbi_0_enforcement.py`

Enforces:

- **M1** — projection access monitor allows only declared scopes.
- **M2** — registry validator requires active descriptors and overlap classifications.
- **M3** — composition guard rejects re-entry of terminal artifacts and implicit IR synthesis.
- **M4** — binding audit emitter records `EQUIVALENT` or `DIVERGED` and emits a traceable record.

**Run:**

```bash
python -m pytest tests/test_cbi_0_enforcement.py -q
```

### Drift response workflow

```
Fitness function fails
    │
    ├─→ Triage: code change vs. intentional architecture evolution
    │
    ├─→ If code change: fix and re-run gate
    │
    └─→ If intentional evolution:
            ├─→ Write ADR
            ├─→ Update test expectations if approved
            └─→ Board sign-off
```

No test in this group may be disabled without a recorded ADR and board approval.

---

## Metrics

Track these metrics monthly:

| Metric | Source | Target |
|--------|--------|--------|
| Feedback-to-ticket conversion | `[ASK USER: source]` | `[ASK USER: target]` |
| Incident retro completion rate | Post-mortem tracker | 100% for SEV1/SEV2 |
| ADRs from incidents | `docs/adr/` | ≥ 1 per quarter |
| Fitness function pass rate | CI | 100% on main |
| Mean time from incident to code fix | Issue tracker | `[ASK USER: target]` |

---

## Reference Files

- `tests/test_arch_enforcement.py`
- `tests/test_gate5_invariants.py`
- `tests/test_cbi_0_enforcement.py`
- `src/egregore/governance/cbi0_governance.py`
- `src/egregore/application/feature_flag_registry.py`
- `docs/runbook.md`
- `docs/oncall.md`
