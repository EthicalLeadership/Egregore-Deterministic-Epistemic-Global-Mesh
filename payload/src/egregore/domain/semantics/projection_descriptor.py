"""Projection descriptor domain types for CBI-0 Constraint Binding Interface.

These types convert the conceptual projection notion pi_A(IR) from Gate 4.2 PRR-0 into
concrete, typed, immutable structural objects that can be registered, validated, compared,
and hashed.

Authority note: this module defines data structures only. It does not define semantic rules
(owned by BIOK), projection contract semantics (Gate 4.1), or overlap classification rules
(Gate 4.2 PRR-0). It is a binding-layer data vocabulary.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from egregore.shared.canonical import canonical_json


class IRField(StrEnum):
    """Canonical structural field addresses over CanonicalSemanticIR.

    These are semantic addresses, not storage paths or Python attribute names.
    Defined per Gate 4.2 PRR-0 M3 minimum vocabulary. May expand only through
    versioned registry evolution.

    Mapping to CanonicalSemanticIR:
    - ENTITY_TYPE      → statement type discrimination (isinstance checks over SemanticStatement)
    - RELATION         → relationships between statements (not used in IR v1.0)
    - ATTRIBUTE        → content-bearing fields: content, source_id, evidence_reference,
                         interpretation, claim, classification, confidence
    - EVIDENCE_BLOCK   → EvidenceInterpretationStatement presence and fields
    - INFERENCE_NODE   → inference-adjacent fields (output-side; not IR input fields)
    - METADATA_BLOCK   → top-level IR metadata: version_id, reasoning_version_id
    """

    ENTITY_TYPE = "entity_type"
    RELATION = "relation"
    ATTRIBUTE = "attribute"
    EVIDENCE_BLOCK = "evidence_block"
    INFERENCE_NODE = "inference_node"
    METADATA_BLOCK = "metadata_block"


class SensitivityClass(StrEnum):
    """Required handling class for an agent's declared projection scope.

    STANDARD: general domain knowledge, no elevated handling requirements.
    RESTRICTED: fields that affect downstream reasoning outcomes; requires logging.
    SENSITIVE: fields with legal or regulatory implications; requires audit trail.
    """

    STANDARD = "standard"
    RESTRICTED = "restricted"
    SENSITIVE = "sensitive"


class OverlapClass(StrEnum):
    """Classification for non-empty projection overlap between two agents.

    Defined in Gate 4.2 PRR-0 M6. Reproduced here as the binding-layer vocabulary
    for registry validation and orchestration routing.

    DISJOINT:            no shared IR fields (|Overlap| = 0)
    EQUIVALENT:          semantically equivalent field surfaces; requires explicit declaration
    DEPENDENT:           one agent scope is a strict subset of the other
    INTERFERENCE_PRONE:  overlap on high-entropy or intermediate reasoning fields
    CONFLICT_SENSITIVE:  overlap on decision-affecting fields; requires arbitration policy ref
    """

    DISJOINT = "disjoint"
    EQUIVALENT = "equivalent"
    DEPENDENT = "dependent"
    INTERFERENCE_PRONE = "interference_prone"
    CONFLICT_SENSITIVE = "conflict_sensitive"


@dataclass(frozen=True)
class ProjectionConstraint:
    """A named structural constraint on an agent's projection behavior.

    Examples: "read_only", "evidence_bounded", "no_derived_output".
    These are not enforced by this dataclass — enforcement is CBI-0's responsibility.
    """

    name: str
    description: str = ""


@dataclass(frozen=True)
class ProjectionDescriptor:
    """Canonical registered description of one agent's observable IR slice.

    Implements Gate 4.2 PRR-0 M2 normative structure as an executable typed object.

    Invariants:
    - agent_id + version is a unique key in the registry (enforced by registry)
    - scope is the declared set of IRField addresses the agent is allowed to observe
    - constraints are additional behavioral restrictions (read-only, etc.)
    - sensitivity_level is required for audit routing
    - this dataclass is immutable; no field may change after registration

    Terminal: once registered, this object cannot be mutated. A new version must be
    registered instead.
    """

    agent_id: str
    version: str
    scope: frozenset[IRField]
    constraints: frozenset[ProjectionConstraint]
    sensitivity_level: SensitivityClass

    def canonical_hash(self) -> str:
        """SHA-256 hash of the canonical serialized form.

        Used for registry ledger commitments (Gate 4.2 PRR-0 S2).
        Hash is stable across Python processes because serialization is ordered.
        """
        payload = {
            "agent_id": self.agent_id,
            "version": self.version,
            "scope": sorted(f.value for f in self.scope),
            "constraints": sorted(c.name for c in self.constraints),
            "sensitivity_level": self.sensitivity_level.value,
        }
        canonical = canonical_json(payload)
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class OverlapClassification:
    """Declared classification for non-empty overlap between two agents.

    The pair (agent_id_a, agent_id_b) is unordered — the registry normalizes to
    lexicographic order. Overlap classification is symmetric.

    For CONFLICT_SENSITIVE class, arbitration_policy_ref must be non-empty.
    For all other classes, arbitration_policy_ref should be empty string.
    """

    agent_id_a: str
    agent_id_b: str
    overlap_class: OverlapClass
    arbitration_policy_ref: str = ""  # required non-empty if CONFLICT_SENSITIVE

    def __post_init__(self) -> None:
        if (
            self.overlap_class == OverlapClass.CONFLICT_SENSITIVE
            and not self.arbitration_policy_ref
        ):
            raise ValueError(
                f"Overlap between {self.agent_id_a!r} and {self.agent_id_b!r} is "
                f"CONFLICT_SENSITIVE but has no arbitration_policy_ref"
            )


@dataclass(frozen=True)
class BindingAuditRecord:
    """Structured evidence record from the spec/runtime equivalence audit hook (CBI-0 M4).

    Produced by IBindingAuditEmitter. Contains enough information to reconstruct the
    divergence for post-incident analysis.

    equivalence_status is "EQUIVALENT" or "DIVERGED".
    divergence_details is empty string when equivalent.
    """

    registry_hash: str
    runtime_state_hash: str
    equivalence_status: str  # "EQUIVALENT" | "DIVERGED"
    divergence_details: str  # empty if equivalent
    agent_id: str
    binding_hook_id: str  # "M1" | "M2" | "M3" | "M4"
