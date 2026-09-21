"""JobRouterSchedulerAdapter — bridges WorkTreeService._Scheduler to the distributed job layer.

Implements the _Scheduler protocol required by WorkTreeService:
    submit(work_unit: WorkUnit, timestamp_ns: int) -> bool

Internally:
    WorkUnit -> JobRequest -> JobClassification -> Job -> route -> schedule.

No guessed fields. No getattr fallbacks. No non-deterministic IDs.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from egregore.domain.job_models import (
    JobClassification,
    JobRequest,
    NodeCapability,
)
from egregore.domain.scheduler_models import Job, PriorityTier, SLA
from egregore.domain.work_unit import WorkUnit, WorkUnitType
from egregore.interface.job_router_ports import (
    IJobClassifier,
    INodeRegistry,
    IJobRouter,
    IScheduler,
)

logger = logging.getLogger(__name__)


class JobRouterSchedulerAdapter:
    """Adapts WorkTreeService._Scheduler to job routing + scheduling.

    Args:
        classifier: Maps JobRequest to JobClassification.
        node_registry: Provides candidate NodeCapability objects.
        job_router: Selects a node from candidates.
        scheduler: Queues the routed Job.
        default_tenant_id: Fallback when WorkUnit.metadata lacks tenant_id.
        default_trace_id: Fallback when WorkUnit.metadata lacks trace_id.
    """

    def __init__(
        self,
        classifier: IJobClassifier,
        node_registry: INodeRegistry,
        job_router: IJobRouter,
        scheduler: IScheduler,
        default_tenant_id: str = "default",
        default_trace_id: str = "default",
    ) -> None:
        self._classifier = classifier
        self._node_registry = node_registry
        self._job_router = job_router
        self._scheduler = scheduler
        self._default_tenant_id = default_tenant_id
        self._default_trace_id = default_trace_id

    def submit(self, work_unit: WorkUnit, timestamp_ns: int) -> bool:
        """Route a WorkUnit leaf through the job layer.

        Returns True if the job was successfully classified, routed, and queued.
        Returns False otherwise.
        """
        try:
            return self._submit(work_unit, timestamp_ns)
        except Exception:
            logger.exception(
                "Adapter submit failed for work_unit_id=%s type=%s",
                work_unit.work_unit_id,
                work_unit.work_unit_type.name,
            )
            return False

    # ------------------------------------------------------------------
    # Internal flow
    # ------------------------------------------------------------------

    def _submit(self, work_unit: WorkUnit, timestamp_ns: int) -> bool:
        # 1. Convert WorkUnit -> JobRequest
        job_request = self._work_unit_to_job_request(work_unit)

        # 2. Classify
        classification = self._classifier.classify(job_request)

        # 3. Convert JobClassification -> Job
        job = self._classification_to_job(
            classification, job_request, timestamp_ns
        )

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
        try:
            decision = self._job_router.route(job, candidates)
        except Exception:
            logger.exception("Routing failed job_id=%s", job.job_id)
            return False

        if not decision or not decision.node_id:
            logger.warning(
                "Routing returned no node for job_id=%s", job.job_id
            )
            return False

        # 6. Assign node and submit to scheduler
        routed_job = replace(job, assigned_node_id=decision.node_id)
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

    def _work_unit_to_job_request(self, work_unit: WorkUnit) -> JobRequest:
        """Build a JobRequest from a WorkUnit using only real fields."""
        metadata = work_unit.metadata

        tenant_id = str(metadata.get("tenant_id", self._default_tenant_id))
        trace_id = str(metadata.get("trace_id", self._default_trace_id))

        capabilities = self._capabilities_for_type(work_unit.work_unit_type)
        explicit_caps = metadata.get("requested_capabilities", [])
        if isinstance(explicit_caps, list):
            capabilities = list(dict.fromkeys(capabilities + explicit_caps))

        priority_hint = str(metadata.get("priority_hint", "STANDARD"))

        # Use int safely; metadata may contain strings from external sources.
        sla_deadline_ns = metadata.get("sla_deadline_ns", 0)
        if isinstance(sla_deadline_ns, str):
            try:
                sla_deadline_ns = int(sla_deadline_ns)
            except (TypeError, ValueError):
                sla_deadline_ns = 0

        # Payload for JobRequest is a dict; use metadata plus a marker.
        payload: dict[str, Any] = {
            "_work_unit_id": work_unit.work_unit_id,
            "_work_unit_type": work_unit.work_unit_type.name,
            **metadata,
        }

        return JobRequest(
            job_id=work_unit.work_unit_id,
            tenant_id=tenant_id,
            trace_id=trace_id,
            payload=payload,
            requested_capabilities=capabilities,
            priority_hint=priority_hint,
            sla_deadline_ns=sla_deadline_ns,
            metadata={
                "work_unit_id": work_unit.work_unit_id,
                "work_unit_type": work_unit.work_unit_type.name,
                "timestamp_ns": work_unit.metadata.get("timestamp_ns", 0),
            },
        )

    @staticmethod
    def _capabilities_for_type(wu_type: WorkUnitType) -> list[str]:
        """Map WorkUnitType to default capability tags."""
        mapping: dict[WorkUnitType, list[str]] = {
            WorkUnitType.LLM_INFERENCE: ["llm", "gpu"],
            WorkUnitType.TENSOR_OPERATION: ["tensor", "gpu"],
            WorkUnitType.FEATURE_ENGINEERING: ["python", "data"],
            WorkUnitType.IMAP_INGESTION: ["imap", "ingestion"],
            WorkUnitType.DATABASE_QUERY: ["database", "query"],
            WorkUnitType.FILE_SYSTEM_SCAN: ["filesystem", "scan"],
            WorkUnitType.HYBRID_AI_AGENT: ["llm", "agent"],
            WorkUnitType.DATA_TURBINE_STREAM: ["stream", "data"],
            WorkUnitType.GOVERNANCE_AUDIT: ["governance", "audit"],
            WorkUnitType.PROVENANCE_COMPACTION: ["provenance", "compaction"],
        }
        return mapping.get(wu_type, [])

    def _classification_to_job(
        self,
        classification: JobClassification,
        job_request: JobRequest,
        timestamp_ns: int,
    ) -> Job:
        """Build a Job from classification + original request."""
        priority_tier = self._resolve_priority_tier(
            classification.priority_tier or job_request.priority_hint
        )

        # SLA: default for now; can be enriched later from metadata.
        sla = SLA()

        return Job(
            job_id=classification.job_id,
            tenant_id=job_request.tenant_id,
            trace_id=job_request.trace_id,
            priority_tier=priority_tier,
            sla=sla,
            classification=classification,
            status="PENDING",
            created_at_ns=timestamp_ns,
            metadata={
                "estimated_tokens": classification.estimated_tokens,
                "complexity": classification.complexity.value,
                "target_vertical": classification.target_vertical,
                "deterministic_required": classification.deterministic_required,
            },
        )

    @staticmethod
    def _resolve_priority_tier(hint: str) -> PriorityTier:
        """Convert a string priority hint to PriorityTier enum."""
        mapping = {
            "CRITICAL": PriorityTier.CRITICAL,
            "HIGH": PriorityTier.HIGH,
            "MEDIUM": PriorityTier.MEDIUM,
            "LOW": PriorityTier.LOW,
        }
        return mapping.get(hint.upper(), PriorityTier.MEDIUM)
