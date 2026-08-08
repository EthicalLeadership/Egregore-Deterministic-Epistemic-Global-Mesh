# epistemic marker: determinism / causality
"""WorkTree — deterministic work decomposition tree.

A WorkTree decomposes a root :class:`WorkUnit` into parent/child WorkUnit
trees with deterministic node identity and fail-closed rollup semantics:

- node IDs are derived (SHA-256 over ``tree_id|path|schema_version``), never
  random — the same decomposition always yields the same tree;
- rollup: a parent is ``COMPLETED`` iff all children complete; ``FAILED``
  (fail-closed) if any child fails or is rejected; ``EXECUTING`` while any
  child is in flight;
- cycles are impossible by construction (children are addressed by path);
- all timestamps are injected; this module does no I/O and no wall-clock.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from egregore.domain.work_unit import WorkUnit, WorkUnitState
from egregore.shared.canonical import sha256_hex

TERMINAL_STATES = frozenset(
    {WorkUnitState.COMPLETED, WorkUnitState.FAILED, WorkUnitState.REJECTED}
)


class WorkTreeError(Exception):
    """Fail-closed error for tree-structure violations."""


def derive_tree_id(root_work_unit_id: str, schema_version: int = 1) -> str:
    """Deterministic tree identity from the root work unit."""
    return sha256_hex(f"worktree|{root_work_unit_id}|{schema_version}".encode())


def derive_node_id(tree_id: str, path: str, schema_version: int = 1) -> str:
    """Deterministic node identity from tree + path."""
    return sha256_hex(f"{tree_id}|{path}|{schema_version}".encode())


@dataclass(frozen=True)
class WorkTreeNode:
    node_id: str
    tree_id: str
    path: str  # e.g. "0", "0/2", "0/2/1"
    parent_id: str | None
    work_unit: WorkUnit
    child_ids: tuple[str, ...] = ()
    state: WorkUnitState = WorkUnitState.SUBMITTED
    created_at_ns: int = 0


def _rollup_state(child_states: list[WorkUnitState]) -> WorkUnitState:
    """Fail-closed parent state from child states."""
    if any(
        state in (WorkUnitState.FAILED, WorkUnitState.REJECTED)
        for state in child_states
    ):
        return WorkUnitState.FAILED
    if all(state == WorkUnitState.COMPLETED for state in child_states):
        return WorkUnitState.COMPLETED
    if any(
        state in (WorkUnitState.EXECUTING, WorkUnitState.DISPATCHED, WorkUnitState.COMPLETED)
        for state in child_states
    ):
        return WorkUnitState.EXECUTING
    return WorkUnitState.SUBMITTED


@dataclass(frozen=True)
class WorkTree:
    tree_id: str
    schema_version: int
    root_id: str
    nodes: Mapping[str, WorkTreeNode] = field(default_factory=dict)

    # -- construction ------------------------------------------------------

    @classmethod
    def create(
        cls,
        root_work_unit: WorkUnit,
        timestamp_ns: int,
        schema_version: int = 1,
    ) -> WorkTree:
        tree_id = derive_tree_id(root_work_unit.work_unit_id, schema_version)
        root = WorkTreeNode(
            node_id=derive_node_id(tree_id, "0", schema_version),
            tree_id=tree_id,
            path="0",
            parent_id=None,
            work_unit=root_work_unit,
            created_at_ns=timestamp_ns,
        )
        return cls(
            tree_id=tree_id,
            schema_version=schema_version,
            root_id=root.node_id,
            nodes={root.node_id: root},
        )

    # -- queries -------------------------------------------------------------

    @property
    def root(self) -> WorkTreeNode:
        return self.nodes[self.root_id]

    def get(self, node_id: str) -> WorkTreeNode:
        node = self.nodes.get(node_id)
        if node is None:
            raise WorkTreeError(f"Unknown node: {node_id}")
        return node

    def find_by_work_unit(self, work_unit_id: str) -> WorkTreeNode | None:
        for node in self.nodes.values():
            if node.work_unit.work_unit_id == work_unit_id:
                return node
        return None

    def leaves(self) -> tuple[WorkTreeNode, ...]:
        return tuple(node for node in self.nodes.values() if not node.child_ids)

    def is_complete(self) -> bool:
        return self.root.state == WorkUnitState.COMPLETED

    def is_terminal(self) -> bool:
        return self.root.state in TERMINAL_STATES

    def to_canonical(self) -> dict:
        return {
            "__type__": "WorkTree",
            "tree_id": self.tree_id,
            "schema_version": self.schema_version,
            "root_id": self.root_id,
            "root_state": self.root.state.name,
            "node_count": len(self.nodes),
            "nodes": {
                node.path: {
                    "node_id": node.node_id,
                    "state": node.state.name,
                    "work_unit_id": node.work_unit.work_unit_id,
                    "child_ids": list(node.child_ids),
                }
                for node in sorted(self.nodes.values(), key=lambda n: n.path)
            },
        }

    # -- transitions (all return new trees) -----------------------------------

    def add_child(
        self,
        parent_id: str,
        work_unit: WorkUnit,
        timestamp_ns: int,
    ) -> WorkTree:
        parent = self.get(parent_id)
        if parent.state in TERMINAL_STATES:
            raise WorkTreeError(
                f"Cannot add child to terminal node {parent_id} ({parent.state.name})"
            )
        child_path = f"{parent.path}/{len(parent.child_ids)}"
        child_id = derive_node_id(self.tree_id, child_path, self.schema_version)
        if child_id in self.nodes:
            raise WorkTreeError(f"Duplicate node id: {child_id}")
        child = WorkTreeNode(
            node_id=child_id,
            tree_id=self.tree_id,
            path=child_path,
            parent_id=parent_id,
            work_unit=work_unit,
            created_at_ns=timestamp_ns,
        )
        nodes = dict(self.nodes)
        nodes[child_id] = child
        nodes[parent_id] = replace(
            parent, child_ids=parent.child_ids + (child_id,)
        )
        return replace(self, nodes=nodes)

    def mark_state(
        self,
        node_id: str,
        state: WorkUnitState,
    ) -> WorkTree:
        node = self.get(node_id)
        if node.state in TERMINAL_STATES and state != node.state:
            raise WorkTreeError(
                f"Node {node_id} is terminal ({node.state.name}); "
                f"cannot transition to {state.name}"
            )
        nodes = dict(self.nodes)
        nodes[node_id] = replace(node, state=state)
        tree = replace(self, nodes=nodes)
        return tree._rollup_ancestors(node.parent_id)

    def _rollup_ancestors(self, node_id: str | None) -> WorkTree:
        tree = self
        current_id = node_id
        while current_id is not None:
            parent = tree.nodes[current_id]
            child_states = [tree.nodes[cid].state for cid in parent.child_ids]
            if child_states:
                new_state = _rollup_state(child_states)
                if new_state != parent.state:
                    nodes = dict(tree.nodes)
                    nodes[current_id] = replace(parent, state=new_state)
                    tree = replace(tree, nodes=nodes)
            current_id = parent.parent_id
        return tree
