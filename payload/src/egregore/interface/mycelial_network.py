"""
interface/mycelial_network.py

Mycelial Network — Inter-Species Communication Protocol.

The mycelial network connects all species on the crust without allowing
any species to access the mantle or core. It provides:
- Message routing between agencies
- Circular charge transfer (information flow, not energy)
- No direct access to mantle or core
- Decay-based message expiration (messages die if not consumed)

Metaphor: Like fungal mycelium connecting trees in a forest — information
flows between species, but no tree roots reach the bedrock.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Protocol, runtime_checkable

from egregore.domain.agency_taxonomy import AgencyId


class MessageType(Enum):
    ALERT = auto()  # Threat detection, defensive coordination
    THEORY = auto()  # Academic findings, model updates
    GOSSIP = auto()  # Intelligence, surveillance data
    LABOR = auto()  # Productive work delegation
    ART = auto()  # Useless/aesthetic transmissions


@dataclass(frozen=True)
class MycelialMessage:
    """A message traveling through the mycelial network."""

    message_id: str
    sender: AgencyId
    recipients: list[AgencyId]
    message_type: MessageType
    payload: Any
    timestamp_ns: int
    ttl_hops: int = 5  # Time-to-live in hops — messages decay
    charge_units: int = 1  # Circular charge transfer units


@runtime_checkable
class IMycelialNetwork(Protocol):
    """Mycelial network protocol."""

    def send(self, message: MycelialMessage) -> bool: ...
    def receive(self, agency_id: AgencyId) -> list[MycelialMessage]: ...
    def route(self, message: MycelialMessage) -> list[AgencyId]: ...
    def decay(
        self, current_time_ns: int
    ) -> int: ...  # Returns number of expired messages


class MycelialMesh:
    """
    Concrete mycelial network implementation.

    Implements circular charge transfer:
    - Messages carry charge_units
    - Each hop consumes 1 charge_unit
    - When charge_units == 0, message dies (becomes sediment)
    - No message ever reaches the mantle or core
    """

    def __init__(self, node_id: str = "pioneer1") -> None:
        self._node_id = node_id
        self._inboxes: dict[str, list[MycelialMessage]] = {}
        self._transit: list[MycelialMessage] = []
        self._expired: list[MycelialMessage] = []
        self._stats = {"sent": 0, "received": 0, "expired": 0, "charge_transferred": 0}

    def send(self, message: MycelialMessage) -> bool:
        if message.ttl_hops <= 0 or message.charge_units <= 0:
            self._expired.append(message)
            self._stats["expired"] += 1
            return False

        # Route to recipients
        routed = self.route(message)
        for recipient in routed:
            inbox = self._inboxes.setdefault(recipient.raw, [])
            inbox.append(message)

        self._transit.append(message)
        self._stats["sent"] += 1
        self._stats["charge_transferred"] += message.charge_units
        return True

    def receive(self, agency_id: AgencyId) -> list[MycelialMessage]:
        inbox = self._inboxes.get(agency_id.raw, [])
        messages = list(inbox)
        inbox.clear()
        self._stats["received"] += len(messages)
        return messages

    def route(self, message: MycelialMessage) -> list[AgencyId]:
        # Simple routing: direct recipients only
        # Future: add mesh routing, gossip protocols, species-specific broadcast
        return [r for r in message.recipients if r.species != message.sender.species]

    def decay(self, current_time_ns: int) -> int:
        """Remove expired messages from transit. Returns count."""
        expired_count = 0
        remaining = []
        for msg in self._transit:
            # Messages expire after 60 seconds in transit
            if current_time_ns - msg.timestamp_ns > 60 * 1e9:
                self._expired.append(msg)
                expired_count += 1
                self._stats["expired"] += 1
            else:
                remaining.append(msg)
        self._transit = remaining
        return expired_count

    def get_stats(self) -> dict[str, Any]:
        return dict(self._stats)
