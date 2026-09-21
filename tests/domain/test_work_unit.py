import pytest

from egregore.domain.units import DT, TU
from egregore.domain.work_unit import (
    WorkUnitDemand,
    WorkUnitRegistry,
    WorkUnitState,
    WorkUnitType,
    create_work_unit,
)
from egregore.domain.work_unit_defaults import register_all_defaults


@pytest.fixture(autouse=True)
def reset_registry():
    WorkUnitRegistry.clear()
    yield
    WorkUnitRegistry.clear()


class TestWorkUnitDemand:
    def test_creation(self):
        demand = WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        assert demand.dt == DT(1.0)
        assert demand.tu == TU(2)
        assert demand.priority == 100

    def test_negative_priority_raises(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            WorkUnitDemand(dt=DT(1.0), tu=TU(2), priority=-1)

    def test_zero_wait_raises(self):
        with pytest.raises(ValueError, match="must be positive"):
            WorkUnitDemand(dt=DT(1.0), tu=TU(2), max_wait_ms=0)


class TestWorkUnit:
    def test_creation(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.5), tu=TU(2))
        )
        wu = create_work_unit(WorkUnitType.LLM_INFERENCE)
        assert wu.work_unit_type == WorkUnitType.LLM_INFERENCE
        assert wu.state == WorkUnitState.SUBMITTED
        assert wu.demand.dt == DT(0.5)

    def test_unregistered_type_raises(self):
        with pytest.raises(ValueError, match="unregistered type"):
            create_work_unit(WorkUnitType.LLM_INFERENCE)

    def test_immutability(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.5), tu=TU(2))
        )
        wu = create_work_unit(WorkUnitType.LLM_INFERENCE)
        with pytest.raises(AttributeError):
            wu.state = WorkUnitState.DISPATCHED

    def test_state_transition(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.5), tu=TU(2))
        )
        wu = create_work_unit(WorkUnitType.LLM_INFERENCE)
        wu2 = wu.with_state(WorkUnitState.ADMITTED)
        assert wu2.state == WorkUnitState.ADMITTED
        assert wu.state == WorkUnitState.SUBMITTED

    def test_metadata_update(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.5), tu=TU(2))
        )
        wu = create_work_unit(WorkUnitType.LLM_INFERENCE)
        wu2 = wu.with_metadata("gpu_id", "cuda:0")
        assert wu2.metadata["gpu_id"] == "cuda:0"
        assert "gpu_id" not in wu.metadata

    def test_canonical_serialization(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.5), tu=TU(2))
        )
        wu = create_work_unit(WorkUnitType.LLM_INFERENCE)
        canonical = wu.to_canonical()
        assert canonical["work_unit_type"] == "LLM_INFERENCE"
        assert canonical["state"] == "SUBMITTED"


class TestWorkUnitRegistry:
    def test_register_and_get(self):
        demand = WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        WorkUnitRegistry.register(WorkUnitType.LLM_INFERENCE, demand)
        assert WorkUnitRegistry.get_default_demand(WorkUnitType.LLM_INFERENCE) == demand

    def test_duplicate_registration_raises(self):
        demand = WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        WorkUnitRegistry.register(WorkUnitType.LLM_INFERENCE, demand)
        with pytest.raises(ValueError, match="already registered"):
            WorkUnitRegistry.register(WorkUnitType.LLM_INFERENCE, demand)

    def test_unregistered_lookup_raises(self):
        with pytest.raises(KeyError, match="M2 violation"):
            WorkUnitRegistry.get_default_demand(WorkUnitType.LLM_INFERENCE)

    def test_lock_prevents_registration(self):
        demand = WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        WorkUnitRegistry.register(WorkUnitType.LLM_INFERENCE, demand)
        WorkUnitRegistry.lock()
        with pytest.raises(RuntimeError, match="locked"):
            WorkUnitRegistry.register(WorkUnitType.TENSOR_OPERATION, demand)

    def test_register_all_defaults(self):
        register_all_defaults()
        assert len(WorkUnitRegistry.registered_types()) == 10
        assert WorkUnitRegistry.is_registered(WorkUnitType.LLM_INFERENCE)
        assert WorkUnitRegistry.is_registered(WorkUnitType.GOVERNANCE_AUDIT)
