# Ollama VRAM Risk Acceptance Template

Status: pending operator signature

## Risk

A root-owned `ollama` process may remain active on a node where Egregore is expected to be the exclusive inference backend. This creates:

- VRAM contention
- scheduler distortion
- ambiguous operational ownership of inference output

## Current Expected Control

- `EGREGORE_DEFAULT_BACKEND=egregore`
- EMS on port `8001`
- user/systemd services pinned to Egregore backend
- Ollama stopped and disabled at the host level

## Accepted Exception

Document here why `ollama` remains active, for what duration, and on which nodes.

- Node(s):
- Reason:
- Start time:
- Planned removal time:
- Mitigations:

## Required Sign-off

- Operator:
- Reviewer:
- Timestamp:
- Signature reference:

## Exit Criteria

Risk acceptance expires when any of the following occur:

- `ollama` is stopped and disabled
- inference routing is proven exclusive to Egregore
- VRAM telemetry confirms no foreign inference residency on the node
