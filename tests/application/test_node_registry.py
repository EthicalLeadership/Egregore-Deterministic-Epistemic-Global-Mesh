"""Tests for NodeRegistry."""

from egregore.application.node_registry import InMemoryNodeStore, NodeRegistry
from egregore.domain.job_models import NodeCapability, NodeHeartbeat, ResourceProfile
from egregore.interface.job_router_ports import INodeRegistry


class TestInMemoryNodeStore:
    def test_upsert_and_get(self):
        store = InMemoryNodeStore()
        node = NodeCapability(node_id="n1", capabilities=["reasoning"])
        store.upsert(node)
        assert store.get("n1").node_id == "n1"

    def test_get_by_capability(self):
        store = InMemoryNodeStore()
        store.upsert(
            NodeCapability(node_id="n1", capabilities=["reasoning"], status="ACTIVE")
        )
        store.upsert(
            NodeCapability(node_id="n2", capabilities=["coding"], status="ACTIVE")
        )
        found = store.get_by_capability("reasoning")
        assert len(found) == 1
        assert found[0].node_id == "n1"

    def test_deprecate(self):
        store = InMemoryNodeStore()
        store.upsert(
            NodeCapability(node_id="n1", capabilities=["reasoning"], status="ACTIVE")
        )
        assert store.deprecate("n1") is True
        assert store.get("n1").status == "OFFLINE"


class TestNodeRegistry:
    def test_heartbeat_creates_node(self):
        store = InMemoryNodeStore()
        reg = NodeRegistry(store)
        pulse = NodeHeartbeat(
            node_id="n1",
            timestamp_ns=1000,
            load_metrics=ResourceProfile(),
            available_capabilities=["reasoning"],
        )
        reg.heartbeat(pulse)
        node = reg.get_node("n1")
        assert node is not None
        assert node.status == "ACTIVE"

    def test_get_available_by_capability(self):
        store = InMemoryNodeStore()
        reg = NodeRegistry(store)
        reg.heartbeat(
            NodeHeartbeat(
                node_id="n1",
                timestamp_ns=1000,
                load_metrics=ResourceProfile(),
                available_capabilities=["reasoning", "coding"],
            )
        )
        reg.heartbeat(
            NodeHeartbeat(
                node_id="n2",
                timestamp_ns=1000,
                load_metrics=ResourceProfile(),
                available_capabilities=["reasoning"],
            )
        )
        available = reg.get_available(["reasoning", "coding"])
        assert len(available) == 1
        assert available[0].node_id == "n1"

    def test_deprecate_stale(self):
        store = InMemoryNodeStore()
        reg = NodeRegistry(store)
        reg.heartbeat(
            NodeHeartbeat(
                node_id="n1",
                timestamp_ns=1000,
                load_metrics=ResourceProfile(),
                available_capabilities=["reasoning"],
            )
        )
        deprecated = reg.deprecate_stale(cutoff_ticks=2000)
        assert "n1" in deprecated
        assert reg.get_node("n1").status == "OFFLINE"

    def test_trust_score_from_evidence(self):
        store = InMemoryNodeStore()
        reg = NodeRegistry(store)
        reg.record_evidence("n1", success=True, duration_ms=100)
        reg.record_evidence("n1", success=True, duration_ms=120)
        reg.record_evidence("n1", success=False, duration_ms=500)
        pulse = NodeHeartbeat(
            node_id="n1",
            timestamp_ns=1000,
            load_metrics=ResourceProfile(),
            available_capabilities=["reasoning"],
        )
        reg.heartbeat(pulse)
        node = reg.get_node("n1")
        assert node.trust_score > 0.5

    def test_implements_protocol(self):
        store = InMemoryNodeStore()
        reg = NodeRegistry(store)
        assert isinstance(reg, INodeRegistry)
