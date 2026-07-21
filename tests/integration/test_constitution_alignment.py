"""
Integration tests for Pioneer 2 / Constitution alignment.

Verifies treaty ratification, node admission, entropy exchange, and escalation
logging survive a simulated process restart.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from egregore.application.entropy_exchange import EntropyExchange
from egregore.application.escalation_service import EscalationService
from egregore.application.federation_treaty import (
    RedisTreatyStore,
    TreatyLedger,
)
from egregore.application.node_registry import NodeRegistry
from egregore.domain.federation_constitution import (
    EntropySignal,
    load_constitution,
)
from egregore.domain.job_models import NodeHeartbeat, ResourceProfile
from egregore.domain.units import DT, TU
from egregore.infrastructure.distributed.cluster_aggregator import (
    ClusterAggregator,
    NodeCapacity,
)
from egregore.infrastructure.inter_node_messenger import InterNodeMessenger
from egregore.infrastructure.redis_store import (
    NodeTrustStore,
    RedisNodeStore,
    redis_client_from_env,
)
from egregore.infrastructure.zarc_provenance_sink import ZarcProvenanceSink
from egregore.kernel.provenance import Provenance

pytestmark = pytest.mark.integration


def _default_constitution_yaml() -> str:
    path = (
        Path(__file__).resolve().parents[2] / "config" / "egregore_constitution.yaml"
    )
    return path.read_text(encoding="utf-8")


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


@pytest.fixture(autouse=True)
def _flush_redis(redis_client):
    redis_client.flushdb()
    yield


@pytest.fixture
def provenance_sink():
    key = "a" * 64
    with tempfile.TemporaryDirectory() as tmp:
        prov = Provenance(Path(tmp) / "federation.zarc", signing_key_hex=key)
        yield ZarcProvenanceSink(provenance=prov)


def _fresh_node_store(redis_client):
    return RedisNodeStore(redis_client), NodeTrustStore(redis_client)


def _fresh_treaty_store(redis_client):
    return RedisTreatyStore(redis_client)


class _InMemoryProvenanceSink:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event)


def test_constitution_loads():
    constitution = load_constitution(_default_constitution_yaml())
    assert constitution.version == "1.0.0"
    assert constitution.article("II") is not None
    assert "honor_federation_treaties" in constitution.required_clauses()


def test_treaty_ratification_persists(redis_client, provenance_sink):
    constitution = load_constitution(_default_constitution_yaml())
    store = _fresh_treaty_store(redis_client)
    ledger = TreatyLedger(constitution, store, provenance_sink)

    treaty = ledger.propose(
        treaty_id="pioneer-federation-001",
        parties=["pioneer1", "pioneer2"],
        clauses=list(constitution.required_clauses()),
    )
    assert treaty.state.value == "PROPOSED"

    ledger.ratify(treaty.treaty_id, "pioneer1", "sig-pioneer1")
    # Not active yet; missing pioneer2 signature.
    assert ledger.active_treaty() is None

    ledger.ratify(treaty.treaty_id, "pioneer2", "sig-pioneer2")
    active = ledger.active_treaty()
    assert active is not None
    assert active.state.value == "ACTIVE"
    assert "pioneer1" in active.signatures
    assert "pioneer2" in active.signatures

    # Simulate restart with fresh store instances.
    store2 = _fresh_treaty_store(redis_client)
    ledger2 = TreatyLedger(constitution, store2, None)
    assert ledger2.has_ratified("pioneer1", treaty.treaty_id)
    assert ledger2.has_ratified("pioneer2", treaty.treaty_id)


def test_node_registry_enforces_treaty(redis_client, provenance_sink):
    constitution = load_constitution(_default_constitution_yaml())
    treaty_store = _fresh_treaty_store(redis_client)
    treaty_ledger = TreatyLedger(constitution, treaty_store, provenance_sink)
    treaty_ledger.propose(
        treaty_id="pioneer-federation-001",
        parties=["pioneer1", "pioneer2"],
        clauses=list(constitution.required_clauses()),
    )
    treaty_ledger.ratify("pioneer-federation-001", "pioneer1", "sig-pioneer1")
    treaty_ledger.ratify("pioneer-federation-001", "pioneer2", "sig-pioneer2")

    node_store, trust_store = _fresh_node_store(redis_client)
    registry = NodeRegistry(
        node_store, trust_store=trust_store, treaty_ledger=treaty_ledger
    )

    pioneer1 = NodeHeartbeat(
        node_id="pioneer1",
        timestamp_ns=1_000_000,
        load_metrics=ResourceProfile(cpu_percent=10, memory_mb=1024),
        available_capabilities=["cpu"],
    )
    pioneer2 = NodeHeartbeat(
        node_id="pioneer2",
        timestamp_ns=1_000_000,
        load_metrics=ResourceProfile(cpu_percent=10, memory_mb=1024),
        available_capabilities=["cpu"],
    )
    registry.heartbeat(pioneer1)
    registry.heartbeat(pioneer2)

    available = registry.get_available(["cpu"])
    assert {n.node_id for n in available} == {"pioneer1", "pioneer2"}

    # Restart simulation.
    node_store2, _ = _fresh_node_store(redis_client)
    registry2 = NodeRegistry(node_store2, treaty_ledger=treaty_ledger)
    available2 = registry2.get_available(["cpu"])
    assert {n.node_id for n in available2} == {"pioneer1", "pioneer2"}


def test_cluster_aggregator_blocks_unratified_nodes(redis_client, provenance_sink):
    constitution = load_constitution(_default_constitution_yaml())
    treaty_store = _fresh_treaty_store(redis_client)
    ledger = TreatyLedger(constitution, treaty_store, provenance_sink)
    ledger.propose(
        treaty_id="agg-treaty",
        parties=["pioneer1"],
        clauses=list(constitution.required_clauses()),
    )
    ledger.ratify("agg-treaty", "pioneer1", "sig1")

    aggregator = ClusterAggregator(node_store=redis_client, treaty_ledger=ledger)
    ratified = NodeCapacity(
        node_id="pioneer1",
        available_dt=DT(10.0),
        available_tu=TU(8),
        total_dt=DT(20.0),
        total_tu=TU(16),
        thermal_throttle=False,
        last_seen_ns=1_000_000,
    )
    aggregator.register_node(ratified)
    assert aggregator.node_count == 1

    with pytest.raises(ValueError, match="has not ratified"):
        aggregator.register_node(
            NodeCapacity(
                node_id="pioneer2",
                available_dt=DT(5.0),
                available_tu=TU(4),
                total_dt=DT(10.0),
                total_tu=TU(8),
                thermal_throttle=False,
                last_seen_ns=1_000_000,
            )
        )
    # Pioneer 2 should not be registered.
    assert aggregator.node_count == 1


def test_entropy_exchange_triggers_escalation(redis_client, provenance_sink):
    constitution = load_constitution(_default_constitution_yaml())
    sink = _InMemoryProvenanceSink()
    escalation = EscalationService(provenance_sink=sink)

    # Pioneer 1 exchange receives signals from Pioneer 2 via in-memory messenger.
    p1_messenger = InterNodeMessenger(node_id="pioneer1")

    def _local_publish(topic: str, payload: bytes) -> None:
        p1_messenger.handle_message(topic, payload)

    p1_messenger.register_publish(_local_publish)
    p1_exchange = EntropyExchange(
        node_id="pioneer1",
        constitution=constitution,
        escalation_service=escalation,
        messenger=p1_messenger,
    )

    # Broadcast a critical entropy signal from Pioneer 2.
    p2_signal = EntropySignal(
        source_node_id="pioneer2",
        signal_type="drift",
        value=0.95,
        confidence=1.0,
        timestamp_ns=2_000_000_000,
        signature="",
    )
    now_ns = __import__("time").time_ns()
    p2_signal = EntropySignal(
        source_node_id="pioneer2",
        signal_type="drift",
        value=0.95,
        confidence=1.0,
        timestamp_ns=now_ns,
        signature="",
    )
    payload = p2_signal.__dict__  # noqa: SLF001
    payload["source_node_id"] = p2_signal.source_node_id
    p1_messenger.handle_message(
        EntropyExchange.TOPIC,
        __import__("json").dumps(payload, sort_keys=True).encode("utf-8"),
    )

    # Pioneer 1 also publishes its own signal so min_participating_nodes is met.
    p1_exchange.publish("drift", 0.90)

    escalations = [e for e in sink.events if e.event == "escalation_opened"]
    assert len(escalations) >= 1
    assert escalations[0].payload["level"] == "CRITICAL"
