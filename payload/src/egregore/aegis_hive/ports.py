"""Abstract ports (interfaces) for AEGIS-HIVE Ω backends and actuators.

These ports keep the core reasoning pipeline independent of specific Linux
subsystems, allowing auditd, eBPF, iptables, nftables, or external EDR tools
to be plugged in without changing the cell logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any

from egregore.aegis_hive.schemas import (
    AegisAction,
    AegisEvent,
    AegisFinding,
    AegisIntelIndicator,
)


class ITelemetryBackend(ABC):
    """Source of normalized endpoint telemetry events."""

    @property
    @abstractmethod
    def backend_id(self) -> str:
        """Stable identifier for this backend type, e.g. 'auditd' or 'ebpf'."""

    @abstractmethod
    def start(self) -> None:
        """Start collecting telemetry."""

    @abstractmethod
    def stop(self) -> None:
        """Stop collecting telemetry."""

    @abstractmethod
    def poll(self, max_events: int = 1000) -> list[AegisEvent]:
        """Return the next batch of normalized events."""


class IActuator(ABC):
    """Executor for autonomous defensive actions."""

    @property
    @abstractmethod
    def actuator_id(self) -> str:
        """Stable identifier, e.g. 'process_killer' or 'iptables_blocker'."""

    @abstractmethod
    def can_execute(self, action: AegisAction) -> bool:
        """Return True if this actuator handles the given action type."""

    @abstractmethod
    def execute(self, action: AegisAction) -> dict[str, Any]:
        """Execute the action and return a result dict.

        The result must include at minimum ``success: bool`` and may include
        ``snapshot``, ``rollback_commands``, and ``error``.
        """

    @abstractmethod
    def rollback(self, action: AegisAction) -> dict[str, Any]:
        """Roll back a previously executed action if possible."""


class IIntelStore(ABC):
    """Storage and retrieval for threat-intelligence indicators."""

    @abstractmethod
    def ingest(self, indicators: Iterable[AegisIntelIndicator]) -> int:
        """Store indicators; return the number newly added."""

    @abstractmethod
    def match_event(self, event: AegisEvent) -> list[AegisIntelIndicator]:
        """Return indicators that match the supplied telemetry event."""

    @abstractmethod
    def decay_confidence(self, ts_ns: int) -> None:
        """Apply temporal decay to stored indicator confidence scores."""


class IReasoner(ABC):
    """Consumes events and intel and emits scored findings."""

    @abstractmethod
    def reason(
        self,
        events: list[AegisEvent],
        intel_matches: list[AegisIntelIndicator],
        context: dict[str, Any],
    ) -> list[AegisFinding]:
        """Produce findings given telemetry and threat intelligence."""
