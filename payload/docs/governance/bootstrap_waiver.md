# Bootstrap Waiver Protocol

## Purpose

The Egregore M3 gate requires every terminal module to carry a signed
decommissioning attestation from the Dependency Safety Board (DSB). Until the
DSB is formally seated and its signing keys are distributed, the project may
issue **bootstrap waivers** as a controlled, temporary substitute.

A bootstrap waiver is a public commitment that:

1. The module is terminal and its decommissioning plan has been reviewed by the
core maintainers.
2. The waiver will be replaced by a full DSB attestation as soon as feasible.
3. The waiver can be revoked if the procedure is found to be incomplete or if
the module is modified in a way that invalidates the plan.

Without this protocol, the waiver would be a backdoor. With it, the waiver is a
bridge from bootstrap to sovereignty.

## Token Format

```
BOOTSTRAP-<YEAR>-<SEQUENCE>
```

Examples:

- `BOOTSTRAP-2026-001`
- `BOOTSTRAP-2026-012`

The sequence is zero-padded to three digits and increments per calendar year.

## Who Can Issue a Waiver

Bootstrap waivers are issued by the **Core Maintainer Quorum**. The quorum is
defined in the project constitution and currently requires approval from at
least two maintainers from distinct organizations.

## Issuance Process

1. A maintainer opens a public issue describing the terminal module, its
dependents, and the proposed decommissioning procedure.
2. At least one maintainer from a different organization reviews the procedure
and the test log.
3. The quorum records the waiver token, module ID, procedure path, test log
path, and expiry conditions in the governance transparency log.
4. The waiver token is added to the module's `egregore-module.json` under
`cbi0.m3.decom_manifest.attestation.bootstrap_waiver`.

## Expiry and Renewal

A bootstrap waiver expires on the earliest of:

- The seating of the Dependency Safety Board and publication of its signing keys.
- A calendar date set at issuance (not to exceed 12 months).
- Revocation by the Core Maintainer Quorum due to a material change in the
  module or its dependents.

To renew a waiver, the quorum must repeat the issuance process and issue a new
token. Reused or backdated tokens are invalid.

## Transparency

Every waiver must be recorded in the governance transparency log with the
following fields:

- `waiver_token`
- `module_id`
- `procedure_path`
- `test_log_path`
- `issued_by` (list of maintainer identities)
- `issued_at` (ISO 8601 timestamp)
- `expires_at` (ISO 8601 timestamp or "DSB_SEATED")
- `revoked_at` (if applicable)
- `revocation_reason` (if applicable)

## Enforcement

The M3 checker validates the token format and treats a non-empty, correctly
formatted token as a valid attestation substitute. The checker does not verify
the token against the transparency log at build time; that verification is the
responsibility of the governance audit pipeline and the dashboard. A module with
an unrecognized or revoked waiver will fail the governance audit even if it
passes the sandbox build gate.

## Transition to DSB Attestations

Once the DSB is seated, bootstrap waivers are frozen. No new waivers may be
issued. Existing waivers must be replaced by DSB attestations before their
expiry date. A DSB attestation is present when `signature`, `signer_id`, and
`timestamp` are all populated in the module manifest.
