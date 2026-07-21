"""
BLACKSTAR LAW: Unit System
DT (Deterministic Throughput) and TU (Temporal Unit) value objects.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DT:
    value: float

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError(f"DT cannot be negative: {self.value}")
        if not isinstance(self.value, (int, float)):
            raise TypeError(f"DT value must be numeric, got {type(self.value)}")

    def __lt__(self, other: DT | float) -> bool:
        other_val = other.value if isinstance(other, DT) else other
        return self.value < other_val

    def __le__(self, other: DT | float) -> bool:
        other_val = other.value if isinstance(other, DT) else other
        return self.value <= other_val

    def __gt__(self, other: DT | float) -> bool:
        other_val = other.value if isinstance(other, DT) else other
        return self.value > other_val

    def __ge__(self, other: DT | float) -> bool:
        other_val = other.value if isinstance(other, DT) else other
        return self.value >= other_val

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DT):
            return NotImplemented
        return self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __add__(self, other: DT | float) -> DT:
        other_val = other.value if isinstance(other, DT) else other
        return DT(self.value + other_val)

    def __sub__(self, other: DT | float) -> DT:
        other_val = other.value if isinstance(other, DT) else other
        result = self.value - other_val
        if result < 0:
            raise ValueError(
                f"DT subtraction would yield negative: {self.value} - {other_val}"
            )
        return DT(result)

    def __mul__(self, other: float | int) -> DT:
        return DT(self.value * other)

    def __truediv__(self, other: float | int) -> DT:
        if other == 0:
            raise ZeroDivisionError("Cannot divide DT by zero")
        return DT(self.value / other)

    @property
    def gflops(self) -> float:
        return self.value * 10.0

    @classmethod
    def from_gflops(cls, gflops: float) -> DT:
        return cls(gflops / 10.0)

    def to_canonical(self) -> dict:
        return {"__type__": "DT", "value": round(self.value, 6)}

    @classmethod
    def from_canonical(cls, data: dict) -> DT:
        if data.get("__type__") != "DT":
            raise ValueError(f"Invalid DT canonical type: {data}")
        return cls(data["value"])

    def __repr__(self) -> str:
        return f"DT({self.value:.2f})"

    def __str__(self) -> str:
        return f"{self.value:.2f} DT ({self.gflops:.1f} GFLOPS)"


@dataclass(frozen=True, slots=True)
class TU:
    value: int
    tau_max_ns: int = 10_000_000

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError(f"TU cannot be negative: {self.value}")
        if not isinstance(self.value, int):
            raise TypeError(f"TU value must be integer, got {type(self.value)}")
        if self.tau_max_ns <= 0:
            raise ValueError(f"TU tau_max_ns must be positive: {self.tau_max_ns}")

    def __lt__(self, other: TU | int) -> bool:
        other_val = other.value if isinstance(other, TU) else other
        return self.value < other_val

    def __le__(self, other: TU | int) -> bool:
        other_val = other.value if isinstance(other, TU) else other
        return self.value <= other_val

    def __gt__(self, other: TU | int) -> bool:
        other_val = other.value if isinstance(other, TU) else other
        return self.value > other_val

    def __ge__(self, other: TU | int) -> bool:
        other_val = other.value if isinstance(other, TU) else other
        return self.value >= other_val

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TU):
            return NotImplemented
        return self.value == other.value and self.tau_max_ns == other.tau_max_ns

    def __hash__(self) -> int:
        return hash((self.value, self.tau_max_ns))

    def __add__(self, other: TU | int) -> TU:
        other_val = other.value if isinstance(other, TU) else other
        return TU(self.value + other_val, self.tau_max_ns)

    def __sub__(self, other: TU | int) -> TU:
        other_val = other.value if isinstance(other, TU) else other
        result = self.value - other_val
        if result < 0:
            raise ValueError(
                f"TU subtraction would yield negative: {self.value} - {other_val}"
            )
        return TU(result, self.tau_max_ns)

    def __mul__(self, other: int | float) -> TU:
        if isinstance(other, float):
            raise TypeError("TU multiplication by float not permitted")
        return TU(self.value * other, self.tau_max_ns)

    @property
    def tau_max_ms(self) -> float:
        return self.tau_max_ns / 1_000_000.0

    def to_canonical(self) -> dict:
        return {"__type__": "TU", "value": self.value, "tau_max_ns": self.tau_max_ns}

    @classmethod
    def from_canonical(cls, data: dict) -> TU:
        if data.get("__type__") != "TU":
            raise ValueError(f"Invalid TU canonical type: {data}")
        return cls(data["value"], data["tau_max_ns"])

    def __repr__(self) -> str:
        return f"TU({self.value}, tau_max={self.tau_max_ms:.2f}ms)"

    def __str__(self) -> str:
        return f"{self.value} TU (max {self.tau_max_ms:.2f}ms)"


DT_ZERO = DT(0.0)
TU_ZERO = TU(0)
