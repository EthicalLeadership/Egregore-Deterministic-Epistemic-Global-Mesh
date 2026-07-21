"""
BLACKSTAR LAW: Inter-Node Messenger
NATS-based messaging for distributed scheduler coordination.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from egregore.domain.node_profile import NodeProfile
from egregore.domain.work_unit import WorkUnit
from egregore.shared.canonical import canonical_dumps


class MessengerError(Exception):
    pass


class NodeUnavailableError(MessengerError):
    pass


@dataclass
class InterNodeMessenger:
    """
    Abstract messaging layer. Concrete NATS adapter injects publish/subscribe.
    """

    node_id: str
    _publish: Callable[[str, bytes], None] | None = None
    _subscriptions: dict[str, list[Callable[[bytes], None]]] = field(
        default_factory=dict
    )

    def register_publish(self, publish_fn: Callable[[str, bytes], None]) -> None:
        self._publish = publish_fn

    def subscribe(self, topic: str, handler: Callable[[bytes], None]) -> None:
        self._subscriptions.setdefault(topic, []).append(handler)

    def broadcast_profile(self, profile: NodeProfile) -> None:
        if self._publish is None:
            raise MessengerError("No publish function registered")
        payload = canonical_dumps(profile.to_canonical()).encode("utf-8")
        self._publish(f"egregore.nodes.{self.node_id}.profile", payload)

    def dispatch_work_unit(self, target_node_id: str, work_unit: WorkUnit) -> None:
        if self._publish is None:
            raise MessengerError("No publish function registered")
        payload = canonical_dumps(
            {
                "work_unit_id": work_unit.work_unit_id,
                "work_unit_type": work_unit.work_unit_type.name,
                "demand": {
                    "dt": work_unit.demand.dt.to_canonical(),
                    "tu": work_unit.demand.tu.to_canonical(),
                },
                "source_node": self.node_id,
            }
        ).encode("utf-8")
        self._publish(f"egregore.nodes.{target_node_id}.dispatch", payload)

    def send_heartbeat(self, timestamp_ns: int) -> None:
        if self._publish is None:
            raise MessengerError("No publish function registered")
        payload = canonical_dumps(
            {
                "node_id": self.node_id,
                "timestamp_ns": timestamp_ns,
            }
        ).encode("utf-8")
        self._publish(f"egregore.nodes.{self.node_id}.heartbeat", payload)

    def publish(self, topic: str, payload: bytes) -> None:
        if self._publish is None:
            raise MessengerError("No publish function registered")
        self._publish(topic, payload)

    def handle_message(self, topic: str, payload: bytes) -> None:
        for handler in self._subscriptions.get(topic, []):
            handler(payload)
