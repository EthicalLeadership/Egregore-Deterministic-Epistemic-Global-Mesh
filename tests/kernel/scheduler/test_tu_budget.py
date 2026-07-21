import pytest

from egregore.domain.units import DT, TU
from egregore.domain.work_unit import (
    WorkUnit,
    WorkUnitDemand,
    WorkUnitRegistry,
    WorkUnitType,
)
from egregore.kernel.scheduler.tu_budget import EpochConfig, TUBudget


@pytest.fixture(autouse=True)
def reset_registry():
    WorkUnitRegistry.clear()
    yield
    WorkUnitRegistry.clear()


class TestTUBudget:
    def test_epoch_start(self):
        budget = TUBudget(EpochConfig(tu_budget=TU(100)))
        budget.start_epoch(1000)
        assert budget.remaining == TU(100)

    def test_allocate(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.5), tu=TU(2))
        )
        budget = TUBudget(EpochConfig(tu_budget=TU(10)))
        budget.start_epoch(1000)
        wu = WorkUnit(
            "t1", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.5), tu=TU(2))
        )
        assert budget.allocate(wu, 2000) is True
        assert budget.remaining == TU(8)

    def test_allocate_exceeds_budget(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.5), tu=TU(10))
        )
        budget = TUBudget(EpochConfig(tu_budget=TU(5)))
        budget.start_epoch(1000)
        wu = WorkUnit(
            "t2", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.5), tu=TU(10))
        )
        assert budget.allocate(wu, 2000) is False

    def test_release(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.5), tu=TU(2))
        )
        budget = TUBudget(EpochConfig(tu_budget=TU(10)))
        budget.start_epoch(1000)
        wu = WorkUnit(
            "t3", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.5), tu=TU(2))
        )
        budget.allocate(wu, 2000)
        budget.release("t3")
        assert budget.remaining == TU(10)

    def test_max_backlog(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.1), tu=TU(1))
        )
        budget = TUBudget(EpochConfig(tu_budget=TU(1000), max_backlog=3))
        budget.start_epoch(1000)
        for i in range(4):
            wu = WorkUnit(
                f"b{i}",
                WorkUnitType.LLM_INFERENCE,
                WorkUnitDemand(dt=DT(0.1), tu=TU(1)),
            )
            budget.allocate(wu, 2000 + i)
        assert budget.allocated_count == 3
