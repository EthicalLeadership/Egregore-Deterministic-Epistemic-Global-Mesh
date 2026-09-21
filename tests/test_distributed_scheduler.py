"""
EGREGORE LAW: Distributed Scheduler Test Matrix
"""

from __future__ import annotations

import pytest

from egregore.application.distributed_scheduler import DistributedScheduler
from egregore.application.placement_scorer import PlacementScorer
from egregore.domain.node_profile import NodeProfile
from egregore.domain.units import DT, TU
from egregore.domain.work_unit import (
    WorkUnit,
    WorkUnitDemand,
    WorkUnitRegistry,
    WorkUnitType,
)
from egregore.infrastructure.inter_node_messenger import (
    InterNodeMessenger,
    MessengerError,
)
from egregore.kernel.scheduler.epoch_scheduler import EpochConfig, EpochScheduler


class TestNodeProfile:
    def test_utilization_score_idle(self):
        p = NodeProfile("n1", 4, 16000, 10.0, 100, 5.0, 1000)
        assert p.utilization_score() == 0.0

    def test_utilization_score_saturated(self):
        p = NodeProfile(
            "n1", 4, 16000, 10.0, 100, 5.0, 1000, admitted_count=50, rejected_count=50
        )
        assert p.utilization_score() == 0.5

    def test_capacity_score(self):
        p = NodeProfile("n1", 4, 16000, 10.0, 100, 5.0, 1000)
        assert p.capacity_score() > 0


class TestPlacementScorer:
    def test_score_prefers_low_latency(self):
        scorer = PlacementScorer()
        nodes = [
            NodeProfile("near", 4, 8000, 10.0, 100, 5.0, 1000),
            NodeProfile("far", 4, 8000, 10.0, 100, 50.0, 1000),
        ]
        wu = WorkUnit(
            "wu-1", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )
        score = scorer.score(wu, nodes)
        assert score is not None
        assert score.node_id == "near"

    def test_score_filters_insufficient_dt(self):
        scorer = PlacementScorer()
        nodes = [
            NodeProfile("small", 2, 4000, 0.5, 10, 5.0, 1000),
        ]
        wu = WorkUnit(
            "wu-1", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )
        score = scorer.score(wu, nodes)
        assert score is None

    def test_score_filters_insufficient_tu(self):
        scorer = PlacementScorer()
        nodes = [
            NodeProfile("small", 2, 4000, 10.0, 1, 5.0, 1000),
        ]
        wu = WorkUnit(
            "wu-1", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )
        score = scorer.score(wu, nodes)
        assert score is None


class TestInterNodeMessenger:
    def test_register_and_publish(self):
        published = []

        def pub(topic, payload):
            published.append((topic, payload))

        msg = InterNodeMessenger("node-1")
        msg.register_publish(pub)

        profile = NodeProfile("node-1", 4, 16000, 10.0, 100, 5.0, 1000)
        msg.broadcast_profile(profile)
        assert len(published) == 1
        assert "node-1.profile" in published[0][0]

    def test_dispatch(self):
        published = []

        def pub(topic, payload):
            published.append((topic, payload))

        msg = InterNodeMessenger("node-1")
        msg.register_publish(pub)

        wu = WorkUnit(
            "wu-1", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )
        msg.dispatch_work_unit("node-2", wu)
        assert len(published) == 1
        assert "node-2.dispatch" in published[0][0]

    def test_no_publish_raises(self):
        msg = InterNodeMessenger("node-1")
        with pytest.raises(MessengerError):
            msg.broadcast_profile(NodeProfile("node-1", 4, 16000, 10.0, 100, 5.0, 1000))

    def test_heartbeat(self):
        published = []

        def pub(topic, payload):
            published.append((topic, payload))

        msg = InterNodeMessenger("node-1")
        msg.register_publish(pub)
        msg.send_heartbeat(1000)
        assert len(published) == 1
        assert "heartbeat" in published[0][0]

    def test_subscribe_and_handle(self):
        received = []

        def handler(payload):
            received.append(payload)

        msg = InterNodeMessenger("node-1")
        msg.subscribe("test.topic", handler)
        msg.handle_message("test.topic", b"hello")
        assert len(received) == 1
        assert received[0] == b"hello"


class TestDistributedScheduler:
    def test_local_admission(self):
        WorkUnitRegistry.unlock()
        WorkUnitRegistry.clear()
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )

        local = EpochScheduler(EpochConfig(tu_budget=TU(100)))
        local.start_epoch(1000)

        published = []

        def pub(topic, payload):
            published.append((topic, payload))

        messenger = InterNodeMessenger("node-1")
        messenger.register_publish(pub)

        scorer = PlacementScorer()
        scheduler = DistributedScheduler("node-1", local, scorer, messenger)

        wu = WorkUnit(
            "wu-1", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )
        assert scheduler.submit(wu, 2000) is True
        assert len(published) == 0  # local admission, no remote dispatch

    def test_remote_dispatch_when_local_full(self):
        WorkUnitRegistry.unlock()
        WorkUnitRegistry.clear()
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )

        local = EpochScheduler(EpochConfig(tu_budget=TU(1), max_backlog=1))
        local.start_epoch(1000)
        # Fill local
        local.submit(
            WorkUnit(
                "wu-0", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
            ),
            1000,
        )

        published = []

        def pub(topic, payload):
            published.append((topic, payload))

        messenger = InterNodeMessenger("node-1")
        messenger.register_publish(pub)

        scorer = PlacementScorer()
        scheduler = DistributedScheduler("node-1", local, scorer, messenger)

        # Register a remote node with capacity
        scheduler.register_node(NodeProfile("node-2", 4, 16000, 10.0, 100, 5.0, 1000))

        wu = WorkUnit(
            "wu-1", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )
        assert scheduler.submit(wu, 2000) is True
        assert len(published) == 1  # remote dispatch
        assert "node-2.dispatch" in published[0][0]

    def test_reject_when_no_nodes(self):
        WorkUnitRegistry.unlock()
        WorkUnitRegistry.clear()
        WorkUnitRegistry.register(
            WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )

        local = EpochScheduler(EpochConfig(tu_budget=TU(1), max_backlog=1))
        local.start_epoch(1000)
        local.submit(
            WorkUnit(
                "wu-0", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
            ),
            1000,
        )

        published = []

        def pub(topic, payload):
            published.append((topic, payload))

        messenger = InterNodeMessenger("node-1")
        messenger.register_publish(pub)

        scorer = PlacementScorer()
        scheduler = DistributedScheduler("node-1", local, scorer, messenger)

        wu = WorkUnit(
            "wu-1", WorkUnitType.LLM_INFERENCE, WorkUnitDemand(dt=DT(1.0), tu=TU(2))
        )
        assert scheduler.submit(wu, 2000) is False  # no remote nodes, local full

    def test_cluster_status(self):
        WorkUnitRegistry.unlock()
        WorkUnitRegistry.clear()

        local = EpochScheduler(EpochConfig(tu_budget=TU(100)))
        local.start_epoch(1000)

        messenger = InterNodeMessenger("node-1")
        scorer = PlacementScorer()
        scheduler = DistributedScheduler("node-1", local, scorer, messenger)

        scheduler.register_node(NodeProfile("node-2", 4, 16000, 10.0, 100, 5.0, 1000))
        status = scheduler.get_cluster_status()
        assert status["local_node"] == "node-1"
        assert status["remote_nodes"] == 1
        assert "node-2" in status["node_profiles"]

    def test_remove_node(self):
        local = EpochScheduler(EpochConfig(tu_budget=TU(100)))
        local.start_epoch(1000)

        messenger = InterNodeMessenger("node-1")
        scorer = PlacementScorer()
        scheduler = DistributedScheduler("node-1", local, scorer, messenger)

        scheduler.register_node(NodeProfile("node-2", 4, 16000, 10.0, 100, 5.0, 1000))
        assert scheduler.list_nodes() == ("node-2",)

        scheduler.remove_node("node-2")
        assert scheduler.list_nodes() == ()
