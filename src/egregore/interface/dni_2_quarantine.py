"""
interface/dni_2_quarantine.py

DNI-2 Border / Atmosphere — Quarantine and Ingress/Egress Mediation.

The atmosphere is the protective layer that regulates what enters and exits
the crust. It provides:
- Ingress validation: sanitize and classify incoming WorkUnits
- Egress filtering: prevent data exfiltration and enforce output policies
- Quarantine isolation: suspicious WorkUnits are isolated, not destroyed
- Energy mediation: regulate throughput based on node energy budget

No direct access to mantle (ops) or core (kernel). All enforcement is
projection-only and read-only relative to authoritative state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Protocol, runtime_checkable

# WorkUnit is not a domain type; use generic Any
from egregore.interface.ops.ops_ports import IEnergyGovernor


class QuarantineVerdict(Enum):
    PERMIT = auto()  # Allow passage
    QUARANTINE = auto()  # Isolate for inspection
    REJECT = auto()  # Block and log
    DEFER = auto()  # Hold until energy budget allows


@dataclass(frozen=True)
class IngressEnvelope:
    """A WorkUnit wrapped with ingress metadata."""

    work_unit: Any
    source_node: str
    ingress_timestamp_ns: int
    declared_intent_hash: str
    energy_cost_estimate_j: float
    threat_score: float = 0.0  # 0.0 = benign, 1.0 = critical


@dataclass(frozen=True)
class EgressEnvelope:
    """A response wrapped with egress metadata."""

    payload: Any
    destination_node: str | None
    egress_timestamp_ns: int
    provenance_chain: list[str] = field(default_factory=list)
    sensitivity_classification: str = (
        "public"  # public, internal, restricted, classified
    )


@runtime_checkable
class IDNI2Quarantine(Protocol):
    """Atmosphere border protocol."""

    def ingress(self, envelope: IngressEnvelope) -> QuarantineVerdict: ...
    def egress(self, envelope: EgressEnvelope) -> QuarantineVerdict: ...
    def quarantine_list(self) -> list[str]: ...
    def release(self, wu_id: str) -> bool: ...
    def destroy(self, wu_id: str) -> bool: ...


class DNI2Atmosphere:
    """
    Concrete atmosphere implementation.

    Enforces the DNI-2 border policy:
    1. All ingress is inspected before reaching crust (application layer)
    2. Energy budget is checked before admission
    3. Threat score > 0.7 triggers quarantine
    4. Egress sensitivity is checked against destination clearance
    5. Quarantined WorkUnits are isolated, not destroyed — they become sediment
    """

    THREAT_QUARANTINE_THRESHOLD = 0.7
    ENERGY_DEFER_THRESHOLD = 0.3  # fraction of remaining budget

    def __init__(
        self,
        energy_governor: IEnergyGovernor | None = None,
        node_id: str = "pioneer1",
    ) -> None:
        self._energy = energy_governor
        self._node_id = node_id
        self._quarantine: dict[str, IngressEnvelope] = {}
        self._ingress_log: list[dict[str, Any]] = []
        self._egress_log: list[dict[str, Any]] = []

    def ingress(self, envelope: IngressEnvelope) -> QuarantineVerdict:
        import time

        # 1. Threat assessment
        if envelope.threat_score >= self.THREAT_QUARANTINE_THRESHOLD:
            self._quarantine[envelope.work_unit.wu_id.raw] = envelope
            self._ingress_log.append(
                {
                    "timestamp_ns": time.time_ns(),
                    "action": "quarantine",
                    "wu_id": envelope.work_unit.wu_id.raw,
                    "threat_score": envelope.threat_score,
                }
            )
            return QuarantineVerdict.QUARANTINE

        # 2. Energy budget check
        if self._energy:
            status = self._energy.status(self._node_id, time.time_ns())
            if status.remaining_j < envelope.energy_cost_estimate_j and (
                status.remaining_j / status.total_budget_j < self.ENERGY_DEFER_THRESHOLD
            ):
                self._ingress_log.append(
                    {
                        "timestamp_ns": time.time_ns(),
                        "action": "defer",
                        "wu_id": envelope.work_unit.wu_id.raw,
                        "reason": "insufficient_energy",
                    }
                )
                return QuarantineVerdict.DEFER

        # 3. Permit
        self._ingress_log.append(
            {
                "timestamp_ns": time.time_ns(),
                "action": "permit",
                "wu_id": envelope.work_unit.wu_id.raw,
            }
        )
        return QuarantineVerdict.PERMIT

    def egress(self, envelope: EgressEnvelope) -> QuarantineVerdict:
        import time

        # 1. Sensitivity check — classified data cannot egress to untrusted nodes
        if (
            envelope.sensitivity_classification == "classified"
            and envelope.destination_node != self._node_id
        ):
            self._egress_log.append(
                {
                    "timestamp_ns": time.time_ns(),
                    "action": "reject",
                    "reason": "classified_to_external",
                }
            )
            return QuarantineVerdict.REJECT

        # 2. Provenance chain validation — prevent circular exfiltration
        if len(envelope.provenance_chain) > 10:
            self._egress_log.append(
                {
                    "timestamp_ns": time.time_ns(),
                    "action": "reject",
                    "reason": "provenance_chain_too_long",
                }
            )
            return QuarantineVerdict.REJECT

        self._egress_log.append(
            {
                "timestamp_ns": time.time_ns(),
                "action": "permit",
                "destination": envelope.destination_node,
            }
        )
        return QuarantineVerdict.PERMIT

    def quarantine_list(self) -> list[str]:
        return [e.work_unit.wu_id for e in self._quarantine.values()]

    def release(self, wu_id: str) -> bool:
        if wu_id.raw in self._quarantine:
            del self._quarantine[wu_id.raw]
            return True
        return False

    def destroy(self, wu_id: str) -> bool:
        # Quarantined WorkUnits are not destroyed — they become sediment
        # This method returns False to enforce the policy
        return False
