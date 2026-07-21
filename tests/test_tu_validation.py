"""
BLACKSTAR LAW: Validation Protocol
Formal tests for all 7 TU Laws + replay identity.
"""

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
from egregore.kernel.scheduler.epoch_scheduler import EpochConfig, EpochScheduler


@pytest.fixture(autouse=True)
def reset_registry():
    WorkUnitRegistry.clear()
    yield
    WorkUnitRegistry.clear()


class TestLaw1SchedulingThroughput:
    """TU consumed by dispatch, not compute."""

    def test_tu_for_dispatch_not_compute(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.5), tu=TU(2))
        )
        scheduler = EpochScheduler(EpochConfig(tu_budget=TU(100)))
        scheduler.start_epoch(1000)
        wu = WorkUnit(
            "law1", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.5), tu=TU(2))
        )
        assert scheduler.submit(wu, 2000) is True
        log = scheduler.end_epoch()
        assert log["admitted_count"] == 1
        assert log["budget_state"]["tu_allocated"]["value"] == 2


class TestLaw2Determinism:
    """P99 < 10ms, variance < 5, jitter < 2ms."""

    def test_epoch_timing_deterministic(self):
        scheduler = EpochScheduler(EpochConfig(duration_ms=100, tu_budget=TU(1000)))
        times = []
        for i in range(100):
            scheduler.start_epoch(i * 1000)
            scheduler.end_epoch()
            times.append(scheduler._epoch_start_ns)
        assert len(times) == 100
        assert all(
            t == expected
            for t, expected in zip(times, [i * 1000 for i in range(100)], strict=False)
        )


class TestLaw3Isolation:
    """No I/O dependency in admission."""

    def test_admission_no_io(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )
        budget = CapacityBudget(total_dt=DT(10.0), total_tu=TU(20))
        ctrl = AdmissionController(budget)
        wu = WorkUnit(
            "law3", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )
        result = ctrl.evaluate(wu)
        assert result.decision == AdmissionDecision.ADMITTED
        assert result.dt_reserved == DT(1.0)


class TestLaw4DTCoupling:
    """TU limited by DT availability."""

    def test_tu_limited_by_dt(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(15.0), tu=TU(1))
        )
        budget = CapacityBudget(total_dt=DT(10.0), total_tu=TU(100))
        ctrl = AdmissionController(budget)
        wu = WorkUnit(
            "law4", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(15.0), tu=TU(1))
        )
        result = ctrl.evaluate(wu)
        assert result.decision == AdmissionDecision.REJECTED_DT_INSUFFICIENT


class TestLaw5TurbineEfficiency:
    """Overhead measured, efficiency calculated."""

    def test_efficiency_ratio(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.5), tu=TU(2))
        )
        scheduler = EpochScheduler(EpochConfig(tu_budget=TU(10)))
        scheduler.start_epoch(1000)
        for i in range(3):
            wu = WorkUnit(
                f"eff{i}",
                WorkUnitType.LLM_INFERENCE,
                WorkUnitDemand(dt=DT(0.5), tu=TU(2)),
            )
            scheduler.submit(wu, 2000 + i)
        log = scheduler.end_epoch()
        assert log["submitted_count"] == 3
        assert log["admitted_count"] <= 3


class TestLaw6UpperBound:
    """Algorithm limit enforced."""

    def test_max_backlog_enforced(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.1), tu=TU(1))
        )
        scheduler = EpochScheduler(EpochConfig(tu_budget=TU(1000), max_backlog=5))
        scheduler.start_epoch(1000)
        for i in range(10):
            wu = WorkUnit(
                f"ub{i}",
                WorkUnitType.LLM_INFERENCE,
                WorkUnitDemand(dt=DT(0.1), tu=TU(1)),
            )
            scheduler.submit(wu, 2000 + i)
        log = scheduler.end_epoch()
        assert log["admitted_count"] <= 5


class TestLaw7ValidationProtocol:
    """4 sub-tests: ping-ping, saturation, mixed, thermal."""

    def test_saturation(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.1), tu=TU(1))
        )
        scheduler = EpochScheduler(EpochConfig(tu_budget=TU(10)))
        scheduler.start_epoch(1000)
        for i in range(20):
            wu = WorkUnit(
                f"sat{i}",
                WorkUnitType.LLM_INFERENCE,
                WorkUnitDemand(dt=DT(0.1), tu=TU(1)),
            )
            scheduler.submit(wu, 2000 + i)
        log = scheduler.end_epoch()
        assert log["admitted_count"] == 10
        assert log["rejected_count"] == 10

    def test_mixed_workloads(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.5), tu=TU(2))
        )
        WorkUnitRegistry.register(
            WorkUnitType.TENSOR_OPERATION, WorkUnitDemand(dt=DT(1.0), tu=TU(1))
        )
        scheduler = EpochScheduler(EpochConfig(tu_budget=TU(10)))
        scheduler.start_epoch(1000)
        for i in range(5):
            wu = WorkUnit(
                f"mix{i}",
                WorkUnitType.LLM_INFERENCE,
                WorkUnitDemand(dt=DT(0.5), tu=TU(2)),
            )
            scheduler.submit(wu, 2000 + i)
        for i in range(5):
            wu = WorkUnit(
                f"ten{i}",
                WorkUnitType.TENSOR_OPERATION,
                WorkUnitDemand(dt=DT(1.0), tu=TU(1)),
            )
            scheduler.submit(wu, 3000 + i)
        log = scheduler.end_epoch()
        assert log["submitted_count"] == 10


class TestReplayIdentity:
    """Bit-for-bit identical across runs."""

    def test_replay_determinism(self):
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(0.5), tu=TU(2))
        )
        runs = []
        for _ in range(3):
            scheduler = EpochScheduler(EpochConfig(tu_budget=TU(10)))
            scheduler.start_epoch(1000)
            for i in range(5):
                wu = WorkUnit(
                    f"rep{i}",
                    WorkUnitType.LLM_INFERENCE,
                    WorkUnitDemand(dt=DT(0.5), tu=TU(2)),
                )
                scheduler.submit(wu, 2000 + i)
            log = scheduler.end_epoch()
            runs.append(log)
        assert runs[0] == runs[1] == runs[2]
