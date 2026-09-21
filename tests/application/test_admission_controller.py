import pytest

from egregore.application.admission_controller import (
    AdmissionController,
    AdmissionDecision,
    CapacityBudget,
)
from egregore.domain.units import DT, TU
from egregore.domain.work_unit import (
    WorkUnit,
    WorkUnitDemand,
    WorkUnitRegistry,
    WorkUnitType,
)


@pytest.fixture(autouse=True)
def reset_registry():
    WorkUnitRegistry.unlock()
    WorkUnitRegistry.clear()
    yield
    WorkUnitRegistry.clear()


class TestCapacityBudget:
    def test_initial_state(self):
        budget = CapacityBudget(total_dt=DT(10.0), total_tu=TU(200))
        assert budget.available_dt == DT(9.0)
        assert budget.available_tu == TU(200)

    def test_allocate(self):
        budget = CapacityBudget(total_dt=DT(10.0), total_tu=TU(200))
        budget.allocate(DT(2.0), TU(3))
        assert budget.available_dt == DT(7.0)
        assert budget.available_tu == TU(197)

    def test_release(self):
        budget = CapacityBudget(total_dt=DT(10.0), total_tu=TU(200))
        budget.allocate(DT(2.0), TU(3))
        budget.release(DT(2.0), TU(3))
        assert budget.available_dt == DT(9.0)
        assert budget.available_tu == TU(200)

    def test_can_allocate(self):
        budget = CapacityBudget(total_dt=DT(10.0), total_tu=TU(200))
        assert budget.can_allocate(DT(5.0), TU(5)) is True
        assert budget.can_allocate(DT(15.0), TU(5)) is False


class TestAdmissionController:
    def test_admit(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )
        budget = CapacityBudget(total_dt=DT(10.0), total_tu=TU(200))
        ctrl = AdmissionController(budget)
        wu = WorkUnit(
            "test-1", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )
        result = ctrl.evaluate(wu)
        assert result.decision == AdmissionDecision.ADMITTED

    def test_reject_dt(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )
        budget = CapacityBudget(total_dt=DT(10.0), total_tu=TU(200))
        ctrl = AdmissionController(budget)
        wu = WorkUnit(
            "test-2", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(15.0), tu=TU(1))
        )
        result = ctrl.evaluate(wu)
        assert result.decision == AdmissionDecision.REJECTED_DT_INSUFFICIENT

    def test_reject_tu(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )
        budget = CapacityBudget(total_dt=DT(10.0), total_tu=TU(200))
        ctrl = AdmissionController(budget)
        wu = WorkUnit(
            "test-3", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(250))
        )
        result = ctrl.evaluate(wu)
        assert result.decision == AdmissionDecision.REJECTED_TU_INSUFFICIENT

    def test_reject_backlog(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.1), tu=TU(1))
        )
        budget = CapacityBudget(total_dt=DT(10.0), total_tu=TU(200))
        ctrl = AdmissionController(budget)
        for i in range(36):
            wu = WorkUnit(
                f"backlog-{i}",
                WorkUnitType.LLM_INFERENCE,
                WorkUnitDemand(dt=DT(0.1), tu=TU(1)),
            )
            ctrl.evaluate(wu)
        wu = WorkUnit(
            "full", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.1), tu=TU(1))
        )
        assert (
            ctrl.evaluate(wu).decision
            == AdmissionDecision.REJECTED_WAIT_EXCEEDS_THRESHOLD
        )

    def test_release(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(2.0), tu=TU(2))
        )
        budget = CapacityBudget(total_dt=DT(10.0), total_tu=TU(200))
        ctrl = AdmissionController(budget)
        wu = WorkUnit(
            "test-rel", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(2.0), tu=TU(2))
        )
        result = ctrl.evaluate(wu)
        ctrl.release(result.work_unit)
        assert ctrl._budget.available_dt == DT(9.0)
