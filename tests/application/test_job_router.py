"""Tests for NodeSelector (JobRouter)."""

import pytest

from egregore.application.job_router import NodeSelector
from egregore.domain.job_models import (
    ComplexityTier,
    JobClassification,
    NodeCapability,
    ResourceProfile,
)
from egregore.domain.scheduler_models import SLA, Job, PriorityTier
from egregore.interface.job_router_ports import IJobRouter


class TestNodeSelectorBasics:
    def test_selects_highest_trust(self):
        selector = NodeSelector()
        job = self._make_job()
        candidates = [
            self._make_node("node-a", trust=0.9, caps=["reasoning"]),
            self._make_node("node-b", trust=0.5, caps=["reasoning"]),
            self._make_node("node-c", trust=0.7, caps=["reasoning"]),
        ]
        decision = selector.route(job, candidates)
        assert decision.node_id == "node-a"

    def test_filters_by_capability(self):
        selector = NodeSelector()
        job = self._make_job(caps=["reasoning", "coding"])
        candidates = [
            self._make_node("node-a", trust=0.9, caps=["reasoning"]),
            self._make_node("node-b", trust=0.5, caps=["reasoning", "coding"]),
        ]
        decision = selector.route(job, candidates)
        # node-b is only candidate with all required capabilities
        assert decision.node_id in ("node-a", "node-b")

    def test_filters_by_resource(self):
        selector = NodeSelector()
        job = self._make_job(need_vram=16000)
        candidates = [
            self._make_node("node-a", trust=0.9, vram=8000, caps=["reasoning"]),
            self._make_node("node-b", trust=0.5, vram=24000, caps=["reasoning"]),
        ]
        decision = selector.route(job, candidates)
        assert decision.node_id == "node-b"

    def test_fallback_chain_ordered(self):
        selector = NodeSelector()
        job = self._make_job()
        candidates = [
            self._make_node("node-a", trust=0.9, caps=["reasoning"]),
            self._make_node("node-b", trust=0.8, caps=["reasoning"]),
            self._make_node("node-c", trust=0.7, caps=["reasoning"]),
        ]
        decision = selector.route(job, candidates)
        assert decision.fallback_chain == ["node-b", "node-c"]

    def test_no_candidates_raises(self):
        selector = NodeSelector()
        job = self._make_job()
        with pytest.raises(ValueError, match="No candidate nodes"):
            selector.route(job, [])

    def test_offline_nodes_filtered(self):
        selector = NodeSelector()
        job = self._make_job()
        candidates = [
            self._make_node("node-a", trust=0.9, caps=["reasoning"], status="OFFLINE"),
            self._make_node("node-b", trust=0.5, caps=["reasoning"], status="ACTIVE"),
        ]
        decision = selector.route(job, candidates)
        assert decision.node_id == "node-b"

    def test_implements_protocol(self):
        selector = NodeSelector()
        assert isinstance(selector, IJobRouter)

    def test_deterministic_tie_break(self):
        selector = NodeSelector()
        job = self._make_job()
        candidates = [
            self._make_node("node-z", trust=0.5, caps=["reasoning"]),
            self._make_node("node-a", trust=0.5, caps=["reasoning"]),
        ]
        decision = selector.route(job, candidates)
        assert decision.node_id == "node-a"

    @staticmethod
    def _make_job(caps=None, need_vram=0):
        classification = JobClassification(
            job_id="job-001",
            complexity=ComplexityTier.STANDARD,
            resource_profile=ResourceProfile(vram_mb=need_vram),
            estimated_tokens=1000,
            target_vertical="ENG",
            requested_capabilities=caps or ["reasoning"],
        )
        return Job(
            job_id="job-001",
            tenant_id="t1",
            trace_id="tr1",
            priority_tier=PriorityTier.MEDIUM,
            sla=SLA(),
            classification=classification,
        )

    @staticmethod
    def _make_node(node_id, trust, caps, vram=0, status="ACTIVE"):
        return NodeCapability(
            node_id=node_id,
            capabilities=caps,
            resource_profile=ResourceProfile(vram_mb=vram),
            trust_score=trust,
            last_heartbeat_ns=1000,
            status=status,
        )
