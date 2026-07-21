"""
BLACKSTAR LAW: Causal Vector
Vector-clock-like ordering for ExecutionBlocks and PolicyDecisions.
Pure math — no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CausalVector:
    """
    Vector clock for causal ordering.

    Each key is a node/tenant ID, value is a monotonic counter.
    trace_id and span_id are for distributed tracing.
    parent_span_id links to the causal predecessor.
    """

    vector: dict[str, int] = field(default_factory=dict)
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""
    distributed: bool = False
    cross_node: bool = False

    def increment(self, node_id: str) -> CausalVector:
        """Return a new vector with node_id incremented by 1."""
        new_vector = dict(self.vector)
        new_vector[node_id] = new_vector.get(node_id, 0) + 1
        return CausalVector(
            vector=new_vector,
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            distributed=self.distributed,
            cross_node=self.cross_node,
        )

    def merge(self, other: CausalVector) -> CausalVector:
        """Return the element-wise max of two vectors."""
        merged = dict(self.vector)
        for key, value in other.vector.items():
            merged[key] = max(merged.get(key, 0), value)
        return CausalVector(
            vector=merged,
            trace_id=self.trace_id or other.trace_id,
            span_id=self.span_id or other.span_id,
            parent_span_id=self.parent_span_id or other.parent_span_id,
            distributed=self.distributed or other.distributed,
            cross_node=self.cross_node or other.cross_node,
        )

    def happens_before(self, other: CausalVector) -> bool:
        """
        Return True if self strictly happens before other.
        (self <= other and self != other)
        """
        if self == other:
            return False
        all_keys = set(self.vector.keys()) | set(other.vector.keys())
        for key in all_keys:
            if self.vector.get(key, 0) > other.vector.get(key, 0):
                return False
        return True

    def concurrent_with(self, other: CausalVector) -> bool:
        """Return True if neither happens before the other."""
        return not self.happens_before(other) and not other.happens_before(self)

    def is_valid_predecessor(self, other: CausalVector) -> bool:
        """
        Return True if other is a valid immediate successor of self.
        (other is self incremented by exactly one node by exactly 1)
        """
        diff = {}
        all_keys = set(self.vector.keys()) | set(other.vector.keys())
        for key in all_keys:
            a = self.vector.get(key, 0)
            b = other.vector.get(key, 0)
            if b != a:
                diff[key] = b - a

        # Exactly one node incremented by exactly 1, all others unchanged
        return len(diff) == 1 and list(diff.values())[0] == 1

    def to_canonical(self) -> dict:
        return {
            "vector": dict(sorted(self.vector.items())),
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "distributed": self.distributed,
            "cross_node": self.cross_node,
        }
