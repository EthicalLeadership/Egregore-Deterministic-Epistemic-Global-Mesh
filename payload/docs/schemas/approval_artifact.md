# Approval Artifact Schema

The promotion approval artifact is a signed JSON document required by EMS promotion enforcement before a model promotion can be recorded.

Enforcement owner:

- `src/egregore/ems/registry.py::EmsRegistry.promote`
- Wrapper script: `tools/ai_promotion_gate.py`

## Purpose

This artifact binds a human approval decision to:

- a specific promotion target (`model_family`)
- a specific checkpoint scope (`checkpoints_dir`)
- a specific signed payload

Promotion is fail-closed if verification fails.

## JSON Shape

```json
{
  "schema_version": 1,
  "decision": "APPROVED",
  "approved_by": "operator@example",
  "approved_at_ns": 123456789,
  "expires_at_ns": 123556789,
  "model_family": "ops-copilot",
  "checkpoints_dir": "/mnt/pioneer_cluster/models/checkpoints",
  "human_approval_required": true,
  "rationale": "manual review complete"
}
```

## Fields

- `schema_version`: integer, recommended.
  - Current documentation version: `1`
- `decision`: string, required.
  - Current accepted value: `APPROVED`
- `approved_by`: string, required.
  - Human approver identity
- `approved_at_ns`: integer, required.
  - Nanosecond timestamp of approval decision
- `expires_at_ns`: integer, recommended.
  - Optional TTL boundary. Current code does not enforce expiry yet.
- `model_family`: string, required.
  - Must match the promotion target model id passed to EMS
- `checkpoints_dir`: string, required.
  - Must be an absolute path and must match the promotion checkpoints dir
- `human_approval_required`: boolean, required.
  - Must be `true`
- `rationale`: string, required.
  - Human-readable approval reason

## Signature Format

Detached signature files are hex-encoded Ed25519 signatures over the raw JSON artifact bytes.

Public key file:

- hex-encoded Ed25519 public key

Current implementation uses PyNaCl / Ed25519.

## Scope Binding Rules

EMS promotion rejects the artifact if:

- `decision != APPROVED`
- `human_approval_required != true`
- `model_family` does not equal the requested promotion model id
- `checkpoints_dir` does not equal the requested checkpoints dir
- signature verification fails
- required fields are missing or malformed

## TTL

Current implementation validates `approved_at_ns` but does not yet enforce `expires_at_ns`.

Operational policy recommendation:

- artifacts should include `expires_at_ns`
- expiry enforcement should be added before multi-node automated promotion expands further

## Audit Record

On successful verification, EMS first appends a canonical signed `.zarc` event and only then records a promotion row in the registry SQLite database `promotions` table with:

- `model_id`
- `checkpoints_dir`
- `approval_sha256`
- `signature_sha256`
- `approved_by`
- `approved_at_ns`
- `created_at`

If `.zarc` emission fails, promotion fails closed and the SQLite write does not happen.

The SQLite table is a mutable local cache. The `.zarc` event is the tamper-evident audit trail.

## Crypto Consistency Note

Current implementation uses the same Ed25519 primitive family as wider project provenance, but it is a separate approver public key path:

- `/etc/pioneer/keys/promotion_approver_pubkey.hex`

That means this is currently a distinct trust root unless operators intentionally align key management with the project provenance key hierarchy.

Key ceremony and lifecycle requirements are documented in [docs/governance/key-lifecycle.md](/home/kark/blackstar/docs/governance/key-lifecycle.md).
