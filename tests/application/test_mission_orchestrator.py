"""Tests for MissionOrchestrator with stub protocol implementations."""

from __future__ import annotations

import pytest

from egregore.application.mission_orchestrator import MissionOrchestrator
from egregore.application.mission_ports import (
    CapacityLease,
    ClassificationError,
    NoNodeAvailable,
)
from egregore.domain.job_models import (
    ComplexityTier,
    JobClassification,
    JobRequest,
    JobStatus,
    ResourceProfile,
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubClassifier:
    """Always returns a STANDARD classification."""

    def classify(self, request: JobRequest) -> JobClassification:
        return JobClassification(
            job_id=request.job_id,
            complexity=ComplexityTier.STANDARD,
            resource_profile=ResourceProfile(),
            estimated_tokens=10,
            requested_capabilities=request.requested_capabilities,
            priority_tier=request.priority_hint,
        )


class FailingClassifier:
    """Raises during classification."""

    def classify(self, request: JobRequest) -> JobClassification:
        raise RuntimeError("boom-classify")


class NoneClassifier:
    """Returns None from classification."""

    def classify(self, request: JobRequest) -> JobClassification | None:
        return None


class StubWorkTree:
    """Returns two leaf requests."""

    def __init__(self) -> None:
        self.submitted_classifications: list[JobClassification] = []

    def submit_tree(self, classification: JobClassification) -> list[JobRequest]:
        self.submitted_classifications.append(classification)
        return [
            JobRequest(
                job_id=f"{classification.job_id}:leaf1",
                tenant_id="tenant-1",
                trace_id="trace-1",
                payload={},
                requested_capabilities=["llm", "gpu"],
            ),
            JobRequest(
                job_id=f"{classification.job_id}:leaf2",
                tenant_id="tenant-1",
                trace_id="trace-1",
                payload={},
            ),
        ]


class EmptyWorkTree:
    """Returns no leaf requests."""

    def submit_tree(self, classification: JobClassification) -> list[JobRequest]:
        return []


class StubCapacity:
    """Admits everything; tracks releases."""

    def __init__(self) -> None:
        self.released: list[str] = []

    def admit(self, root_job_id: str, estimated_units: int) -> CapacityLease:
        return CapacityLease(
            lease_id=f"lease-{root_job_id}",
            root_job_id=root_job_id,
            total_units=estimated_units,
            expires_at_ns=999_999_999_999,
        )

    def release(self, lease_id: str) -> None:
        self.released.append(lease_id)

    def extend(self, lease_id: str, extra_units: int) -> CapacityLease:
        return CapacityLease(
            lease_id=lease_id,
            root_job_id="root",
            total_units=extra_units,
            expires_at_ns=0,
        )


class DenyingCapacity:
    """Denies all admission."""

    def admit(self, root_job_id: str, estimated_units: int) -> CapacityLease | None:
        return None

    def release(self, lease_id: str) -> None:
        return None

    def extend(self, lease_id: str, extra_units: int) -> CapacityLease:
        raise AssertionError("extend must not be called when admission denied")


class StubRouter:
    """Routes every leaf to node-1."""

    def __init__(self) -> None:
        self.calls: list[JobRequest] = []

    def route_with_fallback(self, request: JobRequest) -> str:
        self.calls.append(request)
        return "node-1"


class RetryThenSuccessRouter:
    """Fails with NoNodeAvailable twice per leaf, then succeeds."""

    def __init__(self) -> None:
        self.attempts: list[JobRequest] = []
        self._failures: dict[str, int] = {}

    def route_with_fallback(self, request: JobRequest) -> str:
        self.attempts.append(request)
        self._failures[request.job_id] = self._failures.get(request.job_id, 0) + 1
        if self._failures[request.job_id] <= 2:
            raise NoNodeAvailable("no capable node yet")
        return "node-7"


class AlwaysNoNodeRouter:
    """Always raises NoNodeAvailable."""

    def __init__(self) -> None:
        self.attempts: list[JobRequest] = []

    def route_with_fallback(self, request: JobRequest) -> str:
        self.attempts.append(request)
        raise NoNodeAvailable("no capable node")


class RecordingPublisher:
    """Captures published events."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


def _build_orchestrator(
    *,
    classifier=None,
    work_tree=None,
    capacity=None,
    router=None,
    max_route_attempts: int = 3,
    route_backoff_base_seconds: float = 0.01,
    event_publisher=None,
) -> MissionOrchestrator:
    return MissionOrchestrator(
        classifier=classifier or StubClassifier(),
        work_tree=work_tree or StubWorkTree(),
        capacity=capacity or StubCapacity(),
        router=router or StubRouter(),
        max_concurrent_routing=2,
        max_route_attempts=max_route_attempts,
        route_backoff_base_seconds=route_backoff_base_seconds,
        event_publisher=event_publisher,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_routes_all_leaves() -> None:
    router = StubRouter()
    capacity = StubCapacity()
    orchestrator = _build_orchestrator(router=router, capacity=capacity)

    status = await orchestrator.execute_mission(
        intent="deploy",
        tenant_id="tenant-1",
        trace_id="trace-1",
    )

    assert status.admitted is True
    assert status.error_code is None
    assert len(status.leaf_statuses) == 2
    assert all(leaf.status == JobStatus.ROUTED for leaf in status.leaf_statuses)
    assert all(leaf.node_id == "node-1" for leaf in status.leaf_statuses)
    assert {leaf.job_id for leaf in status.leaf_statuses} == {
        "mission:trace-1:tenant-1:root:leaf1",
        "mission:trace-1:tenant-1:root:leaf2",
    }
    assert capacity.released == [status.capacity_lease_id]
    assert status.complexity == ComplexityTier.STANDARD
    assert status.model_policy == "local:default-gguf"
    assert status.total_units == 10  # classification.estimated_tokens


@pytest.mark.asyncio
async def test_capacity_denied_is_non_fatal() -> None:
    orchestrator = _build_orchestrator(capacity=DenyingCapacity())

    status = await orchestrator.execute_mission(
        intent="deploy",
        tenant_id="tenant-1",
        trace_id="trace-1",
    )

    assert status.admitted is False
    assert status.error_code == "CAPACITY_DENIED"
    assert status.capacity_lease_id is None
    assert status.leaf_statuses == ()
    assert "Capacity admission denied" in status.notes


@pytest.mark.asyncio
async def test_retries_then_pending_training() -> None:
    router = AlwaysNoNodeRouter()
    orchestrator = _build_orchestrator(router=router, max_route_attempts=3)

    status = await orchestrator.execute_mission(
        intent="deploy",
        tenant_id="tenant-1",
        trace_id="trace-1",
    )

    assert len(status.leaf_statuses) == 2
    assert all(leaf.status == JobStatus.PENDING_TRAINING for leaf in status.leaf_statuses)
    assert all(leaf.attempts == 3 for leaf in status.leaf_statuses)
    assert len(router.attempts) == 6  # 2 leaves * 3 attempts


@pytest.mark.asyncio
async def test_retry_after_transient_no_node() -> None:
    router = RetryThenSuccessRouter()
    orchestrator = _build_orchestrator(router=router)

    status = await orchestrator.execute_mission(
        intent="deploy",
        tenant_id="tenant-1",
        trace_id="trace-1",
    )

    assert all(leaf.status == JobStatus.ROUTED for leaf in status.leaf_statuses)
    assert all(leaf.attempts == 3 for leaf in status.leaf_statuses)


@pytest.mark.asyncio
async def test_no_leaves_records_note() -> None:
    orchestrator = _build_orchestrator(work_tree=EmptyWorkTree())

    status = await orchestrator.execute_mission(
        intent="deploy",
        tenant_id="tenant-1",
        trace_id="trace-1",
    )

    assert status.admitted is True
    assert status.leaf_statuses == ()
    assert "No leaf work units" in status.notes[0]


@pytest.mark.asyncio
async def test_idempotency_returns_cached_status() -> None:
    router = StubRouter()
    orchestrator = _build_orchestrator(router=router)

    first = await orchestrator.execute_mission(
        intent="deploy",
        tenant_id="tenant-1",
        trace_id="trace-1",
        idempotency_key="key-1",
    )
    call_count_after_first = len(router.calls)

    second = await orchestrator.execute_mission(
        intent="deploy",
        tenant_id="tenant-1",
        trace_id="trace-1",
        idempotency_key="key-1",
    )

    assert second is first
    assert len(router.calls) == call_count_after_first


@pytest.mark.asyncio
async def test_input_validation() -> None:
    orchestrator = _build_orchestrator()

    with pytest.raises(ValueError, match="intent"):
        await orchestrator.execute_mission(
            intent="   ",
            tenant_id="tenant-1",
            trace_id="trace-1",
        )
    with pytest.raises(ValueError, match="tenant_id"):
        await orchestrator.execute_mission(
            intent="deploy",
            tenant_id="",
            trace_id="trace-1",
        )
    with pytest.raises(ValueError, match="trace_id"):
        await orchestrator.execute_mission(
            intent="deploy",
            tenant_id="tenant-1",
            trace_id=" ",
        )


@pytest.mark.asyncio
async def test_classification_failure_raises() -> None:
    orchestrator = _build_orchestrator(classifier=FailingClassifier())

    with pytest.raises(ClassificationError):
        await orchestrator.execute_mission(
            intent="deploy",
            tenant_id="tenant-1",
            trace_id="trace-1",
        )


@pytest.mark.asyncio
async def test_classification_none_raises() -> None:
    orchestrator = _build_orchestrator(classifier=NoneClassifier())

    with pytest.raises(ClassificationError):
        await orchestrator.execute_mission(
            intent="deploy",
            tenant_id="tenant-1",
            trace_id="trace-1",
        )


@pytest.mark.asyncio
async def test_events_published() -> None:
    publisher = RecordingPublisher()
    orchestrator = _build_orchestrator(event_publisher=publisher)

    await orchestrator.execute_mission(
        intent="deploy",
        tenant_id="tenant-1",
        trace_id="trace-1",
    )

    event_types = [event_type for event_type, _ in publisher.events]
    assert event_types == [
        "MISSION_STARTED",
        "CAPACITY_ADMITTED",
        "MISSION_COMPLETED",
    ]
    completed = publisher.events[-1][1]
    assert completed["leaf_count"] == 2
    assert completed["routed_count"] == 2
