"""JobRuntime - shared runtime services for chat command handlers.

This module was reconstructed from existing service classes.
It exposes the services needed by chat_interpreter.py and bootstrap.py,
and includes a placeholder for MissionOrchestrator integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from egregore.application.capacity_orchestrator import CapacityOrchestrator
from egregore.application.default_job_classifier import DefaultJobClassifier
from egregore.application.default_work_decomposition import NoOpWorkDecomposition
from egregore.application.job_router import NodeSelector
from egregore.application.job_router_scheduler_adapter import (
    JobRouterSchedulerAdapter,
)
from egregore.application.mission_adapters import (
    LoggingEventPublisher,
    RuntimeCapacityPort,
    RuntimeClassifierPort,
    RuntimeRouterPort,
    RuntimeWorkTreePort,
)
from egregore.application.mission_orchestrator import MissionOrchestrator
from egregore.application.node_registry import InMemoryNodeStore, NodeRegistry
from egregore.application.scheduler import InMemoryJobStore, JobScheduler
from egregore.application.work_tree_service import (
    InMemoryWorkTreeStore,
    WorkTreeService,
)


@dataclass
class JobRuntime:
    """Container for shared runtime services used by chat command handlers."""

    node_registry: NodeRegistry
    classifier: DefaultJobClassifier
    scheduler: JobScheduler
    work_tree_service: WorkTreeService
    node_selector: NodeSelector
    job_router_scheduler_adapter: JobRouterSchedulerAdapter
    mission_orchestrator: Any | None = None


def build_job_runtime() -> JobRuntime:
    """Build a default runtime with in-memory stores and simple decomposition.

    Returns:
        JobRuntime with all required services. MissionOrchestrator is left
        as None until the mission adapters are fully validated.
    """
    node_store = InMemoryNodeStore()
    node_registry = NodeRegistry(store=node_store)

    classifier = DefaultJobClassifier()
    node_selector = NodeSelector()

    job_store = InMemoryJobStore()
    job_scheduler = JobScheduler(store=job_store)

    # Adapter bridges WorkTreeService's WorkUnit scheduler to the job layer.
    adapter = JobRouterSchedulerAdapter(
        classifier=classifier,
        node_registry=node_registry,
        job_router=node_selector,
        scheduler=job_scheduler,
        default_tenant_id="default",
        default_trace_id="default",
    )

    work_tree_service = WorkTreeService(
        scheduler=adapter,
        decomposition=NoOpWorkDecomposition(),
        store=InMemoryWorkTreeStore(),
    )

    # Mission orchestration layer: lease-based capacity, idempotent,
    # parallel routing on top of the existing runtime services.
    capacity_orchestrator = CapacityOrchestrator.build_default()
    mission_orchestrator = MissionOrchestrator(
        classifier=RuntimeClassifierPort(classifier),
        work_tree=RuntimeWorkTreePort(work_tree_service),
        capacity=RuntimeCapacityPort(capacity_orchestrator),
        router=RuntimeRouterPort(node_registry, node_selector),
        event_publisher=LoggingEventPublisher(),
    )

    return JobRuntime(
        node_registry=node_registry,
        classifier=classifier,
        scheduler=job_scheduler,
        work_tree_service=work_tree_service,
        node_selector=node_selector,
        job_router_scheduler_adapter=adapter,
        mission_orchestrator=mission_orchestrator,
    )
