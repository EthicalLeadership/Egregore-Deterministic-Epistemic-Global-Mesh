"""JobRouterSchedulerAdapter — bridges WorkTreeService._Scheduler to the distributed job layer."""

from __future__ import annotations

import dataclasses
import logging
import time
from typing import Any, Protocol

from egregore.domain.job_models import (
    ComplexityTier,
    Job,
    JobClassification,
    JobRequest,
    JobStatus,  # FIX: added missing import
    NodeCapability,
    PriorityTier,
    ResourceProfile,
    RoutingDecision,
    SLA,
    SLAClass,
)
from egregore.domain.work_unit import WorkUnit, WorkUnitType

logger = logging.getLogger(__name__)


class IJobClassifier(Protocol):
    def classify(self, request: JobRequest) -> JobClassification: ...


class INodeRegistry(Protocol):
    def get_available(self, capabilities: list[str]) -> list[NodeCapability]: ...


class IJobRouter(Protocol):
    def route(self, job: Job, candidates: list[NodeCapability]) -> RoutingDecision: ...


class IResilienceRouter(Protocol):
    def fallback(self, job: Job, primary_failure: str) -> RoutingDecision | None: ...


class IScheduler(Protocol):
    def submit(self, job: Job) -> bool: ...


class JobRouterSchedulerAdapter:
    """Adapter: _Scheduler protocol → distributed job routing + scheduling."""

    def __init__(
        self,
        classifier: IJobClassifier,
        node_registry: INodeRegistry,
        job_router: IJobRouter,
        scheduler: IScheduler,
        resilience_router: IResilienceRouter | None = None,
        default_tenant_id: str = "default",
        default_trace_id: str = "default",
    ) -> None:
        self._classifier = classifier
        self._node_registry = node_registry
        self._job_router = job_router
        self._scheduler = scheduler
        self._resilience_router = resilience_router
        self._default_tenant_id = default_tenant_id
        self._default_trace_id = default_trace_id

    def submit(self, work_unit: WorkUnit, timestamp_ns: int) -> bool:
        """Route a single WorkUnit leaf through the job layer.

        Returns True if the job was successfully classified, routed, and queued.
        Returns False if no capable node is available or scheduler rejects.
        """
        try:
            return self._submit(work_unit, timestamp_ns)
        except Exception:
            logger.exception(
                "Adapter submit failed for work_unit_type=%s",
                getattr(work_unit, "work_unit_type", "UNKNOWN"),
            )
            return False

    # ------------------------------------------------------------------
    # Internal flow
    # ------------------------------------------------------------------

    def _submit(self, work_unit: WorkUnit, timestamp_ns: int) -> bool:
        # 1. Convert WorkUnit → JobRequest
        job_request = self._work_unit_to_job_request(work_unit, timestamp_ns)

        # 2. Classify
        classification = self._classifier.classify(job_request)

        # 3. Convert JobClassification → Job
        job = self._classification_to_job(classification, job_request, timestamp_ns)

        # 4. Find candidate nodes
        candidates = self._node_registry.get_available(
            classification.requested_capabilities
        )
        if not candidates:
            logger.warning(
                "No available nodes for capabilities=%s job_id=%s",
                classification.requested_capabilities,
                job.job_id,
            )
            return False

        # 5. Route
        decision = self._job_router.route(job, candidates)

        # FIX: Handle None decision explicitly
        if decision is None:
            logger.warning("Router returned None for job_id=%s", job.job_id)
            decision = RoutingDecision(node_id="", decision_reason="router_none")

        # 6. Fallback if primary routing failed
        if not decision.node_id:
            if self._resilience_router is not None:
                fallback = self._resilience_router.fallback(
                    job, f"primary_route_empty:{decision.decision_reason}"
                )
                if fallback is not None and fallback.node_id:
                    decision = fallback
                else:
                    logger.warning(
                        "Routing failed and no fallback for job_id=%s", job.job_id
                    )
                    return False
            else:
                logger.warning(
                    "Routing returned empty node_id for job_id=%s", job.job_id
                )
                return False

        # 7. Assign node and submit to scheduler
        # FIX: Ensure Job has field assigned_node_id
        routed_job = dataclasses.replace(job, assigned_node_id=decision.node_id)
        accepted = self._scheduler.submit(routed_job)

        if not accepted:
            logger.warning(
                "Scheduler rejected job_id=%s node_id=%s",
                routed_job.job_id,
                decision.node_id,
            )
            return False

        logger.info(
            "Routed job_id=%s to node_id=%s complexity=%s",
            routed_job.job_id,
            decision.node_id,
            classification.complexity,
        )
        return True

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    def _work_unit_to_job_request(
        self, work_unit: WorkUnit, timestamp_ns: int
    ) -> JobRequest:
        """Extract JobRequest fields from WorkUnit metadata."""

        metadata = getattr(work_unit, "metadata", None)
        if not isinstance(metadata, dict):
            metadata = {}

        job_id = (
            metadata.get("job_id")
            or metadata.get("work_unit_id")
            or f"wu-{id(work_unit)}"
        )
        tenant_id = metadata.get("tenant_id") or self._default_tenant_id
        trace_id = (
            metadata.get("trace_id")
            or metadata.get("mission_id")
            or self._default_trace_id
        )

        # Map WorkUnitType to requested capabilities
        wu_type = getattr(work_unit, "work_unit_type", None)
        capabilities = self._capabilities_for_type(wu_type)

        # Merge explicit capabilities from metadata
        explicit_caps = metadata.get("requested_capabilities", [])
        if isinstance(explicit_caps, list):
            capabilities = list(dict.fromkeys(capabilities + explicit_caps))

        payload = dict(metadata)
        payload["_work_unit_type"] = str(wu_type) if wu_type else "UNKNOWN"

        # FIX: Cast sla_deadline_ns to int safely
        sla_deadline_ns = metadata.get("sla_deadline_ns", 0)
        if isinstance(sla_deadline_ns, str):
            try:
                sla_deadline_ns = int(sla_deadline_ns)
            except (TypeError, ValueError):
                sla_deadline_ns = 0

        return JobRequest(
            job_id=job_id,
            tenant_id=tenant_id,
            trace_id=trace_id,
            payload=payload,
            requested_capabilities=capabilities,
            priority_hint=metadata.get("priority_hint", "STANDARD"),
            sla_deadline_ns=sla_deadline_ns,
            metadata={"created_from_work_unit": True, "timestamp_ns": timestamp_ns},
        )

    @staticmethod
    def _capabilities_for_type(wu_type: WorkUnitType | None) -> list[str]:
        """Default capability tags derived from work unit type."""
        if wu_type is None:
            return []
        mapping = {
            WorkUnitType.LLM_INFERENCE: ["llm", "gpu"],
            # Add other WorkUnitType mappings as needed
        }
        return mapping.get(wu_type, [])

    def _classification_to_job(
        self,
        classification: JobClassification,
        job_request: JobRequest,
        timestamp_ns: int,
    ) -> Job:
        """Build a Job from classification + original request."""

        # Resolve priority tier (guard against None)
        priority_hint = classification.priority_tier or ""
        priority_tier = self._resolve_priority_tier(priority_hint)

        # Build SLA from classification hints or defaults
        metadata = getattr(classification, "metadata", None)
        latency_target_ms = 1000
        if isinstance(metadata, dict):
            latency_target_ms = metadata.get("latency_target_ms", 1000)

        sla = SLA(
            latency_target_ms=latency_target_ms,
            class_=SLAClass.STANDARD,  # FIX: verify field name for SLA class
        )

        return Job(
            job_id=classification.job_id,
            tenant_id=job_request.tenant_id,
            trace_id=job_request.trace_id,
            priority_tier=priority_tier,
            sla=sla,
            classification=classification,
            status=JobStatus.PENDING,
            created_at_ns=timestamp_ns,
            metadata={
                "estimated_tokens": classification.estimated_tokens,
                "complexity": classification.complexity.value,
                "deterministic_required": classification.deterministic_required,
                "target_vertical": classification.target_vertical,
            },
        )

    @staticmethod
    def _resolve_priority_tier(hint: str) -> PriorityTier:
        """Convert string priority hint to PriorityTier enum."""
        if not isinstance(hint, str):
            return PriorityTier.MEDIUM
        mapping = {
            "CRITICAL": PriorityTier.CRITICAL,
            "HIGH": PriorityTier.HIGH,
            "MEDIUM": PriorityTier.MEDIUM,
            "LOW": PriorityTier.LOW,
        }
        return mapping.get(hint.upper(), PriorityTier.MEDIUM)