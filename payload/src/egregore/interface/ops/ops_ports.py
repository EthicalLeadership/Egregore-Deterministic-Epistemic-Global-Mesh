"""
interface/ops/ops_ports.py

OPS Plane Interface Ports — PLANE 1 boundary definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class MetricValue:
    metric_name: str
    value: float
    timestamp_ns: int
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RandomnessConfig:
    zone_name: str = "default"
    seed_source: str = "fixed:42"
    mutation_rate_min: float = 0.0
    mutation_rate_max: float = 0.1
    jitter_fraction: float = 0.05
    poisson_lambda_hours: float = 1.0
    lottery_weights: dict[str, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_name": self.zone_name,
            "seed_source": self.seed_source,
            "mutation_rate_min": self.mutation_rate_min,
            "mutation_rate_max": self.mutation_rate_max,
            "jitter_fraction": self.jitter_fraction,
            "poisson_lambda_hours": self.poisson_lambda_hours,
            "lottery_weights": self.lottery_weights,
        }


@dataclass(frozen=True)
class EnergyBudgetStatus:
    node_id: str
    timestamp_ns: int
    total_budget_j: float
    consumed_j: float
    remaining_j: float
    consumption_rate_jps: float
    projected_depletion_s: float


@dataclass(frozen=True)
class ChaosEvent:
    event_id: str
    timestamp_ns: int
    event_type: str
    target: str
    duration_ms: int
    intensity: float
    seed: int


@runtime_checkable
class IKpiCollector(Protocol):
    def gauge(
        self, name: str, value: float, tags: dict[str, str] | None = None
    ) -> None: ...
    def counter(
        self, name: str, increment: float = 1.0, tags: dict[str, str] | None = None
    ) -> None: ...
    def histogram(
        self, name: str, value: float, tags: dict[str, str] | None = None
    ) -> None: ...
    def summary(
        self, name: str, value: float, tags: dict[str, str] | None = None
    ) -> None: ...
    def flush(self) -> list[MetricValue]: ...
    def record_buffer_snapshot(self, snapshot: Any) -> None: ...
    def record_sovereign_kpi(
        self, metric_name: str, value: float, tags: dict[str, str] | None = None
    ) -> None: ...
    def record_military_kpi(
        self, metric_name: str, value: float, tags: dict[str, str] | None = None
    ) -> None: ...
    def record_economic_kpi(
        self, metric_name: str, value: float, tags: dict[str, str] | None = None
    ) -> None: ...
    def record_ideological_kpi(
        self, metric_name: str, value: float, tags: dict[str, str] | None = None
    ) -> None: ...
    def record_internal_kpi(
        self, metric_name: str, value: float, tags: dict[str, str] | None = None
    ) -> None: ...
    def record_foreign_kpi(
        self, metric_name: str, value: float, tags: dict[str, str] | None = None
    ) -> None: ...
    def record_scientific_kpi(
        self, metric_name: str, value: float, tags: dict[str, str] | None = None
    ) -> None: ...
    def record_surveillance_kpi(
        self, metric_name: str, value: float, tags: dict[str, str] | None = None
    ) -> None: ...


@runtime_checkable
class IControlledRandomness(Protocol):
    def derive_seed(self, source: str, context: dict[str, Any]) -> int: ...
    def mutate(self, value: str, config: RandomnessConfig, seed: int) -> str: ...
    def jitter(
        self, base_value: float, config: RandomnessConfig, seed: int
    ) -> float: ...
    def lottery_select(
        self, candidates: list[str], config: RandomnessConfig, seed: int
    ) -> str: ...
    def poisson_next_event_ms(self, config: RandomnessConfig, seed: int) -> int: ...
    def log_randomness(
        self, operation: str, config: RandomnessConfig, seed: int, result: Any
    ) -> None: ...
    def get_log(self) -> list[dict[str, Any]]: ...
    def clear_log(self) -> None: ...


@runtime_checkable
class IEnergyGovernor(Protocol):
    def allocate(self, work_unit_id: str, budget_j: float) -> bool: ...
    def consume(self, work_unit_id: str, amount_j: float) -> EnergyBudgetStatus: ...
    def release(self, work_unit_id: str) -> EnergyBudgetStatus: ...
    def status(self, node_id: str, timestamp_ns: int) -> EnergyBudgetStatus: ...
    def shed_low_priority(self, required_j: float) -> list[str]: ...


@runtime_checkable
class IChaosEngineer(Protocol):
    def schedule_next(
        self, config: RandomnessConfig, current_time_ns: int
    ) -> ChaosEvent | None: ...
    def execute(self, event: ChaosEvent) -> bool: ...
    def history(self, node_id: str) -> list[ChaosEvent]: ...
    def get_active_events(self) -> list[ChaosEvent]: ...
    def run_chaos_loop(self, current_time_ns: int) -> ChaosEvent | None: ...
    def set_config(self, event_type: str, config: RandomnessConfig) -> None: ...


@runtime_checkable
class IJitBuffer(Protocol):
    def enqueue(self, item: Any) -> bool: ...
    def enqueue_async(self, item: Any) -> Any: ...
    def dequeue(self) -> Any | None: ...
    def dequeue_async(self) -> Any: ...
    def flush(self) -> None: ...
    def snapshot(self, timestamp_ns: int) -> Any: ...
    def backpressure_signal(self) -> Any: ...


@runtime_checkable
class IJitPipeline(Protocol):
    @property
    def config(self) -> Any: ...
    def inject(self, work_unit: Any) -> bool: ...
    def inject_async(self, work_unit: Any) -> Any: ...
    def snapshot(self, timestamp_ns: int) -> dict[str, Any]: ...
    def circuit_breaker_status(self) -> bool: ...
    def open_circuit(self, reason: str) -> None: ...
    def close_circuit(self) -> None: ...
    def start(self) -> Any: ...
    def stop(self) -> Any: ...
