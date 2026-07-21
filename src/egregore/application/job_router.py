"""JobRouter - Crust agency: selects execution node for classified work."""

from dataclasses import dataclass
from typing import Any

from egregore.domain.job_models import (
    JobClassification,
    NodeCapability,
    RoutingDecision,
)
from egregore.domain.scheduler_models import Job
from egregore.interface.job_router_ports import IJobRouter, ILoadRegulator


@dataclass
class ScoredNode:
    node: NodeCapability
    score: float
    reason: str


class NodeSelector(IJobRouter):
    def __init__(
        self,
        load_regulator: ILoadRegulator | None = None,
        trust_store: Any = None,
    ):
        self._load_regulator = load_regulator
        self._trust_store = trust_store

    def route(self, job: Job, candidates: list[NodeCapability]) -> RoutingDecision:
        if not candidates:
            raise ValueError("No candidate nodes available")

        classification = job.classification
        if not isinstance(classification, JobClassification):
            raise ValueError("Job must have JobClassification")

        requested_caps = classification.requested_capabilities
        scored = self._score_candidates(candidates, classification)

        if not scored:
            raise ValueError(
                f"No node satisfies capabilities {classification.target_vertical} "
                f"and resources {requested_caps}"
            )

        primary = scored[0]
        fallback_chain = [s.node.node_id for s in scored[1:]]

        return RoutingDecision(
            job_id=job.job_id,
            node_id=primary.node.node_id,
            tenant_id=job.tenant_id,
            trace_id=job.trace_id,
            decision_reason=primary.reason,
            estimated_latency_ms=self._estimate_latency(primary.node),
            fallback_chain=fallback_chain,
        )

    def _score_candidates(
        self, candidates: list[NodeCapability], classification: JobClassification
    ) -> list[ScoredNode]:
        scored: list[ScoredNode] = []
        requested_caps = classification.requested_capabilities
        need = classification.resource_profile

        for node in candidates:
            if node.status != "ACTIVE":
                continue
            if self._trust_store is not None and self._trust_store.is_cooldown(
                node.node_id
            ):
                continue
            if not node.resource_profile.can_satisfy(need):
                continue

            cap_match = self._capability_match(node, requested_caps)
            trust = node.trust_score
            headroom = 1.0 if node.resource_profile.can_satisfy(need) else 0.0
            score = (trust * 0.5) + (cap_match * 0.3) + (headroom * 0.2)
            reason = f"trust={trust:.2f}, cap_match={cap_match:.2f}, headroom={headroom:.2f}, score={score:.4f}"
            scored.append(ScoredNode(node=node, score=score, reason=reason))

        scored.sort(key=lambda s: (-s.score, s.node.node_id))
        return scored

    @staticmethod
    def _capability_match(node: NodeCapability, requested: list[str]) -> float:
        if not requested:
            return 1.0
        available = set(node.capabilities)
        matched = sum(1 for cap in requested if cap in available)
        return matched / len(requested)

    @staticmethod
    def _estimate_latency(node: NodeCapability) -> int:
        cpu = node.resource_profile.cpu_percent
        base = 100
        penalty = int(cpu * 2)
        return base + penalty
