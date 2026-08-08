"""
Egregore Operator Dashboard — Plane 2 Projection Service

Reads freeze state, audit logs, and system health.
All mutations route through the canonical FreezeController (Plane 1).

Fail-closed: any missing dependency or auth gap raises loud exceptions.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from egregore.shared.canonical import canonical_loads


class SystemStatus(StrEnum):
    NORMAL = "NORMAL"
    FROZEN = "FROZEN"
    UNFROZEN = "UNFROZEN"


class KeyHealth(StrEnum):
    HEALTHY = "HEALTHY"
    SHORT = "SHORT"
    MISSING = "MISSING"
    EXPIRED = "EXPIRED"


class CiStatus(StrEnum):
    PASS = "PASS"  # noqa: S105
    FAIL = "FAIL"
    RUNNING = "RUNNING"
    UNKNOWN = "UNKNOWN"


@dataclasses.dataclass(frozen=True)
class FreezeEvent:
    timestamp: datetime
    operator: str
    action: str
    reason: str
    previous_state: str
    new_state: str
    event_id: str


@dataclasses.dataclass(frozen=True)
class KeyHealthReport:
    health: KeyHealth
    key_length: int
    min_required: int
    rotation_due: bool
    rotation_days_remaining: int | None
    last_rotated: datetime | None


@dataclasses.dataclass(frozen=True)
class CiHealthReport:
    status: CiStatus
    last_run: datetime | None
    lint_ok: bool
    type_ok: bool
    security_ok: bool
    summary: str


@dataclasses.dataclass(frozen=True)
class DashboardState:
    system_status: SystemStatus
    freeze_events: list[FreezeEvent]
    key_health: KeyHealthReport
    ci_health: CiHealthReport
    node_id: str
    timestamp: datetime


class FreezeControllerPort(Protocol):
    def get_status(self) -> str: ...
    def get_audit_log(self, limit: int = 100) -> list[dict[str, Any]]: ...


class AuthContextPort(Protocol):
    operator_id: str
    roles: set[str]


class DashboardService:
    """
    Projection service for the operator dashboard.
    Rules:
    1. Never write to Plane 1 state directly.
    2. All mutations go through FreezeControllerPort.
    3. Missing controller or auth context = loud crash (fail-closed).
    4. All timestamps are UTC.
    """

    _MIN_KEY_LENGTH: int = 32
    _KEY_ROTATION_DAYS: int = 90

    def __init__(
        self,
        *,
        freeze_controller: FreezeControllerPort,
        auth_context: AuthContextPort,
        node_id: str,
        ci_status_path: Path | None = None,
        key_metadata_path: Path | None = None,
    ) -> None:
        if freeze_controller is None:
            raise RuntimeError(
                "DashboardService: freeze_controller required (fail-closed)"
            )
        if auth_context is None:
            raise RuntimeError("DashboardService: auth_context required (fail-closed)")

        self._controller = freeze_controller
        self._auth = auth_context
        self._node_id = node_id
        self._ci_status_path = ci_status_path or Path("ci_status.json")
        self._key_metadata_path = key_metadata_path or Path("key_metadata.json")

    def get_dashboard_state(self) -> DashboardState:
        return DashboardState(
            system_status=self._resolve_status(),
            freeze_events=self._load_freeze_events(),
            key_health=self._check_key_health(),
            ci_health=self._check_ci_health(),
            node_id=self._node_id,
            timestamp=datetime.fromtimestamp(time.time_ns() / 1e9, tz=UTC),
        )

    def _resolve_status(self) -> SystemStatus:
        raw = self._controller.get_status()
        try:
            return SystemStatus(raw.upper())
        except ValueError:
            return SystemStatus.FROZEN

    def _load_freeze_events(self, limit: int = 50) -> list[FreezeEvent]:
        raw_events = self._controller.get_audit_log(limit=limit)
        events: list[FreezeEvent] = []
        for e in raw_events:
            with contextlib.suppress(Exception):
                ts_str = e.get("timestamp") or e.get("timestamp_ns")
                if isinstance(ts_str, int):
                    ts = datetime.fromtimestamp(ts_str / 1e9, tz=UTC)
                else:
                    ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                events.append(
                    FreezeEvent(
                        timestamp=ts,
                        operator=e.get("operator", "UNKNOWN"),
                        action=e.get("action", "UNKNOWN"),
                        reason=e.get("reason", ""),
                        previous_state=e.get("previous_state", "UNKNOWN"),
                        new_state=e.get("new_state", "UNKNOWN"),
                        event_id=e.get("event_id", "UNKNOWN"),
                    )
                )
        return events

    def _check_key_health(self) -> KeyHealthReport:
        key_len = 0
        last_rotated = None

        if self._key_metadata_path.exists():
            with contextlib.suppress(Exception):
                data = canonical_loads(self._key_metadata_path.read_text())
                key_len = data.get("key_length", 0)
                last_rotated_str = data.get("last_rotated")
                if last_rotated_str:
                    last_rotated = datetime.fromisoformat(
                        last_rotated_str.replace("Z", "+00:00")
                    )
        else:
            for env_name in ("EGREGORE_API_KEY", "EG_API_KEY", "API_KEY"):
                val = os.environ.get(env_name)
                if val:
                    key_len = len(val)
                    break

        if key_len == 0:
            health = KeyHealth.MISSING
        elif key_len < self._MIN_KEY_LENGTH:
            health = KeyHealth.SHORT
        else:
            health = KeyHealth.HEALTHY

        rotation_due = False
        days_remaining = None
        if last_rotated:
            age = (datetime.fromtimestamp(time.time_ns() / 1e9, tz=UTC) - last_rotated).days
            days_remaining = max(0, self._KEY_ROTATION_DAYS - age)
            rotation_due = age >= self._KEY_ROTATION_DAYS

        return KeyHealthReport(
            health=health,
            key_length=key_len,
            min_required=self._MIN_KEY_LENGTH,
            rotation_due=rotation_due,
            rotation_days_remaining=days_remaining,
            last_rotated=last_rotated,
        )

    def _check_ci_health(self) -> CiHealthReport:
        if not self._ci_status_path.exists():
            return CiHealthReport(
                status=CiStatus.UNKNOWN,
                last_run=None,
                lint_ok=False,
                type_ok=False,
                security_ok=False,
                summary="No CI status artifact found",
            )

        try:
            data = canonical_loads(self._ci_status_path.read_text())
            status = CiStatus(data.get("status", "UNKNOWN").upper())
            last_run_str = data.get("last_run")
            last_run = (
                datetime.fromisoformat(last_run_str.replace("Z", "+00:00"))
                if last_run_str
                else None
            )
            return CiHealthReport(
                status=status,
                last_run=last_run,
                lint_ok=data.get("lint_ok", False),
                type_ok=data.get("type_ok", False),
                security_ok=data.get("security_ok", False),
                summary=data.get("summary", "No summary"),
            )
        except Exception:
            return CiHealthReport(
                status=CiStatus.UNKNOWN,
                last_run=None,
                lint_ok=False,
                type_ok=False,
                security_ok=False,
                summary="CI status artifact is malformed",
            )

    def require_operator(self) -> str:
        op = self._auth.operator_id
        if not op:
            raise PermissionError("DashboardService: operator attribution required")
        return op

    def require_role(self, role: str) -> None:
        if role not in self._auth.roles:
            raise PermissionError(f"DashboardService: role '{role}' required")
