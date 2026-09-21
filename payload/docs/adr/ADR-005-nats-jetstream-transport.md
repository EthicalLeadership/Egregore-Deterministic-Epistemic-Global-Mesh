# ADR-005: NATS JetStream as Primary Async Transport

## Status

Accepted

## Context

Egregore needs durable, ordered messaging for control plane commands,
telemetry pulses, and DT1 streams. The transport must be lightweight enough to
run on Pioneer 1 and containerized deployments.

## Decision

Use NATS with JetStream as the primary async transport. Streams are
idempotently bootstrapped at startup. Pulse telemetry is published to subjects
`obs.pulse.<node_id>`.

Implementation: `src/egregore/bus/nats_broker.py`,
`src/egregore/cortex/pulse_adapter.py`, and
`src/egregore/infrastructure/telemetry/phase0_pulse_agent.py`.

## Consequences

- **Positive:** Durable streams, at-least-once delivery, observability subjects.
- **Positive:** Works in both systemd and Docker deployments.
- **Negative:** Requires NATS server and JetStream enabled.

## Stakeholder Sign-off

| Role | Name | Date | Status |
|------|------|------|--------|
| Architecture Lead | Egregor | 2026-06-18 | Approved |
| Security Lead | Egregor | 2026-06-18 | Approved |
| SRE Lead | Egregor | 2026-06-18 | Approved |