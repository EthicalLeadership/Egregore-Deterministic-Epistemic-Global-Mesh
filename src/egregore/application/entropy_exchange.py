"""
Entropy signal exchange and constitutional threshold monitoring.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import asdict
from typing import TYPE_CHECKING

from egregore.application.escalation_service import EscalationService
from egregore.domain.federation_constitution import (
    Constitution,
    EntropySignal,
    EscalationLevel,
)
from egregore.shared.canonical import canonical_dumps, canonical_loads

if TYPE_CHECKING:
    from egregore.infrastructure.inter_node_messenger import InterNodeMessenger


class EntropyExchange:
    """
    Publishes/subscribes entropy signals across federation nodes and triggers
    escalations when constitutional thresholds are breached.
    """

    TOPIC = "entropy.signals"

    def __init__(
        self,
        node_id: str,
        constitution: Constitution,
        escalation_service: EscalationService,
        messenger: InterNodeMessenger | None = None,
    ) -> None:
        self._node_id = node_id
        self._constitution = constitution
        self._escalation = escalation_service
        self._messenger = messenger
        self._signals: dict[str, EntropySignal] = {}
        if self._messenger is not None:
            self._messenger.subscribe(self.TOPIC, self._on_signal)

    def publish(
        self, signal_type: str, value: float, confidence: float = 1.0
    ) -> EntropySignal:
        signal = EntropySignal(
            source_node_id=self._node_id,
            signal_type=signal_type,
            value=value,
            confidence=confidence,
            timestamp_ns=time.time_ns(),
            signature="",  # Signature injected by caller/signing layer if required.
        )
        if self._messenger is not None:
            payload = canonical_dumps(asdict(signal)).encode("utf-8")
            self._messenger.publish(self.TOPIC, payload)
        else:
            self._signals[self._node_id] = signal
        return signal

    def receive(self, signal: EntropySignal) -> None:
        self._signals[signal.source_node_id] = signal
        self._evaluate()

    def _on_signal(self, payload: bytes) -> None:
        data = canonical_loads(payload.decode("utf-8"))
        signal = EntropySignal(**data)
        self.receive(signal)

    def latest_signals(self) -> list[EntropySignal]:
        ttl_ns = (
            int(self._constitution.entropy_config.get("signal_ttl_seconds", 300))
            * 1_000_000_000
        )
        now = time.time_ns()
        return [s for s in self._signals.values() if now - s.timestamp_ns <= ttl_ns]

    def aggregate(self) -> float | None:
        signals = self.latest_signals()
        if not signals:
            return None
        values = [s.value for s in signals]
        if len(values) == 1:
            return values[0]
        median = statistics.median(values)
        stdev = statistics.stdev(values) if len(values) > 1 else 0.0
        z = self._constitution.entropy_config.get("aggregation", {}).get(
            "outlier_z_score", 2.0
        )
        filtered = [v for v in values if stdev == 0 or abs(v - median) <= z * stdev]
        if not filtered:
            filtered = values
        return statistics.median(filtered)

    def _evaluate(self) -> None:
        cfg = self._constitution.entropy_config
        min_nodes = cfg.get("aggregation", {}).get("min_participating_nodes", 2)
        signals = self.latest_signals()
        if len(signals) < min_nodes:
            return
        aggregated = self.aggregate()
        if aggregated is None:
            return
        highest: EscalationLevel | None = None
        for threshold in cfg.get("thresholds", []):
            comparator = threshold.get("comparator")
            value = float(threshold.get("value", 0))
            matched = (comparator == "gt" and aggregated > value) or (
                comparator == "gte" and aggregated >= value
            )
            if matched:
                level = EscalationLevel(threshold.get("escalation_level", "WARNING"))
                if highest is None or _level_rank(level) > _level_rank(highest):
                    highest = level
        if highest is not None:
            nodes = sorted({s.source_node_id for s in signals})
            self._escalation.open(
                level=highest,
                trigger=f"entropy_{highest.value.lower()}: aggregated={aggregated:.4f}",
                affected_nodes=nodes,
                evidence_hashes=[
                    f"entropy:{s.timestamp_ns}:{s.source_node_id}" for s in signals
                ],
            )


def _level_rank(level: EscalationLevel) -> int:
    return {
        EscalationLevel.WARNING: 1,
        EscalationLevel.CRITICAL: 2,
        EscalationLevel.OVERRIDE: 3,
    }.get(level, 0)
