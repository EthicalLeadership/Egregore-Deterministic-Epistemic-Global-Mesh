"""WorkTree service — decomposition, leaf dispatch, rollup, completion records.

All timestamps are injected; the service performs no wall-clock access.
Completion is recorded through the existing SEL-X ``ExecutionRecord``
hash-chain (``generate_previous_record_hash``), not a parallel ledger.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from egregore.domain.execution_record import (
    ExecutionRecord,
    PolicyContext,
    generate_previous_record_hash,
    generate_record_id,
)
from egregore.domain.work_tree import WorkTree, WorkTreeError
from egregore.domain.work_unit import WorkUnit, WorkUnitState
from egregore.interface.work_tree_ports import (
    IWorkDecomposition,
    IWorkTreeStore,
    WorkSpec,
)


class _Scheduler(Protocol):
    def submit(self, work_unit: WorkUnit, timestamp_ns: int) -> bool: ...


class InMemoryWorkTreeStore:
    """Default in-memory ``IWorkTreeStore`` (process-scoped)."""

    def __init__(self) -> None:
        self._trees: dict[str, WorkTree] = {}

    def save(self, tree: WorkTree) -> None:
        self._trees[tree.tree_id] = tree

    def load(self, tree_id: str) -> WorkTree | None:
        return self._trees.get(tree_id)

    def list_tree_ids(self) -> Sequence[str]:
        return tuple(self._trees.keys())


@dataclass
class WorkTreeService:
    """Builds work trees, dispatches leaves, rolls up completion."""

    scheduler: _Scheduler
    decomposition: IWorkDecomposition
    store: IWorkTreeStore
    record_sink: Callable[[ExecutionRecord], None] | None = None
    _previous_record: ExecutionRecord | None = None
    _recorded_trees: set[str] = field(default_factory=set)

    def submit_tree(self, root_work_unit: WorkUnit, timestamp_ns: int) -> WorkTree:
        """Decompose ``root_work_unit``, build the tree, dispatch leaves."""
        spec = self.decomposition.decompose(root_work_unit)
        tree = WorkTree.create(root_work_unit, timestamp_ns)
        tree = self._build_children(tree, tree.root_id, spec.children, timestamp_ns)

        for leaf in tree.leaves():
            accepted = self.scheduler.submit(leaf.work_unit, timestamp_ns)
            tree = tree.mark_state(
                leaf.node_id,
                WorkUnitState.DISPATCHED if accepted else WorkUnitState.REJECTED,
            )

        self.store.save(tree)
        self._maybe_record_completion(tree, timestamp_ns)
        return tree

    def on_work_unit_state(
        self,
        work_unit_id: str,
        state: WorkUnitState,
        timestamp_ns: int,
    ) -> WorkTree:
        """Apply an external state update and recompute rollup."""
        for tree_id in self.store.list_tree_ids():
            tree = self.store.load(tree_id)
            if tree is None:
                continue
            node = tree.find_by_work_unit(work_unit_id)
            if node is None:
                continue
            tree = tree.mark_state(node.node_id, state)
            self.store.save(tree)
            self._maybe_record_completion(tree, timestamp_ns)
            return tree
        raise WorkTreeError(f"No tree contains work unit {work_unit_id!r}")

    # -- internals -----------------------------------------------------------

    def _build_children(
        self,
        tree: WorkTree,
        parent_id: str,
        children: tuple[WorkSpec, ...],
        timestamp_ns: int,
    ) -> WorkTree:
        for spec in children:
            tree = tree.add_child(parent_id, spec.work_unit, timestamp_ns)
            child_id = tree.get(parent_id).child_ids[-1]
            tree = self._build_children(tree, child_id, spec.children, timestamp_ns)
        return tree

    def _maybe_record_completion(self, tree: WorkTree, timestamp_ns: int) -> None:
        """Emit exactly one hash-chained ExecutionRecord per terminal tree."""
        if not tree.is_terminal() or tree.tree_id in self._recorded_trees:
            return
        root = tree.root
        metadata: dict[str, Any] = root.work_unit.metadata
        record = ExecutionRecord(
            record_id=generate_record_id(
                trace_id=tree.tree_id,
                timestamp_ns=timestamp_ns,
                operation="worktree_complete",
            ),
            timestamp_ns=timestamp_ns,
            tenant_id=str(metadata.get("tenant_id", "default")),
            principal_id=str(metadata.get("principal_id", "worktree-service")),
            role=str(metadata.get("role", "system")),
            session_id=str(metadata.get("session_id", tree.tree_id[:16])),
            trace_id=tree.tree_id,
            subsystem="worktree",
            operation="worktree_complete",
            policy_context=PolicyContext(
                policy_version=str(metadata.get("policy_version", "unversioned")),
                engine_version=str(metadata.get("engine_version", "unversioned")),
            ),
            previous_record_hash=generate_previous_record_hash(self._previous_record),
            payload={
                "tree_id": tree.tree_id,
                "final_state": root.state.name,
                "node_count": len(tree.nodes),
            },
            success=root.state == WorkUnitState.COMPLETED,
        ).with_integrity_hash()

        if self.record_sink is not None:
            self.record_sink(record)
        self._previous_record = record
        self._recorded_trees.add(tree.tree_id)
