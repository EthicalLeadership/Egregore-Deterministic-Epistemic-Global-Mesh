from typing import Protocol, runtime_checkable


@runtime_checkable
class ILoadRegulator(Protocol):
    """Load regulation port — defined in domain so all layers can import."""

    def acquire(self, tokens: int, lane: str = "default") -> bool: ...
    def release(self, tokens: int, lane: str = "default") -> None: ...
