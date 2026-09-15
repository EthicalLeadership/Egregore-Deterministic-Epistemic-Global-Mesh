# ADR-012: Site and Workspace Shared Kernel Boundary

- Status: Accepted conditionally; implementation gate remains closed for the
  case/evidence/audit extraction.
- Date: 2026-09-14
- Scope: ANCHORUM site projections, workspace commands, and shared domain
  definitions.

## Decision

ANCHORUM Site and ANCHORUM Workspace are one product with two responsibilities:

- the shared kernel owns stable domain types, validation, read models, and
  evidence state labels;
- Workspace owns mutations, authorization checks, and audit-origin commands;
- Site consumes query/read APIs and renders thin projections;
- Egregore remains the orchestration and tool-routing layer.

The first read model is **pull-on-read**. Site projections query the canonical
workspace/provider API when a view is requested or refreshed. An event-driven
projection may be added later, but only after the event schema, replay rules,
lag state, and retry semantics are separately approved. No local site cache may
be treated as the source of truth.

Changing to event-driven later would introduce failure modes that pull-on-read
does not have: stale projections during delivery lag, duplicate or out-of-order
events, missed events after consumer downtime, replay/version incompatibility,
and a second recovery path when the projection diverges from the provider.
The migration would therefore require an authoritative event envelope,
idempotency and ordering keys, durable consumer offsets, replay tooling,
dead-letter handling, freshness/lag status in the API, and a reconciliation
procedure. Until those exist, pull-on-read deliberately accepts provider
latency in exchange for avoiding an unmeasured second source of truth.

## Evidence versus Unknown

Every projection that reports case, evidence, timeline, or audit information
must label the state explicitly:

- `evidence-backed`: the value is present in the canonical provider response
  and carries its source identity;
- `unknown`: the provider did not establish the value, or the path is not yet
  part of the approved contract;
- `derived`: a deterministic projection from evidence-backed values, with the
  source references retained.

Unknown is not an empty value and must not be rendered as a confident fact.
Inference output must not upgrade `unknown` to evidence-backed.

## Import-Linter Contract

The normal test run enforces the following contract in
`tests/test_kernel_boundaries.py`:

- every root `*_kernel.py` and every `src/**/kernel/**/*.py` module must not
  import Tk/Tkinter, `ui_text`, `anchorum_desktop`, or a site-layer module;
- UI and transport adapters may import kernel modules;
- a kernel module may depend on standard-library code and other approved kernel
  modules only;
- adding a new kernel module automatically subjects it to the same AST check.

`timeline_kernel.py` is the first enforced module. `entity_timeline.py` is a
Tk adapter and is deliberately outside the kernel glob.

## Known Verification Gap

The desktop adapter wiring is statically verified and covered by the focused
module tests, but this environment has no `xvfb-run` or `Xvfb`. A display-backed
Tk smoke test confirming widget construction and context-menu installation is
therefore UNVERIFIED. This is a known verification limitation, not evidence
that the desktop path works. It must be closed in a GUI-capable CI job before
the desktop adapter is treated as production-verified.

## Consequences

Positive:

- Site and Workspace cannot silently create divergent case/timeline logic.
- Mutation and audit attribution have one planned owner.
- Import boundaries become executable policy rather than convention.

Required follow-up before the next extraction:

1. establish the canonical Case aggregate and its storage owner;
2. reconcile `Artifact` and ASDS `Evidence` without duplicate identity rules;
3. define the audit event schema and `.zarc`/journal relationship;
4. bind API authentication to case/workspace read scopes;
5. add query-contract tests for pull-on-read projections.

## Rejected for This Gate

An event-driven site read model is not selected yet. It would add replay,
lag, retry, and invalidation behavior before the canonical write and audit
contracts are settled.
