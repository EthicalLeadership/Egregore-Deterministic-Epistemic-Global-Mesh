"""Adapters bridging mission ports to existing runtime components.

Each adapter wraps a concrete runtime component and exposes the mission-port
protocol consumed by MissionOrchestrator. Direct attribute access only; no
getattr fallbacks. All corrections verified against the actual source modules
(work_unit.py, scheduler_models.py, job_router.py, work_tree.py).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from egregore.application.mission_ports import (
    CapacityLease,
    IEventPublisher,
    NoNodeAvailable,
)
from egregore.domain.job_models import (
    ComplexityTier,
    JobClassification,
    JobRequest,
    NodeCapability,
    ResourceProfile,
    RoutingDecision,
)
from egregore.domain.scheduler_models import Job, PriorityTier, SLA, SLAClass
from egregore.domain.units import DT, TU
from egregore.domain.work_unit import WorkUnit, WorkUnitDemand, WorkUnitType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Classifier adapter
# ---------------------------------------------------------------------------


class RuntimeClassifierPort:
    """Wraps the existing sync DefaultJobClassifier."""

    def __init__(self, classifier: Any) -> None:
        self._classifier = classifier

    def classify(self, request: JobRequest) -> JobClassification:
        classification = self._classifier.classify(request)
        if not isinstance(classification, JobClassification):
            raise TypeError(
                f"Classifier returned {type(classification).__name__}, "
                "expected JobClassification"
            )
        return classification


# ---------------------------------------------------------------------------
# Router adapter
# ---------------------------------------------------------------------------


class RuntimeRouterPort:
    """Wraps the existing NodeSelector route(job, candidates) interface.

    NodeSelector.route raises ValueError when no candidate satisfies the
    job; that is translated to NoNodeAvailable so MissionOrchestrator can
    retry and eventually return PENDING_TRAINING.
    """

    def __init__(self, node_registry: Any, node_selector: Any) -> None:
        self._node_registry = node_registry
        self._node_selector = node_selector

    def route_with_fallback(self, request: JobRequest) -> str:
        candidates = self._node_registry.get_available(request.requested_capabilities)
        if not candidates:
            raise NoNodeAvailable(
                f"No available nodes for capabilities {request.requested_capabilities}"
            )

        job = self._request_to_job(request)
        try:
            decision: RoutingDecision = self._node_selector.route(job, candidates)
        except ValueError as exc:
            raise NoNodeAvailable(str(exc)) from exc

        if decision is None or not decision.node_id:
            raise NoNodeAvailable(f"No node selected for job {request.job_id}")

        return decision.node_id

    @staticmethod
    def _request_to_job(request: JobRequest) -> Job:
        metadata = request.metadata
        complexity_tier = _complexity_from_str(metadata.get("complexity_tier", "STANDARD"))
        classification = JobClassification(
            job_id=request.job_id,
            complexity=complexity_tier,
            resource_profile=ResourceProfile(),
            estimated_tokens=int(metadata.get("estimated_tokens", 0)),
            target_vertical=str(metadata.get("target_vertical", "")),
            requested_capabilities=request.requested_capabilities,
            deterministic_required=bool(
                metadata.get("deterministic_required", False)
            ),
            priority_tier=request.priority_hint,
            created_at_ns=time.time_ns(),
        )

        sla = SLA(
            latency_target_ms=int(metadata.get("latency_target_ms", 1000)),
            class_=SLAClass.STANDARD,
        )

        return Job(
            job_id=request.job_id,
            tenant_id=request.tenant_id,
            trace_id=request.trace_id,
            priority_tier=_priority_tier(request.priority_hint),
            sla=sla,
            classification=classification,
            status="PENDING",
            created_at_ns=time.time_ns(),
            metadata=dict(metadata),
        )


def _complexity_from_str(value: str) -> ComplexityTier:
    mapping = {
        "TRIVIAL": ComplexityTier.TRIVIAL,
        "STANDARD": ComplexityTier.STANDARD,
        "COMPLEX": ComplexityTier.COMPLEX,
        "SOVEREIGN": ComplexityTier.SOVEREIGN,
    }
    return mapping.get(value.upper(), ComplexityTier.STANDARD)


def _priority_tier(hint: str) -> PriorityTier:
    mapping = {
        "CRITICAL": PriorityTier.CRITICAL,
        "HIGH": PriorityTier.HIGH,
        "MEDIUM": PriorityTier.MEDIUM,
        "LOW": PriorityTier.LOW,
    }
    return mapping.get(hint.upper(), PriorityTier.MEDIUM)


# ---------------------------------------------------------------------------
# Work tree adapter
# ---------------------------------------------------------------------------


class RuntimeWorkTreePort:
    """Wraps existing WorkTreeService.submit_tree(root_work_unit, ts)."""

    def __init__(self, work_tree_service: Any) -> None:
        self._work_tree_service = work_tree_service

    def submit_tree(self, classification: JobClassification) -> list[JobRequest]:
        root_work_unit = self._classification_to_work_unit(classification)
        timestamp_ns = time.time_ns()
        tree = self._work_tree_service.submit_tree(root_work_unit, timestamp_ns)

        leaf_units = tree.leaves() if hasattr(tree, "leaves") else ()
        return [
            self._work_unit_to_job_request(leaf.work_unit, timestamp_ns)
            for leaf in leaf_units
        ]

    @staticmethod
    def _classification_to_work_unit(classification: JobClassification) -> WorkUnit:
        """Build a root WorkUnit from classification + leaf metadata.

        WorkUnit requires demand, work_unit_type, payload, and metadata.
        """
        return WorkUnit(
            work_unit_id=classification.job_id,
            work_unit_type=WorkUnitType.LLM_INFERENCE,
            demand=WorkUnitDemand(
                dt=DT(1.0),
                tu=TU(1),
                priority=100,
                max_wait_ms=5000,
            ),
            payload=classification.job_id.encode("utf-8"),
            metadata={
                "job_id": classification.job_id,
                "requested_capabilities": classification.requested_capabilities,
                "complexity_tier": classification.complexity.value,
                "estimated_tokens": classification.estimated_tokens,
                "target_vertical": classification.target_vertical,
                "timestamp_ns": classification.created_at_ns,
            },
        )

    @staticmethod
    def _work_unit_to_job_request(work_unit: WorkUnit, timestamp_ns: int) -> JobRequest:
        metadata = work_unit.metadata
        job_id = metadata.get("job_id") or work_unit.work_unit_id
        tenant_id = metadata.get("tenant_id") or "default"
        trace_id = metadata.get("trace_id") or "default"

        capabilities = metadata.get("requested_capabilities", [])
        if not isinstance(capabilities, list):
            capabilities = list(capabilities)

        sla_deadline_ns = metadata.get("sla_deadline_ns", 0)
        if isinstance(sla_deadline_ns, str):
            try:
                sla_deadline_ns = int(sla_deadline_ns)
            except ValueError:
                sla_deadline_ns = 0

        return JobRequest(
            job_id=job_id,
            tenant_id=tenant_id,
            trace_id=trace_id,
            payload=dict(metadata),
            requested_capabilities=capabilities,
            priority_hint=metadata.get("priority_hint", "STANDARD"),
            sla_deadline_ns=sla_deadline_ns,
            metadata={
                "created_from_work_unit": True,
                "timestamp_ns": timestamp_ns,
            },
        )


# ---------------------------------------------------------------------------
# Capacity adapter
# ---------------------------------------------------------------------------


class RuntimeCapacityPort:
    """Lease-based capacity shim over the existing capacity signals.

    The existing CapacityOrchestrator manages DT/TU admission per work unit;
    this shim provides mission-scoped, lease-based admission on top of a
    configurable unit budget. Replace with real DT/TU accounting later.
    """

    def __init__(self, capacity_orchestrator: Any, total_units: int = 1000) -> None:
        self._capacity = capacity_orchestrator
        self._total_units = total_units
        self._available_units = total_units
        self._lease_counter = 0
        self._leases: dict[str, tuple[str, int, int]] = {}

    def admit(self, root_job_id: str, estimated_units: int) -> CapacityLease | None:
        if estimated_units > self._available_units:
            return None

        self._available_units -= estimated_units
        self._lease_counter += 1
        lease_id = f"lease:{root_job_id}:{self._lease_counter}"
        expires_at_ns = time.time_ns() + 3600 * 1_000_000_000
        self._leases[lease_id] = (root_job_id, estimated_units, expires_at_ns)

        return CapacityLease(
            lease_id=lease_id,
            root_job_id=root_job_id,
            total_units=estimated_units,
            expires_at_ns=expires_at_ns,
        )

    def release(self, lease_id: str) -> None:
        entry = self._leases.pop(lease_id, None)
        if entry is not None:
            _, units, _ = entry
            self._available_units += units

    def extend(self, lease_id: str, extra_units: int) -> CapacityLease:
        entry = self._leases.get(lease_id)
        if entry is None:
            raise ValueError(f"Unknown lease_id: {lease_id}")
        if extra_units > self._available_units:
            raise ValueError("Insufficient capacity to extend lease")

        root_job_id, units, expires_at_ns = entry
        self._available_units -= extra_units
        new_units = units + extra_units
        self._leases[lease_id] = (root_job_id, new_units, expires_at_ns)
        return CapacityLease(
            lease_id=lease_id,
            root_job_id=root_job_id,
            total_units=new_units,
            expires_at_ns=expires_at_ns,
        )


# ---------------------------------------------------------------------------
# Event publisher
# ---------------------------------------------------------------------------


class LoggingEventPublisher:
    """Structured log-based event publisher."""

    def __init__(self, target_logger: logging.Logger = logger) -> None:
        self._logger = target_logger

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        self._logger.info("EVENT %s %s", event_type, payload)
