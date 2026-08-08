"""Shared Pydantic schemas for the AEGIS-HIVE Ω subsystem.

These models define the canonical shapes of telemetry events, threat-intel
artifacts, detection findings, and autonomous response actions. They are
intentionally simple for the MVP and can be extended as the mesh grows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class AegisEventCategory(StrEnum):
    """High-level category of a telemetry event."""

    PROCESS = "process"
    FILE = "file"
    NETWORK = "network"
    AUTH = "auth"
    DNS = "dns"
    REGISTRY = "registry"
    SYSTEM = "system"


class AegisEventType(StrEnum):
    """Event type within a category."""

    START = "start"
    END = "end"
    ACCESS = "access"
    MODIFY = "modify"
    CREATE = "create"
    DELETE = "delete"
    CONNECT = "connect"
    ACCEPT = "accept"
    LOGIN = "login"
    LOGOUT = "logout"


class AegisEvent(BaseModel):
    """A single normalized telemetry event from an endpoint."""

    event_id: str = Field(description="Unique event identifier (UUID7 or ULID).")
    ts_ns: int = Field(description="Event timestamp in nanoseconds since epoch.")
    host_id: str = Field(description="Stable host identifier.")
    sensor_id: str = Field(description="Sensor that produced the event.")
    category: AegisEventCategory
    event_type: AegisEventType
    action: str = Field(description="Normalized action name, e.g. 'process_started'.")
    severity: int = Field(default=0, ge=0, le=4, description="Event severity 0-4.")
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0)

    # Entity fields — only the subset relevant to the event is populated.
    process: dict[str, Any] = Field(default_factory=dict)
    file: dict[str, Any] = Field(default_factory=dict)
    network: dict[str, Any] = Field(default_factory=dict)
    identity: dict[str, Any] = Field(default_factory=dict)
    host: dict[str, Any] = Field(default_factory=dict)

    # Raw telemetry envelope for forensics and replay.
    raw: dict[str, Any] = Field(default_factory=dict)

    class Config:
        """Pydantic configuration: allow extra fields in telemetry envelopes."""

        extra = "allow"


class AegisIndicatorType(StrEnum):
    """Supported indicator types for MVP threat-intel matching."""

    IPV4 = "ipv4"
    DOMAIN = "domain"
    SHA256 = "sha256"
    MD5 = "md5"
    TECHNIQUE = "technique"
    PATTERN = "pattern"


class AegisIntelIndicator(BaseModel):
    """A single threat-intelligence indicator with temporal confidence."""

    indicator_id: str
    indicator_type: AegisIndicatorType
    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    severity: float = Field(default=0.0, ge=0.0, le=1.0)
    technique_id: str | None = None
    threat_actor: str | None = None
    campaign: str | None = None
    first_seen_ns: int
    last_seen_ns: int
    source: str
    description: str = ""


class AegisFinding(BaseModel):
    """A detection finding produced by sensor + intel + reasoner correlation."""

    finding_id: str
    ts_ns: int
    host_id: str
    title: str
    description: str = ""
    technique_id: str | None = None
    tactic: str | None = None
    severity: Literal["info", "low", "medium", "high", "critical"] = "info"
    confidence: float = Field(ge=0.0, le=1.0)
    risk_score: float = Field(ge=0.0, le=1.0)
    indicators: list[AegisIntelIndicator] = Field(default_factory=list)
    related_events: list[str] = Field(default_factory=list)
    recommended_action: str | None = None
    blast_radius: dict[str, Any] = Field(default_factory=dict)


class AegisActionTier(StrEnum):
    """Tiered response model from AEGIS-HIVE Ω Chapter 10."""

    TIER_0_OBSERVE = "tier_0_observe"
    TIER_1_HUMAN_APPROVED = "tier_1_human_approved"
    TIER_2_SEMI_AUTONOMOUS = "tier_2_semi_autonomous"
    TIER_3_FULLY_AUTONOMOUS = "tier_3_fully_autonomous"
    TIER_4_CRISIS = "tier_4_crisis"


class AegisActionStatus(StrEnum):
    """Lifecycle status of a response action."""

    PROPOSED = "proposed"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"


class AegisAction(BaseModel):
    """A proposed or executed autonomous defensive action."""

    action_id: str
    finding_id: str
    ts_ns: int
    host_id: str
    action_type: Literal[
        "observe",
        "alert",
        "kill_process",
        "isolate_host",
        "revoke_session",
        "block_ip",
        "disable_account",
        "rotate_credentials",
        "deploy_deception",
    ]
    tier: AegisActionTier
    status: AegisActionStatus = AegisActionStatus.PROPOSED
    justification: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    approval_required: bool = True
    approved_by: str | None = None
    approved_at_ns: int | None = None
    executed_at_ns: int | None = None
    completed_at_ns: int | None = None
    rollback_info: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class AegisTelemetryEnvelope(BaseModel):
    """Batch envelope used when streaming telemetry into the mesh."""

    envelope_id: str
    host_id: str
    sensor_id: str
    ts_ns: int
    events: list[AegisEvent]


def now_ns() -> int:
    """Return current UTC time in nanoseconds."""
    return int(datetime.now(UTC).timestamp() * 1_000_000_000)
