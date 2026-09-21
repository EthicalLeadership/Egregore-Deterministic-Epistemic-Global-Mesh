# 13 — Global Event Schema

Status: `draft`

## Purpose

This document defines the **canonical event record** that every Egregore service or runtime component must emit when it produces a domain event, audit record, telemetry sample, or inter-node message. It unifies the existing `.zarc` provenance record, `AuditEvent`, `OutboxEntry`, telemetry envelope, and transport messages under one header contract.

The schema is intentionally minimal and strict: it adds only the fields required for ordering, causality, replay, signature verification, and cross-service correlation. Event-specific data lives inside `payload`.

## Scope

Applies to every emitter in the Egregore runtime, including but not limited to:

- `CorePlaneGenerateDossierExecutor` / `DossierGenerateService`
- `ZarcProvenanceSink` and the `.zarc` writer
- `Phase0TelemetryPulseAgent` / `PulseAdapter`
- `AEGIS-HIVE` sensors, `RFE` provenance store, `DT1` panelmesh runtime
- Federation treaty / escalation services
- `InterNodeMessenger`, `MycelialMesh`, and `ANCHORUM` bridge
- Any future cell, adapter, or HTTP handler that emits state-changing events

## Serialization contract

All events are serialized with the **canonical JSON** contract defined in `egregore/src/egregore/shared/canonical.py`:

- UTF-8 safe (`ensure_ascii=False`)
- Keys sorted recursively (`sort_keys=True`)
- No whitespace (`separators=(",", ":")`)
- NaN/Inf rejected fail-closed

Only `shared/canonical.py` may define `canonical_json` / `sha256_hex`. Emitters must route serialization through it. Source: `02_zarc_provenance.md` and `egregore/src/egregore/shared/canonical.py`.

## Canonical event header (every service must emit)

The following fields are mandatory at the top level of every emitted event. `payload` is the only place for event-specific data.

| Field | Type | Cardinality | Description |
|---|---|---|---|
| `event_id` | string | required | Stable, globally unique identifier. In Plane 1 it must be derived deterministically (see `egregore/src/egregore/shared/stable_ids.py`). Plane 2/edge may use UUIDv4 when deterministic derivation is not available. |
| `event_type` | string | required | Reverse-domain name: `egregore.<service>.<resource>.<verb>`. Examples: `egregore.dossier.generation.requested`, `egregore.telemetry.gate.sample`, `egregore.federation.escalation.opened`. |
| `timestamp_ns` | integer | required | Epoch nanoseconds. Core plane must inject a deterministic timestamp; wall-clock `time.time_ns()` is only allowed outside Plane 1. |
| `schema_version` | string | required | Event schema version. Current value: `egregore-event-v1.0.0`. |
| `source` | object | required | Identity of the emitting service/component. See table below. |
| `causality` | object | required | Request lineage. See table below. |
| `context` | object | optional | Multi-tenant / case / actor context. See table below. |
| `payload` | object | required | Event-specific payload. Must be a JSON object (not `null` or a scalar). Empty object `{}` is allowed when there is no domain data. |

### `source` object

| Field | Type | Cardinality | Description |
|---|---|---|---|
| `service` | string | required | Logical service name, dotted form, e.g. `core_plane.dossier_executor`, `cortex.pulse_agent`, `aegis_hive.sensor`. |
| `component` | string | required | Component or class name, e.g. `CorePlaneGenerateDossierExecutor`. |
| `node_id` | string | required | Logical node id. In tests this may be a stable fixture value; in production it is the runtime node id. |
| `cluster_id` | string | optional | Deployment cluster / federation cell. |
| `instance_version` | string | optional | Service semver, e.g. `1.0.0`. |

### `causality` object

| Field | Type | Cardinality | Description |
|---|---|---|---|
| `correlation_id` | string | required | Stable request lineage id. All events belonging to the same user/request/command share this value. |
| `causation_id` | string | recommended | Id of the command or message that caused this event. Required for Plane 1 audit events. |
| `parent_event_id` | string | optional | Immediate predecessor `event_id` in a causal chain. Use when an event is a direct consequence of another event. |

### `context` object

Optional, but required for domain events that belong to an organization/case:

| Field | Type | Cardinality | Description |
|---|---|---|---|
| `tenant_id` | string | optional | Top-level tenant / agency partition. |
| `organization_id` | string | optional | Organization scope (dossier domain). |
| `case_id` | string | optional | Case scope (dossier domain). |
| `version_id` | string | optional | Dossier or artifact version id. |
| `actor_id` | string | optional | Identity that triggered the operation. |
| `request_id` | string | optional | Ingress-plane request id; treated as a hint by Plane 1. |

## Provenance storage envelope

When an event is committed to the `.zarc` audit log, the provenance layer wraps the canonical header and payload. The on-wire `.zarc` line is:

```json
{
  "ts_ns": <timestamp_ns>,
  "engine": "<source.service>",
  "event": "<event_type>",
  "payload": { <canonical event without _provenance> },
  "prev_hash": "<sha256 of previous canonical line>",
  "sig": "<ed25519 signature over unsigned canonical record>"
}
```

| Field | Type | Cardinality | Description |
|---|---|---|---|
| `ts_ns` | integer | required | Same value as `timestamp_ns` from the canonical header. |
| `engine` | string | required | Same value as `source.service`. |
| `event` | string | required | Same value as `event_type`. |
| `payload` | object | required | The full canonical event (header + payload). |
| `prev_hash` | string | required | SHA256 hex of the previous canonical `.zarc` line. First line uses `"0" * 64`. |
| `sig` | string | required | Ed25519 signature (hex) over the unsigned canonical record (record without `sig`). |

Source: `egregore/src/egregore/kernel/provenance.py`.

## Domain-specific profiles

Emitters must satisfy the canonical header above. The following profiles add additional required fields inside `payload` for specific subsystems.

### Audit profile (dossier domain)

Used by `AuditEvent` and `OutboxEntry` construction in `egregore/src/egregore/domain/semantics/derivations.py`.

The canonical event's `payload` must contain:

| Field | Type | Cardinality | Description |
|---|---|---|---|
| `organization_id` | string | required | From `context.organization_id`. |
| `case_id` | string | required | From `context.case_id`. |
| `version_id` | string | required | From `context.version_id`. |
| `event_seq` | integer | required | Replay-reconstructable logical sequence number. |
| `causality_id` | string | required | From `causality.causation_id`. |
| `canonical_envelope` | object | required | Embedded `CanonicalEventEnvelope` (`envelope_id`, `causation_id`, `correlation_id`, `logical_timestamp_ns`, `producer_identity`, `envelope_schema_version`). Source: `egregore/src/egregore/domain/semantics/canonical_event_envelope.py`. |

### Telemetry profile

Used by `TelemetryEnvelope` in `egregore/src/egregore/infrastructure/telemetry/telemetry_collector.py`.

The canonical event's `payload` must contain:

| Field | Type | Cardinality | Description |
|---|---|---|---|
| `event_seq` | integer | required | Logical sequence number. |
| `gate` | string | required | Gate name: `cpu`, `memory`, `storage`, `network`, `gpu`, or `interconnect`. |
| `metrics` | object | required | Gate-specific metrics. At minimum must be an object; values may degrade deterministically in CPU-only skeletons. |

### Outbox profile

Used by `OutboxEntry` in `egregore/src/egregore/domain/semantics_models.py`.

The canonical event's `payload` must contain:

| Field | Type | Cardinality | Description |
|---|---|---|---|
| `side_effect_type` | string | required | E.g. `GOVERNANCE_INGEST`. |
| `outbox_id` | string | required | Stable outbox entry id. |
| `organization_id`, `case_id`, `version_id`, `causality_id` | strings | required | Same semantics as audit profile. |

## Event identity and ordering rules

1. **Uniqueness**: `event_id` must be unique within the Egregore deployment. Plane 1 derives it from deterministic inputs; Plane 2 may use UUIDv4.
2. **Ordering**: `timestamp_ns` is the primary logical clock. Within the same `correlation_id`, `event_seq` (inside payload) provides a replay-stable order.
3. **Causality**: `correlation_id` groups all events for a single request; `causation_id` points to the command; `parent_event_id` points to the immediate predecessor event.
4. **Immutability**: Once emitted, an event must never be mutated. Corrections are emitted as new events with `event_type` ending in `.corrected` and a `parent_event_id` referencing the original.
5. **Determinism**: Plane 1 emitters must produce bit-for-bit identical event streams when replayed from the same inputs. This implies deterministic `event_id`, `timestamp_ns`, and stable key ordering in payloads.

## Example event

```json
{
  "event_id": "a1b2c3d4e5f6...",
  "event_type": "egregore.dossier.generation.requested",
  "timestamp_ns": 1699900000000000000,
  "schema_version": "egregore-event-v1.0.0",
  "source": {
    "service": "core_plane.dossier_executor",
    "component": "CorePlaneGenerateDossierExecutor",
    "node_id": "node-7",
    "cluster_id": "us-east-1a",
    "instance_version": "1.0.0"
  },
  "causality": {
    "correlation_id": "req-abc-123",
    "causation_id": "cmd-abc-123",
    "parent_event_id": null
  },
  "context": {
    "tenant_id": "tenant-x",
    "organization_id": "org-42",
    "case_id": "case-99",
    "version_id": "v-1",
    "actor_id": "user-1",
    "request_id": "req-abc-123"
  },
  "payload": {
    "organization_id": "org-42",
    "case_id": "case-99",
    "version_id": "v-1",
    "event_seq": 0,
    "causality_id": "cmd-abc-123",
    "canonical_envelope": {
      "envelope_id": "req-abc-123",
      "causation_id": "cmd-abc-123",
      "correlation_id": "req-abc-123",
      "logical_timestamp_ns": 1699900000000000000,
      "producer_identity": "core_plane",
      "envelope_schema_version": "env-v1"
    }
  }
}
```

The same event committed to `.zarc` would appear as:

```json
{
  "ts_ns": 1699900000000000000,
  "engine": "core_plane.dossier_executor",
  "event": "egregore.dossier.generation.requested",
  "payload": { <canonical event above> },
  "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "sig": "<ed25519 hex>"
}
```

## Mapping to existing code

| Concept | Code location | Maps to schema section |
|---|---|---|
| Canonical JSON | `egregore/src/egregore/shared/canonical.py` | Serialization contract |
| `.zarc` record | `egregore/src/egregore/kernel/provenance.py` (`ZarcEntry`) | Provenance storage envelope |
| Domain provenance event | `egregore/src/egregore/domain/provenance_model.py` (`ProvenanceEvent`) | `payload` of `.zarc` record |
| Audit event | `egregore/src/egregore/domain/semantics_models.py` (`AuditEvent`) | Audit profile |
| Outbox entry | `egregore/src/egregore/domain/semantics_models.py` (`OutboxEntry`) | Outbox profile |
| Canonical envelope | `egregore/src/egregore/domain/semantics/canonical_event_envelope.py` | Embedded in audit/outbox payload |
| Stable event id | `egregore/src/egregore/shared/stable_ids.py` | `event_id` derivation |
| Telemetry envelope | `egregore/src/egregore/infrastructure/telemetry/telemetry_collector.py` | Telemetry profile |

## Machine-readable schema

The JSON Schema for this document lives at `schemas/egregore_event.schema.yaml`. It validates the canonical header and the provenance storage envelope. Domain-specific profiles (`audit`, `telemetry`, `outbox`) are validated by additional `payload` constraints in their respective code-level contracts until a registry is ratified.

## Conformance checklist for new services

Before a new service is considered integrated, it must:

- [ ] Emit every state-changing operation as a canonical event.
- [ ] Include all required header fields.
- [ ] Use `egregore-event-v1.0.0` for `schema_version`.
- [ ] Serialize with `egregore/src/egregore/shared/canonical.py`.
- [ ] Route meaningful events through a provenance sink so they are committed to `.zarc`.
- [ ] Derive `event_id` deterministically when running in Plane 1.
- [ ] Include a `correlation_id` shared across all events for the same request.
- [ ] Declare its event types in code reviews using the reverse-domain convention.

## Open questions

1. Should `schema_version` be semver (`1.0.0`) or a named tag (`egregore-event-v1`)? This draft uses `egregore-event-v1.0.0` to make the contract explicit.
2. Should the telemetry envelope be merged into the canonical header (replacing `gate`/`metrics` with a generic `observability` payload) or kept as a distinct profile? Kept distinct for now to match the existing telemetry collector.
3. Should a runtime schema registry (`egregore/src/egregore/shared/event_schema.py`) be introduced to enforce these fields in tests? Proposed but not implemented.
