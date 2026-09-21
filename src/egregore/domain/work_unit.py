"""
EGREGORE LAW: Work Unit Abstraction
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from egregore.domain.units import DT, TU


class WorkUnitState(Enum):
    SUBMITTED = auto()
    VALIDATED = auto()
    ADMITTED = auto()
    DISPATCHED = auto()
    EXECUTING = auto()
    COMPLETED = auto()
    REJECTED = auto()
    FAILED = auto()


class WorkUnitType(Enum):
    LLM_INFERENCE = auto()
    TENSOR_OPERATION = auto()
    FEATURE_ENGINEERING = auto()
    IMAP_INGESTION = auto()
    DATABASE_QUERY = auto()
    FILE_SYSTEM_SCAN = auto()
    HYBRID_AI_AGENT = auto()
    DATA_TURBINE_STREAM = auto()
    GOVERNANCE_AUDIT = auto()
    PROVENANCE_COMPACTION = auto()


@dataclass(frozen=True, slots=True)
class WorkUnitDemand:
    dt: DT
    tu: TU
    priority: int = 100
    max_wait_ms: int = 1000

    def __post_init__(self) -> None:
        if self.priority < 0:
            raise ValueError(f"Priority cannot be negative: {self.priority}")
        if self.max_wait_ms <= 0:
            raise ValueError(f"max_wait_ms must be positive: {self.max_wait_ms}")


@dataclass(frozen=True, slots=True)
class WorkUnit:
    work_unit_id: str
    work_unit_type: WorkUnitType
    demand: WorkUnitDemand
    payload: bytes = field(default=b"")
    metadata: dict[str, Any] = field(default_factory=dict)
    state: WorkUnitState = WorkUnitState.SUBMITTED

    def __post_init__(self) -> None:
        if not self.work_unit_id:
            raise ValueError("work_unit_id cannot be empty")
        if self.demand.dt < DT(0.0):
            raise ValueError("DT demand cannot be negative")
        if self.demand.tu < TU(0):
            raise ValueError("TU demand cannot be negative")

    def with_state(self, new_state: WorkUnitState) -> WorkUnit:
        return WorkUnit(
            work_unit_id=self.work_unit_id,
            work_unit_type=self.work_unit_type,
            demand=self.demand,
            payload=self.payload,
            metadata=self.metadata,
            state=new_state,
        )

    def with_metadata(self, key: str, value: Any) -> WorkUnit:
        new_metadata = dict(self.metadata)
        new_metadata[key] = value
        return WorkUnit(
            work_unit_id=self.work_unit_id,
            work_unit_type=self.work_unit_type,
            demand=self.demand,
            payload=self.payload,
            metadata=new_metadata,
            state=self.state,
        )

    def to_canonical(self) -> dict:
        return {
            "__type__": "WorkUnit",
            "work_unit_id": self.work_unit_id,
            "work_unit_type": self.work_unit_type.name,
            "demand": {
                "dt": self.demand.dt.to_canonical(),
                "tu": self.demand.tu.to_canonical(),
                "priority": self.demand.priority,
                "max_wait_ms": self.demand.max_wait_ms,
            },
            "state": self.state.name,
            "metadata_keys": list(self.metadata.keys()),
        }

    def __repr__(self) -> str:
        return (
            f"WorkUnit({self.work_unit_id}, "
            f"type={self.work_unit_type.name}, "
            f"dt={self.demand.dt}, tu={self.demand.tu}, "
            f"state={self.state.name})"
        )


class WorkUnitRegistry:
    _registry: dict[WorkUnitType, WorkUnitDemand] = {}
    _locked: bool = False

    @classmethod
    def register(
        cls, work_unit_type: WorkUnitType, default_demand: WorkUnitDemand
    ) -> None:
        if cls._locked:
            raise RuntimeError("Registry is locked")
        if work_unit_type in cls._registry:
            raise ValueError(f"Type {work_unit_type.name} already registered")
        cls._registry[work_unit_type] = default_demand

    @classmethod
    def get_default_demand(cls, work_unit_type: WorkUnitType) -> WorkUnitDemand:
        if work_unit_type not in cls._registry:
            raise KeyError(f"Type {work_unit_type.name} not registered — M2 violation")
        return cls._registry[work_unit_type]

    @classmethod
    def is_registered(cls, work_unit_type: WorkUnitType) -> bool:
        return work_unit_type in cls._registry

    @classmethod
    def lock(cls) -> None:
        cls._locked = True

    @classmethod
    def unlock(cls) -> None:
        cls._locked = False

    @classmethod
    def registered_types(cls) -> list:
        return list(cls._registry.keys())

    @classmethod
    def clear(cls) -> None:
        cls._registry.clear()
        cls._locked = False


def create_work_unit(
    work_unit_type: WorkUnitType,
    demand: WorkUnitDemand | None = None,
    payload: bytes = b"",
    metadata: dict[str, Any] | None = None,
    schema_version: int = 1,
    event_seq: int = 0,
) -> WorkUnit:
    if not WorkUnitRegistry.is_registered(work_unit_type):
        raise ValueError(
            f"Cannot create Work Unit of unregistered type {work_unit_type.name}"
        )

    if demand is None:
        demand = WorkUnitRegistry.get_default_demand(work_unit_type)

    # Use a simple UUID for now; we'll later replace with stable_event_id
    work_unit_id = f"wu-{uuid.uuid4().hex[:16]}"

    return WorkUnit(
        work_unit_id=work_unit_id,
        work_unit_type=work_unit_type,
        demand=demand,
        payload=payload,
        metadata=metadata or {},
    )
