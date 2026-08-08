"""
CBI-0 (Constraint Binding Interface — Checkpoint 0)
Four fail-closed governance checkpoints: M1, M2, M3, M4
All checkpoints fail-closed: violation = execution halt.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, auto
from typing import Any


class CheckpointStatus(Enum):
    PASS = auto()
    FAIL = auto()
    BLOCKED = auto()


class AuditResult(Enum):
    EQUIVALENT = auto()
    DIVERGED = auto()
    UNVERIFIED = auto()


@dataclass(frozen=True)
class Violation:
    checkpoint: str
    rule: str
    detail: str
    timestamp_ns: int


@dataclass
class CheckpointReport:
    checkpoint: str
    status: CheckpointStatus
    violations: list[Violation] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_blocked(self) -> bool:
        return self.status == CheckpointStatus.BLOCKED


class M1ProjectionAccess:
    def __init__(self):
        self._declared_scopes: dict[str, set[str]] = {}
        self._access_log: list[dict] = []

    def declare_scope(self, projection_id: str, allowed_paths: set[str]) -> str:
        self._declared_scopes[projection_id] = allowed_paths
        token = hashlib.sha256(
            f"{projection_id}:{sorted(allowed_paths)}".encode()
        ).hexdigest()[:16]
        return token

    def check_access(
        self, projection_id: str, requested_path: str, scope_token: str
    ) -> CheckpointReport:
        violations = []
        ts = int(datetime.now(UTC).timestamp() * 1_000_000_000)

        if projection_id not in self._declared_scopes:
            violations.append(
                Violation(
                    "M1",
                    "UNDECLARED_PROJECTION",
                    f"Projection '{projection_id}' has no declared scope",
                    ts,
                )
            )
            return CheckpointReport("M1", CheckpointStatus.BLOCKED, violations)

        allowed = self._declared_scopes[projection_id]
        if requested_path not in allowed:
            violations.append(
                Violation(
                    "M1",
                    "SCOPE_VIOLATION",
                    f"Path '{requested_path}' not in declared scope for '{projection_id}'",
                    ts,
                )
            )
            return CheckpointReport("M1", CheckpointStatus.BLOCKED, violations)

        expected_token = hashlib.sha256(
            f"{projection_id}:{sorted(allowed)}".encode()
        ).hexdigest()[:16]
        if scope_token != expected_token:
            violations.append(
                Violation(
                    "M1",
                    "TOKEN_MISMATCH",
                    "Scope token does not match declared scope",
                    ts,
                )
            )
            return CheckpointReport("M1", CheckpointStatus.BLOCKED, violations)

        self._access_log.append(
            {
                "projection_id": projection_id,
                "path": requested_path,
                "token": scope_token,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        return CheckpointReport(
            "M1", CheckpointStatus.PASS, [], {"access_granted": requested_path}
        )


class M2RegistryCompleteness:
    def __init__(self):
        self._registry: dict[str, list[str]] = {}
        self._overlap_classifications: dict[str, str] = {}

    def register(
        self, port: str, adapter_id: str, overlap_resolution: str | None = None
    ) -> CheckpointReport:
        if port not in self._registry:
            self._registry[port] = []
        self._registry[port].append(adapter_id)
        ts = int(datetime.now(UTC).timestamp() * 1_000_000_000)

        if len(self._registry[port]) > 1:
            if overlap_resolution is None:
                return CheckpointReport(
                    "M2",
                    CheckpointStatus.BLOCKED,
                    [
                        Violation(
                            "M2",
                            "UNRESOLVED_OVERLAP",
                            f"Port '{port}' has multiple adapters but no resolution strategy",
                            ts,
                        )
                    ],
                )
            self._overlap_classifications[port] = overlap_resolution

        return CheckpointReport(
            "M2",
            CheckpointStatus.PASS,
            [],
            {
                "port": port,
                "adapter": adapter_id,
                "adapters_count": len(self._registry[port]),
            },
        )

    def verify_complete(self, required_ports: set[str]) -> CheckpointReport:
        missing = required_ports - set(self._registry.keys())
        ts = int(datetime.now(UTC).timestamp() * 1_000_000_000)
        if missing:
            return CheckpointReport(
                "M2",
                CheckpointStatus.BLOCKED,
                [
                    Violation(
                        "M2",
                        "INCOMPLETE_REGISTRY",
                        f"Missing adapters for ports: {missing}",
                        ts,
                    )
                ],
            )
        return CheckpointReport(
            "M2",
            CheckpointStatus.PASS,
            [],
            {"registered_ports": list(self._registry.keys())},
        )


class M3TerminalNonReentry:
    DERIVATIVE_MARKER = "__PLANE2_DERIVATIVE__"

    def __init__(self):
        self._derivative_outputs: set[str] = set()
        self._block_log: list[dict] = []

    def mark_derivative(self, output_id: str, source_trace: str) -> str:
        marked = f"{output_id}{self.DERIVATIVE_MARKER}{source_trace}"
        self._derivative_outputs.add(marked)
        return marked

    def check_input(self, input_id: str) -> CheckpointReport:
        ts = int(datetime.now(UTC).timestamp() * 1_000_000_000)
        if self.DERIVATIVE_MARKER in input_id:
            self._block_log.append(
                {
                    "blocked_input": input_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
            return CheckpointReport(
                "M3",
                CheckpointStatus.BLOCKED,
                [
                    Violation(
                        "M3",
                        "DERIVATIVE_REENTRY",
                        f"Attempted to re-enter derivative '{input_id}' into Core Plane",
                        ts,
                    )
                ],
            )
        return CheckpointReport(
            "M3", CheckpointStatus.PASS, [], {"input_verified": input_id}
        )


class M4SpecRuntimeEquivalence:
    def __init__(self, halt_on_divergence: bool = True):
        self.halt_on_divergence = halt_on_divergence
        self._audit_log: list[dict] = []

    def canonical_json(self, obj: Any) -> str:
        from egregore.shared.canonical import canonical_dumps

        return canonical_dumps(obj)

    def audit(
        self, execution_id: str, spec_state: Any, runtime_state: Any
    ) -> CheckpointReport:
        spec_hash = hashlib.sha256(self.canonical_json(spec_state).encode()).hexdigest()
        runtime_hash = hashlib.sha256(
            self.canonical_json(runtime_state).encode()
        ).hexdigest()
        result = (
            AuditResult.EQUIVALENT
            if spec_hash == runtime_hash
            else AuditResult.DIVERGED
        )
        ts = int(datetime.now(UTC).timestamp() * 1_000_000_000)

        record = {
            "execution_id": execution_id,
            "spec_hash": spec_hash,
            "runtime_hash": runtime_hash,
            "result": result.name,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self._audit_log.append(record)

        if result == AuditResult.DIVERGED and self.halt_on_divergence:
            return CheckpointReport(
                "M4",
                CheckpointStatus.BLOCKED,
                [
                    Violation(
                        "M4",
                        "SPEC_RUNTIME_DIVERGENCE",
                        f"Spec hash {spec_hash[:16]}... != Runtime hash {runtime_hash[:16]}...",
                        ts,
                    )
                ],
                record,
            )

        return CheckpointReport("M4", CheckpointStatus.PASS, [], record)


class CBI0Governance:
    def __init__(self, halt_on_divergence: bool = True):
        self.m1 = M1ProjectionAccess()
        self.m2 = M2RegistryCompleteness()
        self.m3 = M3TerminalNonReentry()
        self.m4 = M4SpecRuntimeEquivalence(halt_on_divergence)

    def execute(self, operation: Callable, context: dict[str, Any]) -> Any:
        required_ports = context.get("required_ports", set())
        m2_report = self.m2.verify_complete(required_ports)
        if m2_report.is_blocked():
            raise CBI0BlockedError(m2_report)

        if "projection_access" in context:
            pa = context["projection_access"]
            m1_report = self.m1.check_access(
                pa["projection_id"], pa["path"], pa["scope_token"]
            )
            if m1_report.is_blocked():
                raise CBI0BlockedError(m1_report)

        for input_id in context.get("inputs", []):
            m3_report = self.m3.check_input(input_id)
            if m3_report.is_blocked():
                raise CBI0BlockedError(m3_report)

        result = operation()

        if "spec_state" in context and "runtime_state" in context:
            m4_report = self.m4.audit(
                context.get("execution_id", "unknown"),
                context["spec_state"],
                context["runtime_state"],
            )
            if m4_report.is_blocked():
                raise CBI0BlockedError(m4_report)

        return result

    def full_report(self) -> dict[str, Any]:
        return {
            "m1_access_log_count": len(self.m1._access_log),
            "m2_registered_ports": list(self.m2._registry.keys()),
            "m3_block_count": len(self.m3._block_log),
            "m4_audit_count": len(self.m4._audit_log),
            "m4_diverged_count": sum(
                1 for r in self.m4._audit_log if r["result"] == "DIVERGED"
            ),
        }


class CBI0BlockedError(Exception):
    def __init__(self, report: CheckpointReport):
        self.report = report
        super().__init__(
            f"CBI-0 BLOCKED: {report.checkpoint} — {report.violations[0].rule}"
        )


__all__ = [
    "CBI0Governance",
    "CBI0BlockedError",
    "M1ProjectionAccess",
    "M2RegistryCompleteness",
    "M3TerminalNonReentry",
    "M4SpecRuntimeEquivalence",
    "CheckpointReport",
    "CheckpointStatus",
    "Violation",
    "AuditResult",
]
