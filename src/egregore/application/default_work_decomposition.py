"""Default deterministic work decomposition.

This implementation intentionally returns a leaf-only decomposition:
one work unit, no children. It satisfies IWorkDecomposition and is safe
for the first wiring pass. Complex decomposition policies can replace it
later without changing WorkTreeService.
"""

from __future__ import annotations

from egregore.domain.work_unit import WorkUnit
from egregore.interface.work_tree_ports import IWorkDecomposition, WorkSpec


class NoOpWorkDecomposition:
    """Leaf-only deterministic decomposition."""

    def decompose(self, work_unit: WorkUnit) -> WorkSpec:
        return WorkSpec(work_unit=work_unit, children=())
