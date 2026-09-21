#!/usr/bin/env python3
"""
SafetyCircuitCoordinator — Standalone Study Prototype
Based on: docs/architecture/safety_circuit_coordinator_design.md

Run: python docs/architecture/prototypes/proto_safety_circuit.py
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Mapping, Optional, Protocol


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class SignalType(str, Enum):
    THERMAL = "thermal"
    LOAD = "load"
    ADMISSION = "admission"
    MANUAL = "manual"
    FUSED = "fused"


@dataclass(frozen=True)
class SafetySignal:
    signal_id: str
    source: str
    signal_type: SignalType
    severity: Severity
    metric_value: float
    timestamp_ns: int
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["signal_type"] = self.signal_type.value
        d["severity"] = self.severity.value
        return d


@dataclass
class SafetyPolicy:
    name: str
    signal_type: SignalType
    trip_threshold: float
    reset_threshold: float
    min_duration_ns: int
    priority: int
    cooldown_ns: int
    escalate_to: Optional[str] = None


@dataclass
class CircuitBreaker:
    policy: SafetyPolicy
    state: CircuitState = CircuitState.CLOSED
    first_tripped_ns: Optional[int] = None
    last_tripped_ns: Optional[int] = None
    cooldown_until_ns: Optional[int] = None
    last_metric: Optional[float] = None

    def evaluate(self, now_ns: int, metric_value: float) -> Optional[SafetySignal]:
        self.last_metric = metric_value
        if self.cooldown_until_ns and now_ns < self.cooldown_until_ns:
            return None
        tripping = metric_value >= self.policy.trip_threshold
        resetting = metric_value <= self.policy.reset_threshold
        if self.state == CircuitState.CLOSED and tripping:
            self.state = CircuitState.OPEN
            self.first_tripped_ns = now_ns
            self.last_tripped_ns = now_ns
            self.cooldown_until_ns = now_ns + self.policy.cooldown_ns
            return SafetySignal(
                signal_id=f"trip-{self.policy.name}-{now_ns}",
                source="safety_circuit",
                signal_type=self.policy.signal_type,
                severity=Severity.CRITICAL,
                metric_value=metric_value,
                timestamp_ns=now_ns,
                payload={"policy": self.policy.name, "threshold": self.policy.trip_threshold, "state": self.state.value},
            )
        if self.state == CircuitState.OPEN and resetting and self.last_tripped_ns and (now_ns - self.last_tripped_ns) >= self.policy.min_duration_ns:
            self.state = CircuitState.HALF_OPEN
        if self.state == CircuitState.HALF_OPEN and resetting:
            self.state = CircuitState.CLOSED
            self.first_tripped_ns = None
            self.last_tripped_ns = None
            self.cooldown_until_ns = None
        return None


class ZarcJournalPort(Protocol):
    def append(self, event_type: str, payload: Mapping[str, object]) -> str: ...


class EscalationServicePort(Protocol):
    def freeze(self, reason: str, action_id: str) -> None: ...
    def unfreeze(self, reason: str, action_id: str) -> None: ...


class LoadSamplerPort(Protocol):
    def sample(self) -> Mapping[SignalType, float]: ...


class InMemoryZarcJournal:
    def __init__(self):
        self.events: list[dict[str, Any]] = []

    def append(self, event_type: str, payload: Mapping[str, object]) -> str:
        event = {"event_type": event_type, "payload": dict(payload)}
        self.events.append(event)
        return "sha256-dummy"


class InMemoryEscalationService:
    def __init__(self):
        self.frozen = False
        self.log: list[dict[str, Any]] = []

    def freeze(self, reason: str, action_id: str) -> None:
        self.frozen = True
        self.log.append({"action": "freeze", "reason": reason, "action_id": action_id})
        print(f"[ESCALATION] FREEZE: {reason} ({action_id})")

    def unfreeze(self, reason: str, action_id: str) -> None:
        self.frozen = False
        self.log.append({"action": "unfreeze", "reason": reason, "action_id": action_id})
        print(f"[ESCALATION] UNFREEZE: {reason} ({action_id})")


class SimulatedLoadSampler:
    def __init__(self, scenario: list[Mapping[SignalType, float]]):
        self.scenario = list(scenario)
        self.idx = 0

    def sample(self) -> Mapping[SignalType, float]:
        values = self.scenario[self.idx % len(self.scenario)]
        self.idx += 1
        return values


class SafetyCircuitCoordinator:
    def __init__(
        self,
        policies: list[SafetyPolicy],
        zarc_journal: ZarcJournalPort,
        escalation_service: EscalationServicePort,
        load_sampler: LoadSamplerPort,
    ):
        self.breakers: dict[SignalType, CircuitBreaker] = {
            p.signal_type: CircuitBreaker(policy=p) for p in policies
        }
        self.zarc_journal = zarc_journal
        self.escalation_service = escalation_service
        self.load_sampler = load_sampler
        self._frozen = False

    def receive_signal(self, signal: SafetySignal) -> Optional[str]:
        breaker = self.breakers.get(signal.signal_type)
        if breaker is None:
            return None
        trip = breaker.evaluate(signal.timestamp_ns, signal.metric_value)
        if trip:
            self.zarc_journal.append("safety_circuit_trip", trip.to_dict())
            self._maybe_freeze(trip)
            return trip.signal_id
        self.zarc_journal.append("safety_circuit_sample", signal.to_dict())
        return None

    def tick(self, now_ns: Optional[int] = None) -> list[str]:
        now = now_ns or time.time_ns()
        tripped: list[str] = []
        samples = self.load_sampler.sample()
        for signal_type, metric in samples.items():
            signal = SafetySignal(
                signal_id=f"{signal_type.value}-{now}",
                source="sampler",
                signal_type=signal_type,
                severity=Severity.INFO,
                metric_value=metric,
                timestamp_ns=now,
            )
            tid = self.receive_signal(signal)
            if tid:
                tripped.append(tid)
        return tripped

    def health(self) -> Mapping[str, Any]:
        return {
            "frozen": self._frozen,
            "circuits": {
                st.value: {"state": cb.state.value, "last_metric": cb.last_metric}
                for st, cb in self.breakers.items()
            },
        }

    def manual_reset(self, signal_type: SignalType) -> None:
        breaker = self.breakers.get(signal_type)
        if breaker:
            breaker.state = CircuitState.CLOSED
            breaker.first_tripped_ns = None
            breaker.last_tripped_ns = None
            breaker.cooldown_until_ns = None
            self.zarc_journal.append(
                "safety_circuit_reset",
                {"signal_type": signal_type.value, "timestamp_ns": time.time_ns()},
            )
            self._unfreeze(f"Manual reset: {signal_type.value}", signal_type.value)

    def _maybe_freeze(self, trip: SafetySignal) -> None:
        if trip.severity in (Severity.CRITICAL, Severity.EMERGENCY):
            self._frozen = True
            self.escalation_service.freeze(f"{trip.signal_type.value} circuit tripped", trip.signal_id)

    def _unfreeze(self, reason: str, action_id: str) -> None:
        self._frozen = False
        self.escalation_service.unfreeze(reason, action_id)


def demo():
    print("=" * 60)
    print("SafetyCircuitCoordinator Prototype")
    print("=" * 60)
    print()

    policies = [
        SafetyPolicy(
            name="thermal",
            signal_type=SignalType.THERMAL,
            trip_threshold=85.0,
            reset_threshold=70.0,
            min_duration_ns=1_000_000_000,
            priority=1,
            cooldown_ns=2_000_000_000,
        ),
        SafetyPolicy(
            name="load",
            signal_type=SignalType.LOAD,
            trip_threshold=95.0,
            reset_threshold=75.0,
            min_duration_ns=500_000_000,
            priority=2,
            cooldown_ns=1_000_000_000,
        ),
        SafetyPolicy(
            name="admission",
            signal_type=SignalType.ADMISSION,
            trip_threshold=1000.0,
            reset_threshold=500.0,
            min_duration_ns=500_000_000,
            priority=3,
            cooldown_ns=1_000_000_000,
        ),
    ]

    scenario = [
        {SignalType.THERMAL: 60.0, SignalType.LOAD: 40.0, SignalType.ADMISSION: 100.0},
        {SignalType.THERMAL: 88.0, SignalType.LOAD: 40.0, SignalType.ADMISSION: 100.0},
        {SignalType.THERMAL: 90.0, SignalType.LOAD: 97.0, SignalType.ADMISSION: 100.0},
        {SignalType.THERMAL: 90.0, SignalType.LOAD: 97.0, SignalType.ADMISSION: 1200.0},
        {SignalType.THERMAL: 65.0, SignalType.LOAD: 60.0, SignalType.ADMISSION: 300.0},
    ]

    coordinator = SafetyCircuitCoordinator(
        policies=policies,
        zarc_journal=InMemoryZarcJournal(),
        escalation_service=InMemoryEscalationService(),
        load_sampler=SimulatedLoadSampler(scenario),
    )

    print("--- Initial healthy tick ---")
    coordinator.tick(1_000_000_000)
    print(json.dumps(coordinator.health(), indent=2))

    print("\n--- Thermal spike ---")
    coordinator.tick(2_000_000_000)
    print(json.dumps(coordinator.health(), indent=2))

    print("\n--- Combined thermal + load ---")
    coordinator.tick(3_500_000_000)
    print(json.dumps(coordinator.health(), indent=2))

    print("\n--- Admission overload ---")
    coordinator.tick(4_000_000_000)
    print(json.dumps(coordinator.health(), indent=2))

    print("\n--- Recovery ---")
    coordinator.tick(5_500_000_000)
    print(json.dumps(coordinator.health(), indent=2))

    print("\n--- Manual reset ---")
    coordinator.manual_reset(SignalType.THERMAL)
    coordinator.manual_reset(SignalType.LOAD)
    coordinator.manual_reset(SignalType.ADMISSION)
    print(json.dumps(coordinator.health(), indent=2))

    print("\n" + "=" * 60)


if __name__ == "__main__":
    demo()
