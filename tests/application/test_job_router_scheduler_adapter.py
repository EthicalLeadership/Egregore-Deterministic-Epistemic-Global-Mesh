"""Tests for JobRouterSchedulerAdapter."""

from dataclasses import replace

from egregore.application.job_router_scheduler_adapter import (
    JobRouterSchedulerAdapter,
)
from egregore.domain.job_models import (
    ComplexityTier,
    JobClassification,
    NodeCapability,
    ResourceProfile,
    RoutingDecision,
)
from egregore.domain.scheduler_models import Job, PriorityTier, SLA
from egregore.domain.units import DT, TU
from egregore.domain.work_unit import WorkUnit, WorkUnitDemand, WorkUnitType


class StubClassifier:
    def __init__(self, classification: JobClassification):
        self._classification = classification

    def classify(self, request):
        return self._classification


class StubNodeRegistry:
    def __init__(self, nodes: list[NodeCapability]):
        self._nodes = nodes

    def get_available(self, capabilities: list[str]):
        return self._nodes


class StubRouter:
    def __init__(self, decision: RoutingDecision | None = None, raise_error: bool = False):
        self.decision = decision
        self.raise_error = raise_error

    def route(self, job: Job, candidates: list[NodeCapability]):
        if self.raise_error:
            raise ValueError("boom")
        return self.decision


class StubScheduler:
    def __init__(self, accept: bool = True):
        self.accept = accept
        self.submitted_jobs: list[Job] = []

    def submit(self, job: Job) -> bool:
        if self.accept:
            self.submitted_jobs.append(job)
            return True
        return False


def _make_work_unit() -> WorkUnit:
    return WorkUnit(
        work_unit_id="wu-test-123",
        work_unit_type=WorkUnitType.LLM_INFERENCE,
        demand=WorkUnitDemand(dt=DT(1.0), tu=TU(2), priority=100, max_wait_ms=500),
        payload=b"",
        metadata={
            "tenant_id": "tenant-a",
            "trace_id": "trace-b",
            "requested_capabilities": ["gpu"],
            "priority_hint": "HIGH",
        },
    )


def _make_classification() -> JobClassification:
    return JobClassification(
        job_id="wu-test-123",
        complexity=ComplexityTier.STANDARD,
        resource_profile=ResourceProfile(vram_mb=4096, memory_mb=8192),
        estimated_tokens=100,
        target_vertical="llm",
        requested_capabilities=["llm", "gpu"],
        deterministic_required=False,
        priority_tier="HIGH",
        created_at_ns=1234,
    )


def _make_candidate() -> NodeCapability:
    return NodeCapability(
        node_id="node-1",
        capabilities=["llm", "gpu"],
        resource_profile=ResourceProfile(vram_mb=12288, memory_mb=32768),
        trust_score=0.9,
        last_heartbeat_ns=9999,
        status="ACTIVE",
        public_key_fingerprint="fp-1",
    )


def test_submit_success_route_and_schedule():
    work_unit = _make_work_unit()
    classification = _make_classification()
    candidate = _make_candidate()
    decision = RoutingDecision(
        job_id="wu-test-123",
        node_id="node-1",
        tenant_id="tenant-a",
        trace_id="trace-b",
        decision_reason="match",
    )

    classifier = StubClassifier(classification)
    registry = StubNodeRegistry([candidate])
    router = StubRouter(decision)
    scheduler = StubScheduler(accept=True)

    adapter = JobRouterSchedulerAdapter(
        classifier=classifier,
        node_registry=registry,
        job_router=router,
        scheduler=scheduler,
    )

    result = adapter.submit(work_unit, 123456)

    assert result is True
    assert len(scheduler.submitted_jobs) == 1
    submitted = scheduler.submitted_jobs[0]
    assert submitted.job_id == "wu-test-123"
    assert submitted.assigned_node_id == "node-1"
    assert submitted.priority_tier == PriorityTier.HIGH
    assert submitted.tenant_id == "tenant-a"


def test_submit_no_candidates_returns_false():
    work_unit = _make_work_unit()
    classification = _make_classification()
    classifier = StubClassifier(classification)
    registry = StubNodeRegistry([])
    router = StubRouter(None)
    scheduler = StubScheduler(accept=True)

    adapter = JobRouterSchedulerAdapter(classifier, registry, router, scheduler)

    result = adapter.submit(work_unit, 123456)

    assert result is False
    assert len(scheduler.submitted_jobs) == 0


def test_submit_router_empty_node_id_returns_false():
    work_unit = _make_work_unit()
    classification = _make_classification()
    candidate = _make_candidate()
    decision = RoutingDecision(
        job_id="wu-test-123",
        node_id="",
        tenant_id="tenant-a",
        trace_id="trace-b",
        decision_reason="no match",
    )
    classifier = StubClassifier(classification)
    registry = StubNodeRegistry([candidate])
    router = StubRouter(decision)
    scheduler = StubScheduler(accept=True)

    adapter = JobRouterSchedulerAdapter(classifier, registry, router, scheduler)

    result = adapter.submit(work_unit, 123456)

    assert result is False
    assert len(scheduler.submitted_jobs) == 0


def test_submit_scheduler_rejects_returns_false():
    work_unit = _make_work_unit()
    classification = _make_classification()
    candidate = _make_candidate()
    decision = RoutingDecision(
        job_id="wu-test-123",
        node_id="node-1",
        tenant_id="tenant-a",
        trace_id="trace-b",
        decision_reason="match",
    )
    classifier = StubClassifier(classification)
    registry = StubNodeRegistry([candidate])
    router = StubRouter(decision)
    scheduler = StubScheduler(accept=False)

    adapter = JobRouterSchedulerAdapter(classifier, registry, router, scheduler)

    result = adapter.submit(work_unit, 123456)

    assert result is False
    assert len(scheduler.submitted_jobs) == 0
