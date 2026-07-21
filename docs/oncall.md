# Egregore On-Call Guide

## Scope

This guide defines rotation, alert routing, and handoff procedures for the
Egregore platform. It pairs with `docs/runbook.md` for incident response.

---

## Rotation Schedule

| Week / Period | Primary | Secondary |
|---------------|---------|-----------|
| `[ASK USER: period]` | `[ASK USER: name]` | `[ASK USER: name]` |
| `[ASK USER: period]` | `[ASK USER: name]` | `[ASK USER: name]` |
| `[ASK USER: period]` | `[ASK USER: name]` | `[ASK USER: name]` |

- Rotation tool: `[ASK USER: tool]`
- Calendar integration: `[ASK USER: calendar]`
- Override policy: `[ASK USER: override policy]`

---

## Primary / Secondary / Escalation

| Tier | Responsibility | Contact | SLA |
|------|---------------|---------|-----|
| Primary on-call | First response, triage, SEV1-4 acknowledgment | `[ASK USER: contact]` | `[ASK USER: minutes]` |
| Secondary on-call | Backup if primary unreachable; complex investigations | `[ASK USER: contact]` | `[ASK USER: minutes]` |
| SRE escalation | Infrastructure, deployment, rollback execution | `[ASK USER: contact]` | `[ASK USER: minutes]` |
| Governance escalation | CBI-0, Anchorum, M4 divergence, legal data concerns | `[ASK USER: contact]` | `[ASK USER: minutes]` |

If the primary does not acknowledge a SEV1/SEV2 page within the SLA, the page
automatically routes to the secondary. If the secondary does not acknowledge,
the page escalates to the SRE lead, then the governance owner.

---

## Alert Routing

### Prometheus → Alertmanager → Pager

`config/prometheus.yml` scrapes the API at `egregore:9000` every 15 seconds:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "egregore-api"
    static_configs:
      - targets: ["egregore:9000"]
```

Recommended alert rules to add (not yet present in `prometheus.yml`):

| Alert | Expression | Severity | Route to |
|-------|-----------|----------|----------|
| `EgregoreReadyDown` | `up{job="egregore-api"} == 0` or probe of `/ready` != 200 | SEV1 | Primary + SRE |
| `EgregoreCBI0BlockedRate` | rate of `CBI0BlockedError` logs > threshold | SEV1 | Primary + Governance |
| `EgregoreZarcSignatureFailure` | `anchorum_integrity_gate.py` reports sig failure | SEV2 | Primary + Governance |
| `EgregoreNatsDown` | `/ready` `nats` check error or `nats_broker.py` exception | SEV3 | Primary |
| `EgregorePulseStale` | no `obs.pulse.<node_id>` received for > 60s | SEV3 | Primary |
| `EgregoreLatencyP95High` | `/v1/dossiers/generate` p95 > budget | SEV4 | Primary (business hours) |

### Telemetry emitters

Two agents publish pulse telemetry on subject `obs.pulse.<node_id>`:

- `src/egregore/cortex/pulse_adapter.py` — `PulseAdapter.emit()` publishes
  CPU, GPU temperature, GPU power, and VRAM samples as canonical JSON.
- `src/egregore/infrastructure/telemetry/phase0_pulse_agent.py` —
  `Phase0TelemetryPulseAgent` emits gate envelopes for `cpu`, `memory`,
  `storage`, `network`, `gpu`, and `interconnect`.

On-call should verify that both publishers are visible to subscribers or to the
NATS monitoring endpoint.

### Expected subjects

- `obs.pulse.<node_id>` — node telemetry
- `dt1.*.*.*.*` — DT1 live stream
- `ctrl.>` — control plane

---

## Handoff Checklist

Outgoing on-call must confirm the following before signing off:

### 1. Architecture purity gate

Run and review results:

```bash
cd ~/egregore && source .venv/bin/activate
python -m pytest tests/test_arch_enforcement.py -q
```

If any layer dependency or canonicalization test fails, file an incident and do
not complete handoff until triaged.

### 2. CBI-0 gate

```bash
python -m pytest tests/test_cbi_0_enforcement.py -q
```

M1–M4 must all pass. Any `DIVERGED` M4 record requires governance escalation.

### 3. Gate 5 invariants

```bash
python -m pytest tests/test_gate5_invariants.py -q
```

Confirms bounded replay invariance under representational drift.

### 4. Anchorum integrity gate

```bash
PYTHONPATH=src python3 src/egregore/governance/anchorum_integrity_gate.py
```

All checks should report `PASS`.

### 5. ZARC journal integrity

For each active journal file (check `BLACKSTAR_SIGNING_KEY_PATH` and configured
zarc paths):

```bash
PYTHONPATH=src python3 - <<'PY'
from pathlib import Path
from egregore.kernel.provenance import Provenance
# Adjust path and key as appropriate for the node
p = Provenance(
    Path("/opt/egregore/data/journal.zarc"),
    signing_key_hex=open("secrets/signing_key.pem").read().strip()[:64],
)
print("chain_ok:", p.verify_chain())
PY
```

If `verify_chain()` returns `False`, treat as SEV2 and follow
`docs/runbook.md`.

### 6. Open incidents / alerts

- [ ] No active SEV1/SEV2 incidents unresolved
- [ ] No unacknowledged pages
- [ ] Incident notes linked in runbook/post-mortem tracker

### 7. Deployment / flag state

- [ ] Last deploy SHA recorded
- [ ] Any active `feature_flag_registry.py` flags documented in shift notes
- [ ] No pending rollback windows

---

## Runbooks

- SEV1/SEV2 response: `docs/runbook.md`
- Deployment & rollback: `docs/pipeline.md`
- Feedback & retros: `docs/feedback.md`

---

## Reference Files

- `config/prometheus.yml`
- `src/egregore/infrastructure/telemetry/phase0_pulse_agent.py`
- `src/egregore/cortex/pulse_adapter.py`
- `src/egregore/governance/anchorum_integrity_gate.py`
- `src/egregore/infrastructure/zarc_journal.py`
- `tests/test_gate5_invariants.py`
- `tests/test_arch_enforcement.py`
- `tests/test_cbi_0_enforcement.py`
