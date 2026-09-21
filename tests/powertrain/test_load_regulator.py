"""Tests for LoadRegulator and TokenBucket."""

import pytest

from egregore.domain.job_router_ports import ILoadRegulator
from egregore.powertrain.load_regulator import LaneConfig, LoadRegulator, TokenBucket


class TestTokenBucketBasics:
    def test_initially_full(self):
        bucket = TokenBucket(lane="test", capacity=100, tokens=100, refill_rate=10)
        assert bucket.can_consume(50) is True
        assert bucket.can_consume(100) is True
        assert bucket.can_consume(101) is False

    def test_consume_reduces_tokens(self):
        bucket = TokenBucket(lane="test", capacity=100, tokens=100, refill_rate=10)
        assert bucket.consume(30) is True
        assert bucket.tokens == 70
        assert bucket.total_consumed == 30

    def test_consume_rejects_when_empty(self):
        bucket = TokenBucket(lane="test", capacity=100, tokens=5, refill_rate=10)
        assert bucket.consume(10) is False
        assert bucket.total_rejected == 1

    def test_refill_adds_tokens(self):
        bucket = TokenBucket(
            lane="test", capacity=100, tokens=0, refill_rate=10, last_refill_tick=0
        )
        bucket.refill(tick=5)
        assert bucket.tokens == 50

    def test_refill_caps_at_capacity(self):
        bucket = TokenBucket(
            lane="test", capacity=100, tokens=90, refill_rate=10, last_refill_tick=0
        )
        bucket.refill(tick=5)
        assert bucket.tokens == 100

    def test_refill_no_regression(self):
        bucket = TokenBucket(
            lane="test", capacity=100, tokens=50, refill_rate=10, last_refill_tick=10
        )
        bucket.refill(tick=5)
        assert bucket.tokens == 50


class TestLoadRegulatorDeterminism:
    def test_same_ticks_same_state(self):
        configs = [
            LaneConfig("inference", capacity=100, refill_rate=10),
            LaneConfig("governance", capacity=50, refill_rate=5),
        ]
        reg1 = LoadRegulator.from_configs(configs)
        reg2 = LoadRegulator.from_configs(configs)
        reg1.refill(tick=3)
        reg2.refill(tick=3)
        reg1.consume("inference", 15)
        reg2.consume("inference", 15)
        assert reg1.get_state("inference") == reg2.get_state("inference")

    def test_tick_regression_raises(self):
        configs = [LaneConfig("inference", capacity=100, refill_rate=10)]
        reg = LoadRegulator.from_configs(configs)
        reg.refill(tick=5)
        with pytest.raises(ValueError, match="Tick regression"):
            reg.refill(tick=3)


class TestLoadRegulatorLanes:
    def test_lane_isolation(self):
        configs = [
            LaneConfig("inference", capacity=100, refill_rate=10),
            LaneConfig("governance", capacity=50, refill_rate=5),
        ]
        reg = LoadRegulator.from_configs(configs)
        reg.refill(tick=1)
        reg.consume("inference", 100)
        assert reg.get_state("inference")["tokens"] == 0
        assert reg.get_state("governance")["tokens"] == 50

    def test_unknown_lane_rejected(self):
        configs = [LaneConfig("inference", capacity=100, refill_rate=10)]
        reg = LoadRegulator.from_configs(configs)
        assert reg.can_admit("nonexistent", 1) is False

    def test_burst_allowance(self):
        configs = [
            LaneConfig("inference", capacity=100, refill_rate=10, burst_allowance=50)
        ]
        reg = LoadRegulator.from_configs(configs)
        assert reg.consume("inference", 150) is True

    def test_implements_protocol(self):
        configs = [LaneConfig("inference", capacity=100, refill_rate=10)]
        reg = LoadRegulator.from_configs(configs)
        assert isinstance(reg, ILoadRegulator)


class TestLoadRegulatorBackpressure:
    def test_rejection_counts(self):
        configs = [LaneConfig("inference", capacity=10, refill_rate=1)]
        reg = LoadRegulator.from_configs(configs)
        reg.consume("inference", 10)
        for _ in range(5):
            reg.consume("inference", 1)
        state = reg.get_state("inference")
        assert state["total_rejected"] == 5
