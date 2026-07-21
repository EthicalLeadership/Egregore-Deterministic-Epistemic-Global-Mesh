"""Load regulator ports — domain-level protocol for Mantle flow control."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class ILoadRegulator(Protocol):
    """Mantle regulator: admits or rejects work based on lane token availability."""

    def can_admit(self, lane: str, cost: float) -> bool:
        """Check if lane has enough tokens for cost without consuming them."""
        ...

    def consume(self, lane: str, cost: float) -> bool:
        """Attempt to consume tokens. Return True if admitted, False if rejected."""
        ...

    def refill(self, tick: int) -> None:
        """Deterministic refill event, aligned with gearbox tick."""
        ...

    def get_state(self, lane: str) -> dict:
        """Return lane state: tokens, capacity, refill_rate."""
        ...
