# epistemic marker: provenance / auditability
"""DOSS-08: Governance & Compliance — CBI-0 checkpoints and policy versioning."""

from __future__ import annotations

from dataclasses import dataclass, field
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


class GovernanceCompliance:
    """CBI-0 governance and compliance engine for Egregore."""

    def __init__(self) -> None:
        self._policies: dict[str, dict[str, Any]] = {}
        self._history: dict[str, list[dict[str, Any]]] = {}

    def validate(self, projection: dict[str, Any]) -> CheckpointReport:
        violations = []
        # Run built-in structural checks
        if not projection.get("projection_id"):
            violations.append(
                Violation(
                    checkpoint="M1",
                    rule="no-unbound-projection",
                    detail="projection_id is missing",
                    timestamp_ns=0,
                )
            )
        if not projection.get("bindings"):
            violations.append(
                Violation(
                    checkpoint="M2",
                    rule="no-empty-bindings",
                    detail="bindings are empty",
                    timestamp_ns=0,
                )
            )
        # Run policy-specific rules if a policy_id is declared
        policy_id = projection.get("policy_id")
        if policy_id and policy_id in self._policies:
            policy = self._policies[policy_id]
            rules = policy.get("rules", {})
            for rule_name, rule_spec in rules.items():
                if rule_name == "max_depth":
                    depth = projection.get("depth", 0)
                    if isinstance(rule_spec, int) and depth > rule_spec:
                        violations.append(
                            Violation(
                                checkpoint="P1",
                                rule="max-depth-exceeded",
                                detail=f"depth {depth} exceeds policy max {rule_spec}",
                                timestamp_ns=0,
                            )
                        )
                elif rule_name == "required_fields":
                    required = rule_spec if isinstance(rule_spec, list) else []
                    for field in required:
                        if field not in projection:
                            violations.append(
                                Violation(
                                    checkpoint="P2",
                                    rule="required-field-missing",
                                    detail=f"required field '{field}' missing",
                                    timestamp_ns=0,
                                )
                            )
        status = CheckpointStatus.PASS if not violations else CheckpointStatus.BLOCKED
        return CheckpointReport(
            checkpoint="CBI-0", status=status, violations=violations
        )

    def publish_policy(
        self, policy_id: str, version: str, rules: dict[str, Any]
    ) -> None:
        self._policies[policy_id] = {"version": version, "rules": rules}
        self._history.setdefault(policy_id, []).append(
            {"version": version, "rules": rules}
        )

    def get_policy(self, policy_id: str) -> dict[str, Any] | None:
        return self._policies.get(policy_id)

    def get_history(self, policy_id: str) -> list[dict[str, Any]]:
        return list(self._history.get(policy_id, []))
