from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from egregore.application.dossier_generate_service import DossierGenerateRequest
from egregore.domain.semantics_models import CommandAck
from egregore.shared.canonical import canonical_dumps, sha256_hex


def build_deterministic_request(
    *, message: str, session_id: str
) -> DossierGenerateRequest:
    """
    Deterministically map WebSocket message context -> DossierGenerateRequest.

    Kept in application layer so the HTTP/transport layer can remain free of
    disallowed cross-layer imports (notably `egregore.shared`).
    """
    return DossierGenerateRequest(
        organization_id="anchorum-chat",
        case_id=session_id,
        actor_id="chat_user",
        input_fingerprint=sha256_hex(message.encode("utf-8")),
        engine_version="chat_v1",
        policy_version="chat_policy_v1",
        input_payload={"input": message},
        causality_id=f"chat_{session_id}",
        request_id=None,
        timestamp_ns=None,
    )


def dumps_ws_result(*, ack: CommandAck) -> str:
    """
    Serialize a CommandAck into a deterministic, canonical JSON string.
    """
    return canonical_dumps(
        {
            "type": "result",
            "http_status": ack.http_status,
            "data": ack.result.data,
            "outbox_ids": ack.outbox_ids,
            "version_id": ack.result.version_id,
            "version_number": ack.result.version_number,
        }
    )


def dumps_ws_error(*, error: str) -> str:
    """
    Serialize an error payload into a deterministic, canonical JSON string.
    """
    return canonical_dumps({"type": "error", "error": error})


def dumps_chat_result(*, payload: dict[str, Any]) -> str:
    """
    Serialize a chat interpreter result envelope into a canonical JSON string.
    """
    return canonical_dumps(payload)


def dumps_chat_error(*, command: str, error: str) -> str:
    """
    Serialize a chat-level error into a canonical JSON envelope.
    """
    return canonical_dumps(
        {
            "type": "chat",
            "command": command,
            "ok": False,
            "summary": f"Error: {error}",
            "detail": None,
        }
    )


@dataclass(frozen=True)
class WebSocketTransportConfig:
    """Immutable configuration for WebSocket transport mapping."""

    endpoint: str
    protocol: str = "wss"
    reconnect_interval: float = 5.0
    max_reconnect_attempts: int = 10
    heartbeat_interval: float = 30.0
    timeout: float = 10.0


class WebSocketTransportMapper:
    """
    Maps chat transport events to WebSocket protocol handlers.
    Pure Python. No JavaScript execution.
    """

    def __init__(self, config: WebSocketTransportConfig):
        self.config = config
        self._handlers: dict[str, Callable] = {}
        self._connected = False

    def register_handler(self, event_type: str, handler: Callable) -> None:
        self._handlers[event_type] = handler

    def map_inbound(self, raw_message: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw_message, dict):
            raise ValueError("Inbound message must be a dict.")
        sanitized = self._sanitize_payload(raw_message)
        return {
            "type": sanitized.get("type", "unknown"),
            "payload": sanitized.get("payload", {}),
            "timestamp": sanitized.get("timestamp"),
            "transport": "websocket",
            "sanitized": True,
        }

    def map_outbound(self, internal_event: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": internal_event.get("type"),
            "payload": self._sanitize_payload(internal_event.get("payload", {})),
            "seq": internal_event.get("seq"),
        }

    def _sanitize_payload(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            return {
                k: self._sanitize_payload(v)
                for k, v in payload.items()
                if k not in ("__proto__", "constructor", "prototype")
                and not (
                    isinstance(v, str)
                    and v.strip().startswith(
                        ("javascript:", "data:text/html", "<script")
                    )
                )
            }
        if isinstance(payload, list):
            return [self._sanitize_payload(item) for item in payload]
        if isinstance(payload, str) and (
            "<script" in payload.lower() or "javascript:" in payload.lower()
        ):
            return "[SANITIZED_ACTIVE_CONTENT]"
        return payload
