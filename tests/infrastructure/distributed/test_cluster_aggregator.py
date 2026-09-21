from egregore.domain.units import DT, TU
from egregore.infrastructure.distributed.cluster_aggregator import (
    ClusterAggregator,
    NodeCapacity,
)


class TestClusterAggregator:
    def test_empty_cluster(self):
        agg = ClusterAggregator()
        capacity = agg.get_cluster_capacity()
        assert capacity["status"] == "NO_NODES"

    def test_register_node(self):
        agg = ClusterAggregator()
        node = NodeCapacity(
            node_id="pioneer1",
            available_dt=DT(5.0),
            available_tu=TU(50),
            total_dt=DT(10.0),
            total_tu=TU(100),
            thermal_throttle=False,
            last_seen_ns=1000,
        )
        agg.register_node(node)
        assert agg.node_count == 1
        capacity = agg.get_cluster_capacity()
        assert capacity["status"] == "OK"
        assert capacity["node_count"] == 1

    def test_multi_node(self):
        agg = ClusterAggregator()
        agg.register_node(
            NodeCapacity("p1", DT(5.0), TU(50), DT(10.0), TU(100), False, 1000)
        )
        agg.register_node(
            NodeCapacity("p2", DT(3.0), TU(30), DT(8.0), TU(80), False, 1000)
        )
        capacity = agg.get_cluster_capacity()
        assert capacity["total_dt"]["value"] == 8.0
        assert capacity["total_tu"]["value"] == 80

    def test_remove_node(self):
        agg = ClusterAggregator()
        agg.register_node(
            NodeCapacity("p1", DT(5.0), TU(50), DT(10.0), TU(100), False, 1000)
        )
        agg.remove_node("p1")
        assert agg.node_count == 0
