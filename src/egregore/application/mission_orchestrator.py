"""MissionOrchestrator - application service for /mission command.

Coordinates:
- IJobClassifier
- IWorkTreeDecomposer
- ICapacityOrchestrator (lease-based)
- IJobRouter

Does NOT call AgentRunner.run().
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from egregore.application.mission_ports import (
    CapacityDeniedError,
    CapacityLease,
    ClassificationError,
    IEventPublisher,
    IJobClassifier,
    IJobRouter,
    ICapacityOrchestrator,
    IWorkTreeDecomposer,
    MissionExecutionError,
    NoNodeAvailable,
    RoutingError,
    WorkTreeError,
)
from egregore.domain.job_models import (
    ComplexityTier,
    JobClassification,
    JobRequest,
    JobStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Complexity -> model policy
# ---------------------------------------------------------------------------


class ComplexityModelPolicy:
    """Maps scheduler complexity tier to chat model cascade tier.

    TRIVIAL   -> local Qwen-3B GGUF
    STANDARD  -> local larger GGUF / DeepSeek
    COMPLEX   -> remote DeepSeek
    SOVEREIGN -> remote Claude API
    """

    TIER_MODEL = {
        ComplexityTier.TRIVIAL: "local:qwen3b",
        ComplexityTier.STANDARD: "local:default-gguf",
        ComplexityTier.COMPLEX: "remote:deepseek",
        ComplexityTier.SOVEREIGN: "remote:claude",
    }

    @classmethod
    def for_tier(cls, tier: ComplexityTier) -> str:
        return cls.TIER_MODEL.get(tier, "local:default-gguf")


# ---------------------------------------------------------------------------
# Result objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LeafStatus:
    """Structured status for a single leaf work unit."""

    job_id: str
    status: JobStatus
    node_id: str = ""
    attempts: int = 1
    error: str | None = None


@dataclass(frozen=True, slots=True)
class MissionStatus:
    """Outcome of a mission orchestration attempt."""

    mission_id: str
    root_job_id: str
    complexity: ComplexityTier
    model_policy: str
    total_units: int
    admitted: bool
    capacity_lease_id: str | None
    leaf_statuses: tuple[LeafStatus, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)
    started_at_ns: int = 0
    ended_at_ns: int = 0
    error_code: str | None = None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class MissionOrchestrator:
    """Mission orchestration service.

    Classifies, decomposes, acquires capacity, and routes leaves.
    Supports idempotency, parallel routing with retries, and event emission.
    """

    def __init__(
        self,
        classifier: IJobClassifier,
        work_tree: IWorkTreeDecomposer,
        capacity: ICapacityOrchestrator,
        router: IJobRouter,
        *,
        max_concurrent_routing: int = 5,
        max_route_attempts: int = 3,
        route_backoff_base_seconds: float = 0.5,
        event_publisher: IEventPublisher | None = None,
    ) -> None:
        self.classifier = classifier
        self.work_tree = work_tree
        self.capacity = capacity
        self.router = router
        self.max_concurrent_routing = max_concurrent_routing
        self.max_route_attempts = max_route_attempts
        self.route_backoff_base_seconds = route_backoff_base_seconds
        self.event_publisher = event_publisher

        # In-memory idempotency cache (replace with persistent store in production)
        self._idempotency_cache: dict[str, MissionStatus] = {}

    async def execute_mission(
        self,
        intent: str,
        tenant_id: str,
        trace_id: str,
        requested_capabilities: tuple[str, ...] = (),
        idempotency_key: str | None = None,
    ) -> MissionStatus:
        """Orchestrate a mission from intent to leaf routing.

        Args:
            intent: User-provided mission description.
            tenant_id: Tenant identifier.
            trace_id: Distributed trace identifier.
            requested_capabilities: Optional capability filters.
            idempotency_key: If provided, prevents duplicate execution.

        Returns:
            MissionStatus describing the outcome.
        """
        self._validate_inputs(intent, tenant_id, trace_id)

        mission_id = f"mission:{trace_id}:{tenant_id}"
        root_job_id = f"{mission_id}:root"

        # Idempotency check
        if idempotency_key:
            key = f"{tenant_id}:{idempotency_key}"
            cached = self._idempotency_cache.get(key)
            if cached is not None:
                logger.info("Returning cached mission status for key=%s", key)
                return cached

        started_at_ns = time.time_ns()

        # 1. Create root JobRequest
        root_request = JobRequest(
            job_id=root_job_id,
            tenant_id=tenant_id,
            trace_id=trace_id,
            payload={"intent": intent},
            requested_capabilities=list(requested_capabilities),
            priority_hint="STANDARD",
        )

        self._publish_event(
            "MISSION_STARTED",
            {"mission_id": mission_id, "root_job_id": root_job_id},
        )

        # 2. Classify
        try:
            classification = await self._maybe_await(
                self.classifier.classify(root_request)
            )
        except Exception as exc:
            logger.exception(
                "Classification failed mission_id=%s root_job_id=%s",
                mission_id,
                root_job_id,
            )
            raise ClassificationError(
                f"Classification failed for mission {mission_id}"
            ) from exc

        if classification is None:
            raise ClassificationError(
                f"Classifier returned None for mission {mission_id}"
            )

        # 3. Decompose
        try:
            leaf_requests = await self._maybe_await(
                self.work_tree.submit_tree(classification)
            )
        except Exception as exc:
            logger.exception(
                "Work tree decomposition failed mission_id=%s root_job_id=%s",
                mission_id,
                root_job_id,
            )
            raise WorkTreeError(
                f"Work tree decomposition failed for mission {mission_id}"
            ) from exc

        if leaf_requests is None:
            leaf_requests = []

        # 4. Estimate capacity and acquire lease
        total_units = self._estimate_total_units(classification, leaf_requests)

        try:
            lease = await self._maybe_await(
                self.capacity.admit(root_job_id, total_units)
            )
        except Exception as exc:
            logger.exception(
                "Capacity admission failed mission_id=%s root_job_id=%s",
                mission_id,
                root_job_id,
            )
            raise CapacityDeniedError(
                f"Capacity admission failed for mission {mission_id}"
            ) from exc

        if lease is None:
            ended_at_ns = time.time_ns()
            status = MissionStatus(
                mission_id=mission_id,
                root_job_id=root_job_id,
                complexity=classification.complexity,
                model_policy=ComplexityModelPolicy.for_tier(
                    classification.complexity
                ),
                total_units=total_units,
                admitted=False,
                capacity_lease_id=None,
                notes=("Capacity admission denied",),
                started_at_ns=started_at_ns,
                ended_at_ns=ended_at_ns,
                error_code="CAPACITY_DENIED",
            )
            self._publish_event(
                "CAPACITY_DENIED",
                {"mission_id": mission_id, "total_units": total_units},
            )
            if idempotency_key:
                self._idempotency_cache[f"{tenant_id}:{idempotency_key}"] = status
            return status

        lease_id = lease.lease_id
        self._publish_event(
            "CAPACITY_ADMITTED",
            {
                "mission_id": mission_id,
                "lease_id": lease_id,
                "total_units": total_units,
            },
        )

        # 5. Route leaves in parallel with bounded concurrency
        leaf_statuses, notes = [], []
        if leaf_requests:
            semaphore = asyncio.Semaphore(self.max_concurrent_routing)

            async def route_one(leaf: JobRequest) -> LeafStatus:
                async with semaphore:
                    return await self._route_leaf(
                        leaf, classification, root_request
                    )

            results = await asyncio.gather(
                *[route_one(leaf) for leaf in leaf_requests],
                return_exceptions=True,
            )

            for i, result in enumerate(results):
                if isinstance(result, LeafStatus):
                    leaf_statuses.append(result)
                else:
                    # Unexpected exception from route_one
                    leaf = leaf_requests[i]
                    logger.exception(
                        "Unexpected leaf routing failure mission_id=%s leaf_id=%s",
                        mission_id,
                        leaf.job_id,
                    )
                    leaf_statuses.append(
                        LeafStatus(
                            job_id=leaf.job_id,
                            status=JobStatus.FAILED,
                            error=f"Unexpected failure: {result}",
                        )
                    )
                    notes.append(f"Leaf {leaf.job_id} failed unexpectedly")
        else:
            notes.append("No leaf work units produced by work tree decomposition")

        # 6. Release capacity lease after routing (placeholder for actual hold semantics)
        try:
            await self._maybe_await(self.capacity.release(lease_id))
        except Exception:
            logger.exception(
                "Capacity release failed mission_id=%s lease_id=%s",
                mission_id,
                lease_id,
            )

        ended_at_ns = time.time_ns()
        status = MissionStatus(
            mission_id=mission_id,
            root_job_id=root_job_id,
            complexity=classification.complexity,
            model_policy=ComplexityModelPolicy.for_tier(classification.complexity),
            total_units=total_units,
            admitted=True,
            capacity_lease_id=lease_id,
            leaf_statuses=tuple(leaf_statuses),
            notes=tuple(notes),
            started_at_ns=started_at_ns,
            ended_at_ns=ended_at_ns,
        )
        self._publish_event(
            "MISSION_COMPLETED",
            {
                "mission_id": mission_id,
                "leaf_count": len(leaf_statuses),
                "routed_count": sum(
                    1 for ls in leaf_statuses if ls.status == JobStatus.ROUTED
                ),
            },
        )

        if idempotency_key:
            self._idempotency_cache[f"{tenant_id}:{idempotency_key}"] = status

        return status

    # ------------------------------------------------------------------
    # Routing helper with retries
    # ------------------------------------------------------------------

    async def _route_leaf(
        self,
        leaf: JobRequest,
        root_classification: JobClassification,
        root_request: JobRequest,
    ) -> LeafStatus:
        """Route a single leaf with retries and backoff."""
        enriched_leaf = self._enrich_leaf(leaf, root_classification, root_request)

        for attempt in range(1, self.max_route_attempts + 1):
            try:
                node_id = await self._maybe_await(
                    self.router.route_with_fallback(enriched_leaf)
                )
                if not node_id:
                    raise NoNodeAvailable(
                        f"Router returned empty node_id for leaf {enriched_leaf.job_id}"
                    )
                return LeafStatus(
                    job_id=enriched_leaf.job_id,
                    status=JobStatus.ROUTED,
                    node_id=node_id,
                    attempts=attempt,
                )
            except NoNodeAvailable:
                if attempt == self.max_route_attempts:
                    logger.warning(
                        "No node available after %d attempts for leaf %s",
                        attempt,
                        enriched_leaf.job_id,
                    )
                    # Trigger curriculum load before returning PENDING_TRAINING
                    await self._trigger_curriculum_load(enriched_leaf)
                    return LeafStatus(
                        job_id=enriched_leaf.job_id,
                        status=JobStatus.PENDING_TRAINING,
                        attempts=attempt,
                    )
                await asyncio.sleep(
                    self.route_backoff_base_seconds * (2 ** (attempt - 1))
                )
            except Exception as exc:
                logger.exception(
                    "Leaf routing failed mission_id=%s leaf_id=%s",
                    root_request.job_id,
                    enriched_leaf.job_id,
                )
                raise RoutingError(
                    f"Leaf routing failed for {enriched_leaf.job_id}"
                ) from exc

        # Should never reach here
        return LeafStatus(
            job_id=enriched_leaf.job_id,
            status=JobStatus.FAILED,
            error="Routing failed after all attempts",
            attempts=self.max_route_attempts,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _estimate_total_units(
        self,
        classification: JobClassification,
        leaves: list[JobRequest],
    ) -> int:
        """Return aggregate capacity units for admission."""
        if classification.estimated_tokens > 0:
            return int(classification.estimated_tokens)

        total = sum(self._leaf_unit_estimate(leaf) for leaf in leaves)
        return total or len(leaves)

    @staticmethod
    def _leaf_unit_estimate(leaf: JobRequest) -> int:
        """Extract capacity units from a leaf JobRequest."""
        metadata = leaf.metadata
        for key in ("capacity_units", "estimated_units", "estimated_tokens"):
            value = metadata.get(key)
            if value is not None:
                try:
                    return max(0, int(value))
                except (TypeError, ValueError):
                    continue
        return 1

    def _enrich_leaf(
        self,
        leaf: JobRequest,
        root_classification: JobClassification,
        root_request: JobRequest,
    ) -> JobRequest:
        """Return a new JobRequest with inherited classification metadata."""
        model_policy = ComplexityModelPolicy.for_tier(
            root_classification.complexity
        )

        new_metadata = dict(leaf.metadata)
        new_metadata.setdefault(
            "complexity_tier", root_classification.complexity.value
        )
        new_metadata.setdefault("model_policy", model_policy)
        new_metadata.setdefault(
            "estimated_tokens", root_classification.estimated_tokens
        )

        leaf_caps = leaf.requested_capabilities
        if not leaf_caps:
            root_caps = root_request.requested_capabilities
            if root_caps:
                return dataclasses.replace(
                    leaf,
                    metadata=new_metadata,
                    requested_capabilities=list(root_caps),
                )

        return dataclasses.replace(leaf, metadata=new_metadata)

    async def _trigger_curriculum_load(self, leaf: JobRequest) -> None:
        """Trigger academy/curriculum load for a leaf pending training."""
        logger.info(
            "Leaf %s pending training; triggering curriculum load",
            leaf.job_id,
        )
        # TODO: Wire to AcademyService / CurriculumLoader when available.

    @staticmethod
    def _validate_inputs(intent: str, tenant_id: str, trace_id: str) -> None:
        if not intent or not intent.strip():
            raise ValueError("Mission intent must be a non-empty string")
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        if not trace_id or not trace_id.strip():
            raise ValueError("trace_id must be a non-empty string")

    def _publish_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_publisher:
            try:
                self.event_publisher.publish(event_type, payload)
            except Exception:
                logger.exception("Event publishing failed for %s", event_type)

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        """Await if the supplied value is awaitable, otherwise return it."""
        if inspect.isawaitable(value):
            return await value
        return value
