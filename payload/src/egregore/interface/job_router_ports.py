"""Job router ports - injection-friendly protocols for grafted organs."""

from typing import Protocol, runtime_checkable

from egregore.domain.job_models import (
    JobClassification,
    JobRequest,
    NodeCapability,
    NodeHeartbeat,
    RoutingDecision,
    SealedEvidence,
)
from egregore.domain.job_router_ports import ILoadRegulator  # noqa: F401
from egregore.domain.scheduler_models import Job, QueueSnapshot


@runtime_checkable
class IJobClassifier(Protocol):
    def classify(self, request: JobRequest) -> JobClassification: ...


@runtime_checkable
class INodeRegistry(Protocol):
    def heartbeat(self, pulse: NodeHeartbeat) -> None: ...

    def get_available(self, capabilities: list[str]) -> list[NodeCapability]: ...

    def get_node(self, node_id: str) -> NodeCapability | None: ...

    def deprecate_stale(self, cutoff_ticks: int) -> list[str]: ...


@runtime_checkable
class IJobRouter(Protocol):
    def route(self, job: Job, candidates: list[NodeCapability]) -> RoutingDecision: ...


@runtime_checkable
class IScheduler(Protocol):
    def submit(self, job: Job) -> bool: ...

    def drain(self, tick: int, max_jobs: int) -> list[Job]: ...

    def get_queue_depth(self, tenant_id: str) -> dict: ...

    def snapshot(self, tick: int, tenant_id: str) -> QueueSnapshot: ...


@runtime_checkable
class IResilienceRouter(Protocol):
    def fallback(self, job: Job, primary_failure: str) -> RoutingDecision | None: ...

    def log_evidence(self, evidence: SealedEvidence) -> None: ...
