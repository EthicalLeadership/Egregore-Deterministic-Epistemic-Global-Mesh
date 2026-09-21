# TaskIntent Schema

`TaskIntent` is the required execution envelope for agent invocations at the `AgentRunner` boundary.

## Purpose

It replaces raw free-form instructions with a typed contract that can be validated before any agent subprocess is launched.

Authority source:

- Caller identity is resolved before runner execution and passed as `caller_identity`.
- Agent admission is constrained by `AgentSpec.allowed_roles` in the agent registry.
- Task privilege demand is declared by `TaskIntent.required_privilege` and enforced against resolved caller identity in `src/egregore/domain/task_intent.py`.

## JSON Shape

```json
{
  "domain": "ops",
  "required_privilege": "operator",
  "output_sink": "chat",
  "human_approval_required": true,
  "instruction": "Collect service health and summarize drift."
}
```

## Fields

- `domain`: string, required.
  - Allowed values: `code`, `infra`, `legal`, `forensic`, `ops`, `general`
- `required_privilege`: string, required.
  - Allowed values: `read`, `operator`, `admin`
- `output_sink`: string, required.
  - Allowed values: `chat`, `log`, `artifact`
- `human_approval_required`: boolean, required.
  - Must be `true` for tasks that require a human release/review step downstream.
- `instruction`: string, required.
  - Must be non-empty.

Backward compatibility:

- `max_privilege` is rejected. Callers must send `required_privilege`.

## Privilege Model

Current ordered privilege vocabulary:

1. `read`
2. `operator`
3. `admin`

Current role-to-privilege mapping:

- `reader` -> `read`
- `read` -> `read`
- `operator` -> `operator`
- `admin` -> `admin`

Enforcement rules:

- Caller role must satisfy `AgentSpec.allowed_roles`.
- Caller identity status must be `active`.
- Caller identity privilege must be greater than or equal to `TaskIntent.required_privilege`.

This is a local execution-boundary privilege model backed by resolved `UserIdentity` role state at call time. It is not yet backed by a repository-wide external policy registry beyond the resolved identity object.

## Validation Rules

Validation occurs in `AgentRunner`, not the route handler.

The runner rejects:

- non-JSON payloads
- non-object payloads
- missing required fields
- unknown enum values
- non-boolean `human_approval_required`
- empty `instruction`
- privilege mismatches
- missing verified caller identity token

## Identity Verification

Agent execution requires a verified caller identity token at runner boundary.

- The runner rejects bare `caller_identity` dictionaries.
- The runner verifies `caller_identity_token` itself before privilege checks.
- Supported token formats are defined by `src/egregore/application/identity_port.py`.

## Legacy Shim

Raw string instructions are rejected by default.

Temporary compatibility mode exists behind:

- `EGREGORE_AGENT_LEGACY_TASK_INTENT=1`

Removal condition:

- remove the shim after `v0.8.0` or `2026-10-01`, whichever lands first

When enabled, raw text is wrapped into a conservative envelope:

- `domain=general`
- `output_sink=chat`
- `human_approval_required=true`
- `required_privilege` derived from resolved caller identity

This path emits a deprecation warning.
