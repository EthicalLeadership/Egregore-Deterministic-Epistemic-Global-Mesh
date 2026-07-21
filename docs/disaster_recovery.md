# Egregore Disaster Recovery Plan

## Scope

This document defines recovery objectives, backup procedures, failover, and
drill cadence for the Egregore core (deterministic dossier generation) and
projection planes. It supports control **C7.1 — Disaster Recovery
(RPO / RTO / Backup / Failover)**.

Egregore's durability model is event-sourced: every dossier commit is
serialized to a signed `.zarc` entry. This means the smallest unit of recovery
is a single commit, and the system can be rebuilt by replaying the chain.

---

## RPO / RTO Targets

| Plane | RPO Target | RTO Target | Justification |
|-------|------------|------------|---------------|
| Core Plane (dossier commits) | `[ASK USER: e.g., zero data loss — every commit is a checkpoint]` | `[ASK USER: e.g., 15 minutes]` | Each `commit_generate_t2()` is durable in `.zarc` before ACK |
| Projection Plane (read models) | `[ASK USER: e.g., bounded by last replay — can be reconstructed from Core Plane]` | `[ASK USER: e.g., 1 hour]` | Read models are rebuilt deterministically from `.zarc` |
| Intake / ingestion queue | `[ASK USER: e.g., last NATS JetStream persisted message]` | `[ASK USER: e.g., 30 minutes]` | JetStream streams are configured with file retention |
| Configuration / secrets | `[ASK USER: e.g., zero data loss — versioned separately]` | `[ASK USER: e.g., 15 minutes]` | Signing keys and KEK must be available before replay |

Measurement: RTO is from incident declaration to `/ready` returning `200` on
the failover target.

---

## Backup Schedule

### `.zarc` journal and provenance chain

| Source | Frequency | Retention | Encryption | Verification |
|--------|-----------|-----------|------------|--------------|
| `ZarcJournal` files (`*.zarc`) | Continuous append + hourly sync to backup store | Per `docs/data_governance.md` retention schedule | AES-256-GCM with cluster KEK | `Provenance.verify_chain()` on restore |
| `src/egregore/kernel/provenance.py` signing keys | Manual rotation + encrypted vault copy | Indefinite (key history required for old chains) | HSM/vault | Quarterly decryption test |
| Postgres relational snapshots | Daily at 02:00 UTC | 30 days | Encrypted at rest | Weekly restore test |
| NATS JetStream streams | Continuous replication | 24 hours hot, 30 days cold | TLS in transit, encrypted disk | Stream health check |

Backup destinations:
- **Pioneer 1:** local encrypted volume + off-site rsync
- **Client Red Dart:** Docker volume snapshots + cloud object storage

Implementation references:
- `src/egregore/infrastructure/zarc_journal.py`
- `src/egregore/kernel/provenance.py`
- `src/egregore/hardening/backup.py` (if present)

---

## Region Failover: Pioneer 1 → Client Red Dart

| Step | Action | Owner | Time budget |
|------|--------|-------|-------------|
| 1. Declare incident | On-call confirms Core Plane unavailable per `docs/runbook.md` | On-call engineer | `[ASK USER: minutes]` |
| 2. Promote backup | Make latest `.zarc` chain available to Red Dart | SRE | `[ASK USER: minutes]` |
| 3. Restore secrets | Inject signing key + KEK into Red Dart environment | Security/SRE | `[ASK USER: minutes]` |
| 4. Replay chain | `ZarcJournal` loads state from `.zarc` | System | Bounded by chain length |
| 5. Validate determinism | Run `tests/test_gate5_invariants.py` and `tests/test_cbi_0_enforcement.py` | CI/governance | `[ASK USER: minutes]` |
| 6. Redirect traffic | Update DNS/gateway to Red Dart endpoints | SRE | `[ASK USER: minutes]` |
| 7. Smoke tests | `/ready` + `obs.pulse.<node_id>` | On-call | `[ASK USER: minutes]` |

Failback to Pioneer 1 follows the same steps in reverse once the primary site
is healthy. The `.zarc` chain is the single source of truth, so divergent
writes on either site must be reconciled using `M4SpecRuntimeEquivalence`
before failback.

---

## Restore Verification Procedure

Every restore — whether for drill or real incident — must execute:

1. **Chain integrity:**
   ```bash
   PYTHONPATH=src python3 - <<'PY'
   from pathlib import Path
   from egregore.kernel.provenance import Provenance
   p = Provenance(Path("/restore/path/journal.zarc"), signing_key_hex=open("secrets/signing_key.pem").read().strip()[:64])
   assert p.verify_chain(), "ZARC chain verification failed"
   print("chain_ok: True")
   PY
   ```

2. **Deterministic replay:**
   ```bash
   python -m pytest tests/test_gate5_invariants.py tests/test_cbi_0_enforcement.py -q
   ```

3. **Read-model parity:** compare case state from replay against the last
   known good relational snapshot.

4. **Smoke:** `curl -fsS http://<target>:8002/ready`

A restore is not considered complete until all four steps pass.

---

## DR Drill Cadence

| Drill type | Frequency | Scope | Success criteria |
|------------|-----------|-------|------------------|
| Tabletop failover | Quarterly | Pioneer 1 → Red Dart runbook walkthrough | All steps documented and roles confirmed |
| Partial restore | Monthly | Restore `.zarc` chain to staging environment | `verify_chain()` passes, replay tests pass |
| Full failover | Bi-annually | Live traffic cutover to Red Dart and back | RTO/RPO targets met, no data loss |
| Secrets recovery | Annually | KEK + signing key decryption and use | Core Plane can replay chain |

Drill results are stored in `docs/audits/dr_drills/`.

---

## Incident Escalation

DR incidents are automatically **SEV1** per `docs/runbook.md`. The on-call
engineer escalates to the SRE lead if the initial restore attempt does not
produce a healthy `/ready` within the RTO target.

---

## Reference Files

- `src/egregore/infrastructure/zarc_journal.py`
- `src/egregore/kernel/provenance.py`
- `docs/data_governance.md`
- `docs/runbook.md`
- `docs/oncall.md`
- `tests/test_gate5_invariants.py`
- `tests/test_cbi_0_enforcement.py`
