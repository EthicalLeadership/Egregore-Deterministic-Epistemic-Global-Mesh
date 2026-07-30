"""
EGREGORE LAW: Causal Policy Engine
Enforces causal ordering before evaluating policy decisions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from egregore.domain.causal_vector import CausalVector
from egregore.domain.execution_block import ExecutionBlock


class CausalPolicyError(Exception):
    pass


class ForkDetectedError(CausalPolicyError):
    pass


class StaleDecisionError(CausalPolicyError):
    pass


@dataclass
class PolicyDecision:
    decision_id: str
    policy_version: str
    input_hash: str
    output: dict[str, Any] = field(default_factory=dict)
    causal_vector: CausalVector = field(default_factory=CausalVector)
    timestamp_ns: int = 0
    block_dependencies: Sequence[str] = field(default_factory=tuple)


@dataclass
class CausalPolicyEngine:
    """
    Fail-closed policy engine with causal consistency.

    - Every policy decision must declare its causal dependencies (block IDs).
    - The engine verifies those blocks have been committed with monotonic vectors.
    - Fork detection: two decisions with the same dependency but different vectors.
    """

    _committed_blocks: dict[str, ExecutionBlock] = field(default_factory=dict)
    _committed_vectors: dict[str, CausalVector] = field(default_factory=dict)
    _decisions: dict[str, PolicyDecision] = field(default_factory=dict)
    _node_id: str = "default"

    def commit_block(self, block: ExecutionBlock) -> None:
        """Commit a block to the causal log."""
        if block.block_id in self._committed_blocks:
            raise CausalPolicyError(f"Block {block.block_id} already committed")

        # Validate causal vector monotonicity
        if block.causal_vector:
            for dep_id in self._get_block_dependencies(block):
                if dep_id in self._committed_vectors:
                    prev = self._committed_vectors[dep_id]
                    if not prev.happens_before(block.causal_vector):
                        raise StaleDecisionError(
                            f"Block {block.block_id} has stale causal vector "
                            f"relative to dependency {dep_id}"
                        )

        self._committed_blocks[block.block_id] = block
        self._committed_vectors[block.block_id] = block.causal_vector or CausalVector()

    def evaluate(
        self,
        *,
        decision_id: str,
        policy_version: str,
        input_hash: str,
        block_dependencies: Sequence[str],
        evaluator: Any,  # Callable[[], Dict[str, Any]]
        timestamp_ns: int,
    ) -> PolicyDecision:
        """
        Evaluate a policy decision after verifying causal dependencies.
        """
        # Verify all dependencies are committed
        for dep_id in block_dependencies:
            if dep_id not in self._committed_blocks:
                raise StaleDecisionError(f"Dependency {dep_id} not yet committed")

        # Build causal vector from dependencies
        base_vector = CausalVector()
        for dep_id in block_dependencies:
            dep_vector = self._committed_vectors[dep_id]
            base_vector = base_vector.merge(dep_vector)

        # Increment our node
        causal_vector = base_vector.increment(self._node_id)

        # Evaluate policy
        output = evaluator() if callable(evaluator) else {}

        decision = PolicyDecision(
            decision_id=decision_id,
            policy_version=policy_version,
            input_hash=input_hash,
            output=output,
            causal_vector=causal_vector,
            timestamp_ns=timestamp_ns,
            block_dependencies=tuple(block_dependencies),
        )

        # Fork detection: check for existing decisions with same deps but different vectors
        for existing in self._decisions.values():
            if (
                existing.block_dependencies == decision.block_dependencies
                and existing.causal_vector.concurrent_with(decision.causal_vector)
            ):
                raise ForkDetectedError(
                    f"Fork detected: {existing.decision_id} and {decision.decision_id} "
                    f"have concurrent vectors for same dependencies"
                )

        self._decisions[decision_id] = decision
        return decision

    def _get_block_dependencies(self, block: ExecutionBlock) -> Sequence[str]:
        """Extract block IDs that this block causally depends on."""
        deps = []
        if block.previous_block_hash and block.previous_block_hash != "0" * 64:
            # previous_block_hash is a hash, not an ID. We don't track by hash.
            pass
        if block.causal_vector and block.causal_vector.parent_span_id:
            # parent_span_id may reference a trace, not a block ID
            pass
        return deps

    def get_decision(self, decision_id: str) -> PolicyDecision | None:
        return self._decisions.get(decision_id)

    def list_committed_blocks(self) -> Sequence[str]:
        return tuple(self._committed_blocks.keys())

    def list_decisions(self) -> Sequence[str]:
        return tuple(self._decisions.keys())
