from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class IAgentRouter(Protocol):
    """Port: maps a CanonicalSemanticIR slice to the agent IDs that should process it.

    Composition constraints intentionally deferred until Agent Composition Law pass.
    This Protocol is unconstrained at Phase 1 — no formal morphism algebra is enforced here.

    OPEN GAP (documented, not hidden):
    - Transition algebra is implicit: IR→LegalFact→RuleMatch→Inference is not proven
      associative or idempotent across agent boundaries.
    - No formal π_executor(IR) ≡ π_legal(IR) projection equivalence constraint.
    - Free orchestration composition: IAgentRouter is an unconstrained morphism
      composition point until Agent Composition Law is formalized.
    - Semantic class prohibition at orchestration level is incomplete: only field-level.
    """

    def route(self, ir: Any) -> tuple[str, ...]:
        """Return agent IDs to route IR to.

        Must return a non-empty tuple. Routing logic is implementation-defined.
        """
        ...


class IResultCombiner(Protocol):
    """Port: combines outputs from multiple agents into a unified result.

    Composition constraints intentionally deferred until Agent Composition Law pass.
    Implementations must not modify inputs — all inputs are terminal agent outputs.
    """

    def combine(self, results: Mapping[str, Any]) -> Mapping[str, Any]:
        """Combine agent results keyed by agent_id.

        Returns a new mapping. Must not modify inputs.
        """
        ...


@dataclass(frozen=True)
class OrchestratedResult:
    """Container for one orchestration pass over a CanonicalSemanticIR.

    Invariants (enforced by orchestration caller, not by this dataclass):
    - canonical_ir_version matches the IR used during routing
    - agent_outputs contains only terminal agent output structures
    - agent_outputs does not include any BIOK internal state

    NOTE: These invariants are NOT enforced here. Enforcement deferred to
    Agent Composition Law pass when second agent is onboarded.
    """

    canonical_ir_version: str
    agent_outputs: Mapping[str, Any]  # agent_id → agent output (terminal)
    routing_decision: tuple[str, ...]  # agent IDs selected by IAgentRouter
