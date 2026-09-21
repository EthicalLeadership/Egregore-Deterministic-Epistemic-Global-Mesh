# epistemic marker: provenance / auditability
"""LoadRegulator - Mantle layer token-bucket flow regulator."""

from dataclasses import dataclass, field

from egregore.domain.job_router_ports import ILoadRegulator


@dataclass
class TokenBucket:
    lane: str
    capacity: float
    tokens: float = field(default=0.0)
    refill_rate: float = 1.0
    last_refill_tick: int = 0
    total_consumed: float = 0.0
    total_rejected: int = 0

    def refill(self, tick: int) -> None:
        if tick <= self.last_refill_tick:
            return
        elapsed = tick - self.last_refill_tick
        self.tokens = min(self.capacity, self.tokens + (elapsed * self.refill_rate))
        self.last_refill_tick = tick

    def can_consume(self, cost: float) -> bool:
        return self.tokens >= cost

    def consume(self, cost: float) -> bool:
        if self.tokens >= cost:
            self.tokens -= cost
            self.total_consumed += cost
            return True
        self.total_rejected += 1
        return False


@dataclass
class LaneConfig:
    lane: str
    capacity: float
    refill_rate: float
    priority_weight: float = 1.0
    burst_allowance: float = 0.0


class LoadRegulator(ILoadRegulator):
    def __init__(self, buckets: dict[str, TokenBucket]):
        self._buckets = buckets
        self._current_tick = 0

    @classmethod
    def from_configs(cls, configs: list[LaneConfig]) -> "LoadRegulator":
        buckets = {}
        for cfg in configs:
            buckets[cfg.lane] = TokenBucket(
                lane=cfg.lane,
                capacity=cfg.capacity + cfg.burst_allowance,
                tokens=cfg.capacity + cfg.burst_allowance,
                refill_rate=cfg.refill_rate * cfg.priority_weight,
                last_refill_tick=0,
            )
        return cls(buckets)

    def can_admit(self, lane: str, cost: float) -> bool:
        bucket = self._buckets.get(lane)
        if bucket is None:
            return False
        return bucket.can_consume(cost)

    def consume(self, lane: str, cost: float) -> bool:
        bucket = self._buckets.get(lane)
        if bucket is None:
            return False
        return bucket.consume(cost)

    def refill(self, tick: int) -> None:
        if tick < self._current_tick:
            raise ValueError(f"Tick regression: {tick} < {self._current_tick}")
        self._current_tick = tick
        for bucket in self._buckets.values():
            bucket.refill(tick)

    def get_state(self, lane: str) -> dict:
        bucket = self._buckets.get(lane)
        if bucket is None:
            return {}
        return {
            "lane": bucket.lane,
            "tokens": round(bucket.tokens, 6),
            "capacity": bucket.capacity,
            "refill_rate": bucket.refill_rate,
            "last_refill_tick": bucket.last_refill_tick,
            "total_consumed": bucket.total_consumed,
            "total_rejected": bucket.total_rejected,
        }

    def get_all_states(self) -> dict[str, dict]:
        return {lane: self.get_state(lane) for lane in self._buckets}

    def add_lane(self, config: LaneConfig) -> None:
        if config.lane in self._buckets:
            raise ValueError(f"Lane already exists: {config.lane}")
        self._buckets[config.lane] = TokenBucket(
            lane=config.lane,
            capacity=config.capacity + config.burst_allowance,
            tokens=config.capacity + config.burst_allowance,
            refill_rate=config.refill_rate * config.priority_weight,
            last_refill_tick=self._current_tick,
        )

    def remove_lane(self, lane: str) -> None:
        if lane not in self._buckets:
            raise ValueError(f"Lane not found: {lane}")
        del self._buckets[lane]
