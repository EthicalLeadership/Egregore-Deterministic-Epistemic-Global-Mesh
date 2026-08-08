# Design Document: SafetyCircuitCoordinator

**Status:** Study-phase production design  
**Scope:** Unify Egregore's fragmented safety mechanisms into a single fail-closed coordination layer.  
**Goal:** Provide precise layering, class responsibilities, API signatures, and wiring so the system clamps or freezes deterministically when any critical signal fires.  

---

## 1. Design principles

1. **Fail-closed.** Any unresolvable disagreement among safety monitors results in freeze.
2. **Hysteresis.** Use cooldown windows and level thresholds to avoid flapping between clamped and unclamped states.
3. **Read-only observation where possible.** The coordinator does not own the underlying controllers; it observes their state and issues clamp commands through injected ports.
4. **Deterministic replay.** Every clamp/unclamp decision and every safety signal snapshot is written to `.zarc`.
5. **Layer-compliant.** Domain models are pure; application layer orchestrates; interface layer exposes HTTP; infrastructure adapters own cross-layer wiring.

---

## 2. Existing safety pieces to coordinate

| Mechanism | Current role | File(s) |
|-----------|--------------|---------|
| `FreezeController` | SEL-X state machine: HEALTHY → FROZEN → RECONCILING → HEALTHY | `src/egregore/shared/freeze_state.py` |
| `CircuitBreaker` | Per-call failure-rate breaker | `src/egregore/patterns/circuit_breaker.py` |
| `ThermalGovernor` | Gearbox thermal throttling, G5 emission events | `src/egregore/powertrain/thermal_governor.py`, `src/egregore/application/thermal_governor_service.py` |
| `LoadRegulator` | Token-bucket rate limiting | `src/egregore/powertrain/load_regulator.py` |
| `AdmissionController` | DT/TU/backlog rejection | `src/egregore/application/admission_controller.py` |
| `AnchorumIntegrityGate` | Forensic integrity gate | `src/egregore/governance/anchorum_integrity_gate.py` |
| `EscalationService` | Opens escalations, logs to `.zarc`, freezes on CRITICAL/OVERRIDE | `src/egregore/application/escalation_service.py` |

These are strong but isolated. The coordinator is the conductor.

---

## 3. Layered module layout

```
src/egregore/
├── domain/
│   └── safety_circuit.py         # SafetySignal, SafetyLevel, SafetyState,
│                                 # ClampRule, composite decision logic
├── application/
│   ├── safety_circuit_coordinator.py
│   └── ports/
│       └── safety_circuit_ports.py
├── interface/
│   └── http_api/http/v1/
│       └── safety.py             # /v1/safety/* endpoints
└── infrastructure/
    └── safety_circuit_adapters.py
```

**Import rule summary**

| Layer | May import from | Must not import from |
|-------|-----------------|----------------------|
| `domain/safety_circuit.py` | `shared/` only | `application/`, `infrastructure/`, `interface/` |
| `application/safety_circuit_coordinator.py` | `domain/`, `shared/`, `application/ports/` | `infrastructure/`, `interface/` |
| `application/ports/safety_circuit_ports.py` | `domain/`, `shared/` | `infrastructure/`, `interface/` |
| `interface/http_api/http/v1/safety.py` | `application/`, `domain/`, `shared/` | `infrastructure/` |
| `infrastructure/safety_circuit_adapters.py` | any layer | — |

---

## 4. Domain layer (`src/egregore/domain/safety_circuit.py`)

### 4.1 Enums

```python
from enum import Enum

class SafetyLevel(str, Enum):
    HEALTHY = "healthy"
    ELEVATED = "elevated"
    CRITICAL = "critical"

class ClampAction(str, Enum):
    NONE = "none"
    THROTTLE = "throttle"
    REJECT = "reject"
    FREEZE = "freeze"
```

### 4.2 `SafetySignal`

```python
from dataclasses import dataclass
from typing import Any, Mapping

@dataclass(frozen=True)
class SafetySignal:
    source: str              # e.g. "thermal_governor", "load_regulator", "circuit_breaker"
    level: SafetyLevel
    reason: str
    timestamp_ns: int
    context: Mapping[str, Any] = field(default_factory=dict)
```

### 4.3 `SafetyState`

```python
from dataclasses import dataclass, field
from typing import List

@dataclass(frozen=True)
class SafetyState:
    overall_level: SafetyLevel
    clamp_action: ClampAction
    active_signals: List[SafetySignal] = field(default_factory=list)
    frozen: bool = False
    timestamp_ns: int = 0
```

### 4.4 `ClampRule`

```python
from dataclasses import dataclass
from typing import Callable, Optional

@dataclass(frozen=True)
class ClampRule:
    name: str
    predicate: Callable[[List[SafetySignal]], bool]
    action: ClampAction
    cooldown_ns: int = 0
    required_sources: int = 1
```

Default rule set:

```python
DEFAULT_RULES = [
    ClampRule(
        name="single_critical_throttle",
        predicate=lambda signals: any(s.level == SafetyLevel.CRITICAL for s in signals),
        action=ClampAction.THROTTLE,
    ),
    ClampRule(
        name="multi_critical_freeze",
        predicate=lambda signals: sum(1 for s in signals if s.level == SafetyLevel.CRITICAL) >= 2,
        action=ClampAction.FREEZE,
    ),
    ClampRule(
        name="integrity_breach_freeze",
        predicate=lambda signals: any(s.source == "anchorum_integrity_gate" and s.level == SafetyLevel.CRITICAL for s in signals),
        action=ClampAction.FREEZE,
    ),
]
```

### 4.5 `SafetyCircuitDecision`

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class SafetyCircuitDecision:
    previous_action: ClampAction
    current_action: ClampAction
    triggered_rules: List[str]
    active_signals_count: int
    timestamp_ns: int
```

---

## 5. Application ports (`src/egregore/application/ports/safety_circuit_ports.py`)

```python
from typing import Mapping, Protocol
from egregore.domain.safety_circuit import SafetySignal, SafetyLevel

class FreezeControllerPort(Protocol):
    def is_frozen(self) -> bool: ...
    def freeze(self, reason: str, evidence: str) -> None: ...
    def unfreeze(self, reason: str, operator_id: str) -> None: ...

class CircuitBreakerPort(Protocol):
    def state(self) -> str: ...  # CLOSED | OPEN | HALF_OPEN
    def failure_rate(self) -> float: ...

class ThermalMonitorPort(Protocol):
    def current_level(self) -> SafetyLevel: ...
    def temperature(self) -> float: ...

class LoadMonitorPort(Protocol):
    def current_level(self) -> SafetyLevel: ...
    def utilization(self) -> float: ...

class AdmissionControllerPort(Protocol):
    def set_clamp(self, action: ClampAction) -> None: ...
    def current_clamp(self) -> ClampAction: ...

class ZarcJournalPort(Protocol):
    def append(self, event_type: str, payload: Mapping[str, object]) -> str: ...
```

---

## 6. Application service (`src/egregore/application/safety_circuit_coordinator.py`)

```python
from dataclasses import dataclass, field
from typing import List, Mapping
import time

from egregore.domain.safety_circuit import (
    ClampAction,
    ClampRule,
    SafetyCircuitDecision,
    SafetyLevel,
    SafetySignal,
    SafetyState,
)
from egregore.application.ports.safety_circuit_ports import (
    AdmissionControllerPort,
    CircuitBreakerPort,
    FreezeControllerPort,
    LoadMonitorPort,
    ThermalMonitorPort,
    ZarcJournalPort,
)

@dataclass
class SafetyCircuitCoordinator:
    freeze_controller: FreezeControllerPort
    circuit_breakers: List[CircuitBreakerPort]
    thermal_monitor: ThermalMonitorPort
    load_monitor: LoadMonitorPort
    admission_controller: AdmissionControllerPort
    zarc_journal: ZarcJournalPort
    rules: List[ClampRule] = field(default_factory=lambda: list(DEFAULT_RULES))
    _signal_history: List[SafetySignal] = field(default_factory=list, init=False)
    _last_action: ClampAction = field(default=ClampAction.NONE, init=False)
    _last_action_ns: int = field(default=0, init=False)

    def sample(self) -> SafetyState:
        signals: List[SafetySignal] = []
        now = time.time_ns()

        # Freeze controller
        if self.freeze_controller.is_frozen():
            signals.append(SafetySignal(
                source="freeze_controller",
                level=SafetyLevel.CRITICAL,
                reason="system_frozen",
                timestamp_ns=now,
            ))

        # Circuit breakers
        for idx, cb in enumerate(self.circuit_breakers):
            if cb.state() == "OPEN":
                signals.append(SafetySignal(
                    source=f"circuit_breaker_{idx}",
                    level=SafetyLevel.CRITICAL,
                    reason=f"breaker_open_failure_rate_{cb.failure_rate():.2f}",
                    timestamp_ns=now,
                ))
            elif cb.state() == "HALF_OPEN":
                signals.append(SafetySignal(
                    source=f"circuit_breaker_{idx}",
                    level=SafetyLevel.ELEVATED,
                    reason="breaker_half_open",
                    timestamp_ns=now,
                ))

        # Thermal
        thermal_level = self.thermal_monitor.current_level()
        if thermal_level != SafetyLevel.HEALTHY:
            signals.append(SafetySignal(
                source="thermal_governor",
                level=thermal_level,
                reason=f"thermal_temperature_{self.thermal_monitor.temperature()}",
                timestamp_ns=now,
            ))

        # Load
        load_level = self.load_monitor.current_level()
        if load_level != SafetyLevel.HEALTHY:
            signals.append(SafetySignal(
                source="load_regulator",
                level=load_level,
                reason=f"load_utilization_{self.load_monitor.utilization()}",
                timestamp_ns=now,
            ))

        self._signal_history.extend(signals)

        # Evaluate rules with hysteresis
        action = ClampAction.NONE
        triggered: List[str] = []
        for rule in self.rules:
            if self._in_cooldown(rule):
                continue
            if rule.predicate(signals):
                if action.value < rule.action.value:  # escalate to highest action
                    action = rule.action
                triggered.append(rule.name)

        # If already frozen, do not downgrade below FREEZE
        if self.freeze_controller.is_frozen():
            action = max(action, ClampAction.FREEZE)

        frozen = action == ClampAction.FREEZE or self.freeze_controller.is_frozen()

        overall = SafetyLevel.HEALTHY
        if any(s.level == SafetyLevel.CRITICAL for s in signals) or frozen:
            overall = SafetyLevel.CRITICAL
        elif any(s.level == SafetyLevel.ELEVATED for s in signals):
            overall = SafetyLevel.ELEVATED

        return SafetyState(
            overall_level=overall,
            clamp_action=action,
            active_signals=signals,
            frozen=frozen,
            timestamp_ns=now,
        )

    def act(self, state: SafetyState) -> SafetyCircuitDecision:
        now = time.time_ns()
        triggered_rules = []

        # Determine which rules caused the action
        for rule in self.rules:
            if rule.predicate(state.active_signals):
                triggered_rules.append(rule.name)

        decision = SafetyCircuitDecision(
            previous_action=self._last_action,
            current_action=state.clamp_action,
            triggered_rules=triggered_rules,
            active_signals_count=len(state.active_signals),
            timestamp_ns=now,
        )

        # Apply clamp to admission controller
        self.admission_controller.set_clamp(state.clamp_action)

        # Apply freeze if needed
        if state.clamp_action == ClampAction.FREEZE and not self.freeze_controller.is_frozen():
            self.freeze_controller.freeze(
                reason="safety_circuit_multi_critical",
                evidence=f"signals:{len(state.active_signals)}",
            )

        # Unfreeze only on explicit healthy state and no active critical signals
        if self._last_action == ClampAction.FREEZE and state.clamp_action != ClampAction.FREEZE:
            if not any(s.level == SafetyLevel.CRITICAL for s in state.active_signals):
                self.freeze_controller.unfreeze(
                    reason="safety_circuit_healthy",
                    operator_id="safety_circuit_coordinator",
                )

        self._last_action = state.clamp_action
        self._last_action_ns = now

        self.zarc_journal.append(
            "safety_circuit_decision",
            {
                "previous_action": decision.previous_action.value,
                "current_action": decision.current_action.value,
                "triggered_rules": triggered_rules,
                "active_signals_count": decision.active_signals_count,
                "timestamp_ns": now,
            },
        )

        return decision

    def run_cycle(self) -> SafetyCircuitDecision:
        state = self.sample()
        return self.act(state)

    def _in_cooldown(self, rule: ClampRule) -> bool:
        if rule.cooldown_ns == 0:
            return False
        return (time.time_ns() - self._last_action_ns) < rule.cooldown_ns
```

---

## 7. HTTP interface (`src/egregore/http_api/http/v1/safety.py`)

```python
from fastapi import APIRouter, Depends
from egregore.application.safety_circuit_coordinator import SafetyCircuitCoordinator

router = APIRouter(prefix="/api/v1/safety", tags=["safety"])

def get_coordinator() -> SafetyCircuitCoordinator:
    from egregore.interface.bootstrap import app_state
    return app_state.safety_circuit_coordinator

@router.post("/cycle")
def run_cycle(coordinator: SafetyCircuitCoordinator = Depends(get_coordinator)):
    decision = coordinator.run_cycle()
    return {
        "previous_action": decision.previous_action.value,
        "current_action": decision.current_action.value,
        "triggered_rules": decision.triggered_rules,
        "active_signals_count": decision.active_signals_count,
    }

@router.get("/state")
def safety_state(coordinator: SafetyCircuitCoordinator = Depends(get_coordinator)):
    state = coordinator.sample()
    return {
        "overall_level": state.overall_level.value,
        "clamp_action": state.clamp_action.value,
        "frozen": state.frozen,
        "active_signals": [
            {
                "source": s.source,
                "level": s.level.value,
                "reason": s.reason,
                "timestamp_ns": s.timestamp_ns,
            }
            for s in state.active_signals
        ],
    }
```

---

## 8. Infrastructure adapters

Adapters wrap the existing controllers. Example for `FreezeController`:

```python
from egregore.application.ports.safety_circuit_ports import FreezeControllerPort
from egregore.shared.freeze_state import FreezeController

class FreezeControllerAdapter(FreezeControllerPort):
    def __init__(self, controller: FreezeController):
        self.controller = controller

    def is_frozen(self) -> bool:
        return self.controller.is_frozen
    def freeze(self, reason: str, evidence: str) -> None:
        self.controller.freeze(reason=reason, detection_source="safety_circuit", evidence=evidence)
    def unfreeze(self, reason: str, operator_id: str) -> None:
        self.controller.unfreeze(reason=reason, operator_id=operator_id)
```

Similar adapters wrap `CircuitBreaker`, `ThermalGovernorService`, `LoadRegulator`, and `AdmissionController`.

---

## 9. Bootstrap wiring

In `src/egregore/interface/bootstrap.py`:

```python
from egregore.application.safety_circuit_coordinator import SafetyCircuitCoordinator
from egregore.domain.safety_circuit import DEFAULT_RULES
from egregore.infrastructure.safety_circuit_adapters import (
    FreezeControllerAdapter,
    CircuitBreakerAdapter,
    ThermalMonitorAdapter,
    LoadMonitorAdapter,
    AdmissionControllerAdapter,
    ZarcJournalAdapter,
)

def _build_safety_circuit(app_state) -> SafetyCircuitCoordinator:
    return SafetyCircuitCoordinator(
        freeze_controller=FreezeControllerAdapter(app_state.freeze_controller),
        circuit_breakers=[CircuitBreakerAdapter(b) for b in app_state.circuit_breakers],
        thermal_monitor=ThermalMonitorAdapter(app_state.thermal_governor_service),
        load_monitor=LoadMonitorAdapter(app_state.load_regulator),
        admission_controller=AdmissionControllerAdapter(app_state.admission_controller),
        zarc_journal=ZarcJournalAdapter(app_state.zarc_journal),
        rules=DEFAULT_RULES,
    )
```

---

## 10. Testing strategy

- **Domain tests:** Rule evaluation with synthetic signal lists; hysteresis behavior.
- **Application tests:** Fake monitors; verify clamp escalation and freeze/unfreeze side effects; verify `.zarc` events emitted.
- **Integration tests:** Run coordinator against real `FreezeController`, `LoadRegulator`, and stub thermal monitor.
- **Architecture-policy tests:** Ensure `application/` does not import `powertrain/` or `shared/freeze_state.py` directly; all access goes through ports/adapters.

---

## 11. Open questions

1. Should the coordinator run on a background loop, or only when `/cycle` is invoked?
2. Should circuit-breaker indices be stable identifiers, or should each breaker have a named source?
3. What is the exact cooldown time? 5 seconds? 30 seconds? Should it vary by rule?
4. How does the coordinator interact with `EscalationService.open()` — does it call it, or is the escalation service a separate observer of the same signals?
5. Should admission-controller clamping reject *all* new work, or only non-critical verticals?
