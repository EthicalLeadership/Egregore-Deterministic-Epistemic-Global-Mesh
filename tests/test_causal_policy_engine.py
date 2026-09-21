"""
EGREGORE LAW: Causal Policy Engine Test Matrix
"""

from __future__ import annotations

import pytest

from egregore.application.causal_policy_engine import (
    CausalPolicyEngine,
    CausalPolicyError,
    ForkDetectedError,
    PolicyDecision,
    StaleDecisionError,
)
from egregore.domain.causal_vector import CausalVector
from egregore.domain.execution_block import ExecutionBlock


class TestCausalVector:
    def test_increment(self):
        v = CausalVector(vector={"A": 1, "B": 2})
        v2 = v.increment("A")
        assert v2.vector["A"] == 2
        assert v2.vector["B"] == 2
        # Original unchanged
        assert v.vector["A"] == 1

    def test_merge(self):
        v1 = CausalVector(vector={"A": 1, "B": 2})
        v2 = CausalVector(vector={"B": 3, "C": 1})
        merged = v1.merge(v2)
        assert merged.vector == {"A": 1, "B": 3, "C": 1}

    def test_happens_before(self):
        v1 = CausalVector(vector={"A": 1, "B": 2})
        v2 = CausalVector(vector={"A": 2, "B": 2})
        assert v1.happens_before(v2)
        assert not v2.happens_before(v1)
        assert not v1.happens_before(v1)

    def test_concurrent(self):
        v1 = CausalVector(vector={"A": 1, "B": 2})
        v2 = CausalVector(vector={"A": 2, "B": 1})
        assert v1.concurrent_with(v2)
        assert v2.concurrent_with(v1)

    def test_valid_predecessor(self):
        v1 = CausalVector(vector={"A": 1, "B": 2})
        v2 = v1.increment("A")
        assert v1.is_valid_predecessor(v2)
        assert not v2.is_valid_predecessor(v1)

    def test_invalid_predecessor_multi_increment(self):
        v1 = CausalVector(vector={"A": 1})
        v2 = CausalVector(vector={"A": 3})
        assert not v1.is_valid_predecessor(v2)

    def test_invalid_predecessor_multi_node(self):
        v1 = CausalVector(vector={"A": 1, "B": 1})
        v2 = CausalVector(vector={"A": 2, "B": 2})
        assert not v1.is_valid_predecessor(v2)


class TestCausalPolicyEngine:
    def test_commit_and_evaluate(self):
        engine = CausalPolicyEngine(_node_id="node-1")

        block = ExecutionBlock(
            block_id="block-1",
            block_seq=0,
            created_at_ns=1000,
            records=(),
            merkle_root="abc",
            previous_block_hash="0" * 64,
            causal_vector=CausalVector(vector={"node-1": 1}),
        ).with_integrity_hash()

        engine.commit_block(block)

        decision = engine.evaluate(
            decision_id="dec-1",
            policy_version="v1",
            input_hash="hash-1",
            block_dependencies=["block-1"],
            evaluator=lambda: {"allowed": True},
            timestamp_ns=2000,
        )

        assert decision.decision_id == "dec-1"
        assert decision.causal_vector.vector["node-1"] == 2
        assert decision.output["allowed"] is True

    def test_evaluate_without_dependency_raises(self):
        engine = CausalPolicyEngine(_node_id="node-1")

        with pytest.raises(StaleDecisionError):
            engine.evaluate(
                decision_id="dec-1",
                policy_version="v1",
                input_hash="hash-1",
                block_dependencies=["block-1"],
                evaluator=lambda: {},
                timestamp_ns=1000,
            )

    def test_duplicate_block_commit_raises(self):
        engine = CausalPolicyEngine(_node_id="node-1")
        block = ExecutionBlock(
            block_id="block-1",
            block_seq=0,
            created_at_ns=1000,
            records=(),
            merkle_root="abc",
            previous_block_hash="0" * 64,
        ).with_integrity_hash()

        engine.commit_block(block)
        with pytest.raises(CausalPolicyError):
            engine.commit_block(block)

    def test_fork_detection(self):
        engine = CausalPolicyEngine(_node_id="node-1")

        block = ExecutionBlock(
            block_id="block-1",
            block_seq=0,
            created_at_ns=1000,
            records=(),
            merkle_root="abc",
            previous_block_hash="0" * 64,
            causal_vector=CausalVector(vector={"node-1": 1}),
        ).with_integrity_hash()

        engine.commit_block(block)

        # First decision by node-1: vector becomes {"node-1": 2}
        engine.evaluate(
            decision_id="dec-1",
            policy_version="v1",
            input_hash="hash-1",
            block_dependencies=["block-1"],
            evaluator=lambda: {"result": "A"},
            timestamp_ns=2000,
        )

        # Inject a concurrent decision (simulating node-2 evaluating independently)

        engine._decisions["dec-2"] = PolicyDecision(
            decision_id="dec-2",
            policy_version="v1",
            input_hash="hash-2",
            output={"result": "B"},
            causal_vector=CausalVector(vector={"node-1": 1, "node-2": 2}),
            timestamp_ns=2000,
            block_dependencies=("block-1",),
        )

        # Third decision by node-1: vector {"node-1": 2} is concurrent with injected {"node-1": 1, "node-2": 2}
        with pytest.raises(ForkDetectedError):
            engine.evaluate(
                decision_id="dec-3",
                policy_version="v1",
                input_hash="hash-3",
                block_dependencies=["block-1"],
                evaluator=lambda: {"result": "C"},
                timestamp_ns=3000,
            )

    def test_causal_chain(self):
        engine = CausalPolicyEngine(_node_id="node-1")

        # Block 1
        b1 = ExecutionBlock(
            block_id="block-1",
            block_seq=0,
            created_at_ns=1000,
            records=(),
            merkle_root="a",
            previous_block_hash="0" * 64,
            causal_vector=CausalVector(vector={"node-1": 1}),
        ).with_integrity_hash()
        engine.commit_block(b1)

        # Decision 1 depends on block 1
        d1 = engine.evaluate(
            decision_id="dec-1",
            policy_version="v1",
            input_hash="h1",
            block_dependencies=["block-1"],
            evaluator=lambda: {},
            timestamp_ns=2000,
        )
        assert d1.causal_vector.vector["node-1"] == 2

        # Block 2 depends on decision 1's vector
        b2 = ExecutionBlock(
            block_id="block-2",
            block_seq=1,
            created_at_ns=3000,
            records=(),
            merkle_root="b",
            previous_block_hash=b1.integrity_hash,
            causal_vector=d1.causal_vector.increment("node-1"),
        ).with_integrity_hash()
        engine.commit_block(b2)

        # Decision 2 depends on block 2
        d2 = engine.evaluate(
            decision_id="dec-2",
            policy_version="v1",
            input_hash="h2",
            block_dependencies=["block-2"],
            evaluator=lambda: {},
            timestamp_ns=4000,
        )
        assert d2.causal_vector.vector["node-1"] == 4

    def test_list_methods(self):
        engine = CausalPolicyEngine(_node_id="node-1")
        assert engine.list_committed_blocks() == ()
        assert engine.list_decisions() == ()

        block = ExecutionBlock(
            block_id="block-1",
            block_seq=0,
            created_at_ns=1000,
            records=(),
            merkle_root="abc",
            previous_block_hash="0" * 64,
        ).with_integrity_hash()
        engine.commit_block(block)

        engine.evaluate(
            decision_id="dec-1",
            policy_version="v1",
            input_hash="h1",
            block_dependencies=["block-1"],
            evaluator=lambda: {},
            timestamp_ns=2000,
        )

        assert engine.list_committed_blocks() == ("block-1",)
        assert engine.list_decisions() == ("dec-1",)
        assert engine.get_decision("dec-1") is not None
        assert engine.get_decision("missing") is None
