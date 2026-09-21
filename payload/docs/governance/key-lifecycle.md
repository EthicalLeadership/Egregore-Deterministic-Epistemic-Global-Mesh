# Key Lifecycle

This document defines the current key ceremony and trust boundaries for Egregore provenance and model-promotion approval.

## Key Classes

1. `.zarc` provenance signing key

- Purpose: sign canonical `.zarc` event stream entries
- Primitive: Ed25519
- Current source: `EGREGORE_ZARC_SIGNING_KEY_HEX`
- Trust scope: system provenance and tamper-evident event history

1. Promotion approver key

- Purpose: verify human approval artifacts before EMS promotion
- Primitive: Ed25519
- Current source: `/etc/pioneer/keys/promotion_approver_pubkey.hex`
- Trust scope: human approval of model promotion

## Current State

The system currently uses the same Ed25519 primitive family for both flows, but the trust roots are distinct.

That means:

- `.zarc` provenance continuity depends on the provenance signing key
- model promotion authorization depends on the promotion approver key

This is acceptable only with explicit operational ceremony and rotation rules.

## Ceremony

### Generation

- Generate provenance signing key on a controlled host.
- Generate promotion approval signing key on a controlled host.
- Record custodian, creation time, purpose, and deployment scope.

### Storage

- Provenance private key must not be stored in source control.
- Promotion approval private key must not be stored on inference nodes.
- Promotion approval public key is distributed read-only to promotion nodes.

### Rotation

- Rotate on schedule or on role/personnel change.
- Before cutover, publish the new public key to target hosts.
- During cutover, record both old and new fingerprints in a governance note.
- Re-sign any in-flight approval artifacts after rotation.

### Revocation

- Revoke immediately on suspected compromise.
- Freeze promotion until the replacement public key is distributed.
- Freeze provenance-dependent release actions if provenance key integrity is uncertain.

### Compromise Recovery

- Promotion key compromised:
  - invalidate all unsigned or pending approval artifacts
  - distribute replacement public key
  - re-approve pending promotions
- Provenance key compromised:
  - freeze state-changing release actions
  - rotate key
  - issue governance incident note referencing the affected `.zarc` span

## Target End State

Preferred end state is one of:

1. Unified trust root with separated usage policy under a single documented ceremony.
2. Explicit dual-root model with formal owner assignment, rotation cadence, and incident runbook.

Until that decision is ratified, the system should treat the two-key model as an active governance burden, not an implicit default.
