"""
Integration test: orchestration suite state survives a process restart when
Redis is available.
"""

from __future__ import annotations

import uuid

import pytest

from egregore.application.admission_controller import (
    AdmissionController,
    CapacityBudget,
)
from egregore.application.job_router import NodeSelector
from egregore.application.node_registry import NodeRegistry
from egregore.application.scheduler import JobScheduler
from egregore.domain.job_models import (
    JobClassification,
    NodeHeartbeat,
    ResourceProfile,
)
from egregore.domain.scheduler_models import SLA, Job, PriorityTier
from egregore.domain.units import DT, TU
from egregore.domain.work_unit import (
    WorkUnit,
    WorkUnitDemand,
    WorkUnitRegistry,
    WorkUnitType,
)
from egregore.infrastructure.redis_store import (
    NodeTrustStore,
    RedisAdmissionBacklog,
    RedisJobStore,
    RedisNodeStore,
    redis_client_from_env,
)

pytestmark = pytest.mark.integration


def _redis_client():
    try:
        client = redis_client_from_env()
        client.ping()
        return client
    except Exception:
        return None


@pytest.fixture(scope="module")
def redis_client():
    client = _redis_client()
    if client is None:
        pytest.skip("Redis is not available")
    client.flushdb()
    yield client
    client.flushdb()


def _fresh_stores(redis_client):
    """Simulate a brand-new process by creating new store instances."""
    return (
        RedisNodeStore(redis_client),
        NodeTrustStore(redis_client),
        RedisJobStore(redis_client),
        RedisAdmissionBacklog(redis_client),
    )


def test_node_registry_survives_restart(redis_client):
    node_store, trust_store, _, _ = _fresh_stores(redis_client)
    registry = NodeRegistry(node_store, trust_store=trust_store)

    pulse = NodeHeartbeat(
        node_id="pioneer1",
        timestamp_ns=1_000_000,
        load_metrics=ResourceProfile(cpu_percent=10, memory_mb=1024),
        available_capabilities=["cpu", "memory"],
    )
    registry.heartbeat(pulse)
    registry.record_evidence("pioneer1", success=True, duration_ms=45)

    # Simulate process restart: fresh NodeRegistry with fresh stores.
    node_store2, trust_store2, _, _ = _fresh_stores(redis_client)
    registry2 = NodeRegistry(node_store2, trust_store=trust_store2)

    node = registry2.get_node("pioneer1")
    assert node is not None
    assert node.status == "ACTIVE"
    assert node.trust_score > 0.5


def test_node_selector_skips_cooled_down_nodes(redis_client):
    node_store, trust_store, _, _ = _fresh_stores(redis_client)
    registry = NodeRegistry(node_store, trust_store=trust_store)
    selector = NodeSelector(trust_store=trust_store)

    pulse = NodeHeartbeat(
        node_id="pioneer1",
        timestamp_ns=1_000_000,
        load_metrics=ResourceProfile(cpu_percent=10, memory_mb=1024),
        available_capabilities=["cpu"],
    )
    registry.heartbeat(pulse)
    registry.cooldown_node("pioneer1", ttl_seconds=300)

    job = Job(
        job_id="j1",
        tenant_id="t1",
        trace_id="tr",
        priority_tier=PriorityTier.HIGH,
        sla=SLA(),
        classification=JobClassification(
            job_id="j1",
            complexity="STANDARD",
            resource_profile=ResourceProfile(cpu_percent=0, memory_mb=0),
            requested_capabilities=["cpu"],
        ),
    )
    candidates = registry.get_available(["cpu"])
    # get_available still returns the node because it only filters by status.
    assert len(candidates) == 1
    # The selector must skip the cooling-down node.
    with pytest.raises(ValueError, match="No node satisfies capabilities"):
        selector.route(job, candidates)


def test_job_scheduler_survives_restart(redis_client):
    _, _, job_store, _ = _fresh_stores(redis_client)
    scheduler = JobScheduler(job_store)

    job = Job(
        job_id="j-restart",
        tenant_id="t1",
        trace_id="tr",
        priority_tier=PriorityTier.MEDIUM,
        sla=SLA(),
        classification={},
        created_at_ns=100,
    )
    assert scheduler.submit(job) is True

    # Restart.
    _, _, job_store2, _ = _fresh_stores(redis_client)
    scheduler2 = JobScheduler(job_store2)
    pending = scheduler2.drain_tenant(tick=1, tenant_id="t1", max_jobs=10)
    assert len(pending) == 1
    assert pending[0].job_id == "j-restart"


def test_admission_backlog_survives_restart(redis_client):
    WorkUnitRegistry.unlock()
    WorkUnitRegistry.clear()
    try:
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE,
            WorkUnitDemand(dt=DT(0.1), tu=TU(1)),
        )

        _, _, _, backlog_store = _fresh_stores(redis_client)
        budget = CapacityBudget(total_dt=DT(10.0), total_tu=TU(10))
        controller = AdmissionController(budget, backlog_store=backlog_store)

        wu = WorkUnit(
            work_unit_id=f"wu-{uuid.uuid4().hex[:8]}",
            work_unit_type=WorkUnitType.LLM_INFERENCE,
            demand=WorkUnitDemand(dt=DT(0.1), tu=TU(1)),
            payload=b"test",
        )
        result = controller.evaluate(wu)
        assert result.decision.name == "ADMITTED"

        # Restart.
        _, _, _, backlog_store2 = _fresh_stores(redis_client)
        controller2 = AdmissionController(budget, backlog_store=backlog_store2)
        restored = controller2._backlog_store.list_units()
        assert len(restored) == 1
        assert restored[0].work_unit_id == wu.work_unit_id
    finally:
        WorkUnitRegistry.clear()


def test_orchestration_fallback_without_redis(monkeypatch):
    """When Redis is unavailable, create_orchestration_stores returns None."""
    monkeypatch.setenv("REDIS_HOST", "invalid.invalid")
    monkeypatch.setenv("REDIS_PORT", "1")
    from egregore.infrastructure.redis_store import create_orchestration_stores

    stores = create_orchestration_stores()
    assert stores is None
