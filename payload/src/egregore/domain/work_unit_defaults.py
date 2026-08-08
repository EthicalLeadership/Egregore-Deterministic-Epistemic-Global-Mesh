"""
Default demand profiles for all registered Work Unit types.
Calibrated for Pioneer 1: Intel i5, 32GB DDR4, RTX 3060
"""

from egregore.domain.units import DT, TU
from egregore.domain.work_unit import WorkUnitDemand, WorkUnitRegistry, WorkUnitType


def register_all_defaults() -> None:
    if WorkUnitRegistry._locked:
        return
    WorkUnitRegistry.register(
        WorkUnitType.LLM_INFERENCE,
        WorkUnitDemand(dt=DT(0.5), tu=TU(2), priority=50, max_wait_ms=500),
    )
    WorkUnitRegistry.register(
        WorkUnitType.TENSOR_OPERATION,
        WorkUnitDemand(dt=DT(1.0), tu=TU(1), priority=75, max_wait_ms=1000),
    )
    WorkUnitRegistry.register(
        WorkUnitType.FEATURE_ENGINEERING,
        WorkUnitDemand(dt=DT(2.0), tu=TU(10), priority=100, max_wait_ms=2000),
    )
    WorkUnitRegistry.register(
        WorkUnitType.IMAP_INGESTION,
        WorkUnitDemand(dt=DT(0.05), tu=TU(5), priority=150, max_wait_ms=5000),
    )
    WorkUnitRegistry.register(
        WorkUnitType.DATABASE_QUERY,
        WorkUnitDemand(dt=DT(0.1), tu=TU(3), priority=120, max_wait_ms=3000),
    )
    WorkUnitRegistry.register(
        WorkUnitType.FILE_SYSTEM_SCAN,
        WorkUnitDemand(dt=DT(0.2), tu=TU(4), priority=130, max_wait_ms=4000),
    )
    WorkUnitRegistry.register(
        WorkUnitType.HYBRID_AI_AGENT,
        WorkUnitDemand(dt=DT(1.5), tu=TU(8), priority=80, max_wait_ms=1500),
    )
    WorkUnitRegistry.register(
        WorkUnitType.DATA_TURBINE_STREAM,
        WorkUnitDemand(dt=DT(0.5), tu=TU(8), priority=110, max_wait_ms=1000),
    )
    WorkUnitRegistry.register(
        WorkUnitType.GOVERNANCE_AUDIT,
        WorkUnitDemand(dt=DT(0.2), tu=TU(1), priority=10, max_wait_ms=100),
    )
    WorkUnitRegistry.register(
        WorkUnitType.PROVENANCE_COMPACTION,
        WorkUnitDemand(dt=DT(0.3), tu=TU(2), priority=20, max_wait_ms=200),
    )
    WorkUnitRegistry.lock()
