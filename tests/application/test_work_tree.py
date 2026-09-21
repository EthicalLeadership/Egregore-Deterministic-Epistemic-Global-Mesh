"""Tests for the WorkTree domain model and WorkTreeService."""

from __future__ import annotations

import pytest

from egregore.application.work_tree_service import (
    InMemoryWorkTreeStore,
    WorkTreeService,
)
from egregore.domain.units import DT, TU
from egregore.domain.work_tree import (
    WorkTree,
    WorkTreeError,
    derive_node_id,
    derive_tree_id,
)
from egregore.domain.work_unit import WorkUnit, WorkUnitDemand, WorkUnitState, WorkUnitType
from egregore.interface.work_tree_ports import WorkSpec


def _wu(name: str, metadata: dict | None = None) -> WorkUnit:
    return WorkUnit(
        work_unit_id=f"wu-{name}",
        work_unit_type=WorkUnitType.GOVERNANCE_AUDIT,
        demand=WorkUnitDemand(dt=DT(1.0), tu=TU(1)),
        metadata=metadata or {},
    )


class _StaticDecomposition:
    """Deterministic fake decomposer: root -> [A -> [A1], B]."""

    def decompose(self, work_unit: WorkUnit) -> WorkSpec:
        return WorkSpec(
            work_unit=work_unit,
            children=(
                WorkSpec(
                    work_unit=_wu("A"),
                    children=(WorkSpec(work_unit=_wu("A1")),),
                ),
                WorkSpec(work_unit=_wu("B")),
            ),
        )


class _FakeScheduler:
    def __init__(self, accept: bool = True):
        self.accept = accept
        self.submitted: list[WorkUnit] = []

    def submit(self, work_unit: WorkUnit, timestamp_ns: int) -> bool:
        self.submitted.append(work_unit)
        return self.accept


def _make_service(accept: bool = True):
    scheduler = _FakeScheduler(accept=accept)
    store = InMemoryWorkTreeStore()
    records = []
    service = WorkTreeService(
        scheduler=scheduler,
        decomposition=_StaticDecomposition(),
        store=store,
        record_sink=records.append,
    )
    return service, scheduler, store, records


def _build_tree() -> WorkTree:
    tree = WorkTree.create(_wu("root"), timestamp_ns=1000)
    tree = tree.add_child(tree.root_id, _wu("A"), timestamp_ns=1000)
    a_id = tree.root.child_ids[0]
    tree = tree.add_child(a_id, _wu("A1"), timestamp_ns=1000)
    tree = tree.add_child(tree.root_id, _wu("B"), timestamp_ns=1000)
    return tree


class TestDeterministicIdentity:
    def test_tree_id_deterministic(self):
        assert derive_tree_id("wu-root") == derive_tree_id("wu-root")
        assert derive_tree_id("wu-root") != derive_tree_id("wu-other")

    def test_node_ids_deterministic_across_builds(self):
        first, second = _build_tree(), _build_tree()
        assert first.tree_id == second.tree_id
        assert sorted(first.nodes) == sorted(second.nodes)

    def test_node_id_matches_derivation(self):
        tree = _build_tree()
        assert tree.root_id == derive_node_id(tree.tree_id, "0")
        a_id = tree.root.child_ids[0]
        assert a_id == derive_node_id(tree.tree_id, "0/0")


class TestRollup:
    def test_all_children_complete_completes_parent(self):
        tree = _build_tree()
        a1_id = tree.get(tree.root.child_ids[0]).child_ids[0]
        b_id = tree.root.child_ids[1]
        tree = tree.mark_state(a1_id, WorkUnitState.COMPLETED)
        assert tree.get(tree.root.child_ids[0]).state == WorkUnitState.COMPLETED
        assert tree.root.state == WorkUnitState.EXECUTING  # B still pending
        tree = tree.mark_state(b_id, WorkUnitState.COMPLETED)
        assert tree.root.state == WorkUnitState.COMPLETED
        assert tree.is_complete()

    def test_any_child_failure_fails_parent(self):
        tree = _build_tree()
        b_id = tree.root.child_ids[1]
        tree = tree.mark_state(b_id, WorkUnitState.FAILED)
        assert tree.root.state == WorkUnitState.FAILED
        assert tree.is_terminal()

    def test_rejected_child_fails_parent_closed(self):
        tree = _build_tree()
        b_id = tree.root.child_ids[1]
        tree = tree.mark_state(b_id, WorkUnitState.REJECTED)
        assert tree.root.state == WorkUnitState.FAILED

    def test_terminal_node_cannot_transition(self):
        tree = _build_tree()
        b_id = tree.root.child_ids[1]
        tree = tree.mark_state(b_id, WorkUnitState.COMPLETED)
        with pytest.raises(WorkTreeError, match="terminal"):
            tree.mark_state(b_id, WorkUnitState.EXECUTING)

    def test_leaves(self):
        tree = _build_tree()
        leaf_ids = {node.work_unit.work_unit_id for node in tree.leaves()}
        assert leaf_ids == {"wu-A1", "wu-B"}


class TestGuards:
    def test_unknown_parent_rejected(self):
        tree = WorkTree.create(_wu("root"), timestamp_ns=1)
        with pytest.raises(WorkTreeError, match="Unknown node"):
            tree.add_child("nonexistent", _wu("X"), timestamp_ns=1)

    def test_unknown_node_mark_rejected(self):
        tree = _build_tree()
        with pytest.raises(WorkTreeError, match="Unknown node"):
            tree.mark_state("nonexistent", WorkUnitState.EXECUTING)

    def test_child_rejected_on_terminal_parent(self):
        tree = _build_tree()
        b_id = tree.root.child_ids[1]
        tree = tree.mark_state(b_id, WorkUnitState.FAILED)
        assert tree.root.state == WorkUnitState.FAILED
        with pytest.raises(WorkTreeError, match="terminal"):
            tree.add_child(tree.root_id, _wu("late"), timestamp_ns=2)


class TestService:
    def test_submit_dispatches_leaves_only(self):
        service, scheduler, store, _ = _make_service()
        tree = service.submit_tree(_wu("root"), timestamp_ns=1000)
        submitted_ids = {wu.work_unit_id for wu in scheduler.submitted}
        assert submitted_ids == {"wu-A1", "wu-B"}
        assert tree.root.state == WorkUnitState.EXECUTING
        assert store.load(tree.tree_id) is not None

    def test_rejected_dispatch_fails_root_and_records(self):
        service, scheduler, _, records = _make_service(accept=False)
        tree = service.submit_tree(_wu("root"), timestamp_ns=1000)
        assert tree.root.state == WorkUnitState.FAILED
        assert len(records) == 1
        record = records[0]
        assert record.success is False
        assert record.payload["final_state"] == "FAILED"
        assert record.previous_record_hash == "0" * 64

    def test_completion_record_emitted_exactly_once(self):
        service, _, _, records = _make_service()
        tree = service.submit_tree(_wu("root"), timestamp_ns=1000)
        for wu_id in ("wu-A1", "wu-B"):
            tree = service.on_work_unit_state(wu_id, WorkUnitState.COMPLETED, 2000)
        assert tree.root.state == WorkUnitState.COMPLETED
        # Repeat notifications must not duplicate the record.
        for wu_id in ("wu-A1", "wu-B"):
            service.on_work_unit_state(wu_id, WorkUnitState.COMPLETED, 3000)
        assert len(records) == 1
        assert records[0].success is True
        assert records[0].integrity_hash

    def test_records_chain_across_trees(self):
        service, scheduler, _, records = _make_service(accept=False)
        service.submit_tree(_wu("root1"), timestamp_ns=1000)
        service.submit_tree(_wu("root2"), timestamp_ns=2000)
        assert len(records) == 2
        assert records[1].previous_record_hash != "0" * 64
        assert records[1].previous_record_hash != records[0].previous_record_hash

    def test_deterministic_decomposition(self):
        first, *_ = _make_service()
        second, *_ = _make_service()
        tree1 = first.submit_tree(_wu("root"), timestamp_ns=1000)
        tree2 = second.submit_tree(_wu("root"), timestamp_ns=1000)
        assert tree1.tree_id == tree2.tree_id
        assert sorted(tree1.nodes) == sorted(tree2.nodes)

    def test_unknown_work_unit_update_fails_closed(self):
        service, *_ = _make_service()
        service.submit_tree(_wu("root"), timestamp_ns=1000)
        with pytest.raises(WorkTreeError, match="No tree contains"):
            service.on_work_unit_state("wu-ghost", WorkUnitState.COMPLETED, 2000)

    def test_to_canonical_shape(self):
        tree = _build_tree()
        canonical = tree.to_canonical()
        assert canonical["__type__"] == "WorkTree"
        assert canonical["node_count"] == 4
        assert set(canonical["nodes"].keys()) == {"0", "0/0", "0/0/0", "0/1"}
