"""Tests for DOSS-08: Governance & Compliance."""

from __future__ import annotations

import pytest

from egregore.dossiers.DOSS_08_governance_compliance.compliance import (
    AuditResult,
    CheckpointStatus,
    GovernanceCompliance,
    Violation,
)


def test_checkpoint_status_values():
    assert CheckpointStatus.PASS.name == "PASS"
    assert CheckpointStatus.FAIL.name == "FAIL"
    assert CheckpointStatus.BLOCKED.name == "BLOCKED"


def test_audit_result_values():
    assert AuditResult.EQUIVALENT.name == "EQUIVALENT"
    assert AuditResult.DIVERGED.name == "DIVERGED"
    assert AuditResult.UNVERIFIED.name == "UNVERIFIED"


def test_violation_is_immutable():
    v = Violation(checkpoint="M1", rule="test", detail="detail", timestamp_ns=123)
    assert v.checkpoint == "M1"
    with pytest.raises(AttributeError):
        v.checkpoint = "M2"


def test_governance_passes_valid_projection():
    governance = GovernanceCompliance()
    projection = {
        "projection_id": "proj-1",
        "bindings": [{"port": "in", "value": "data"}],
    }
    report = governance.validate(projection)
    assert report.status == CheckpointStatus.PASS


def test_governance_blocks_missing_projection_id():
    governance = GovernanceCompliance()
    projection = {"bindings": [{"port": "in", "value": "data"}]}
    report = governance.validate(projection)
    assert report.status == CheckpointStatus.BLOCKED
    assert any(v.checkpoint == "M1" for v in report.violations)


def test_governance_blocks_empty_bindings():
    governance = GovernanceCompliance()
    projection = {"projection_id": "proj-1"}
    report = governance.validate(projection)
    assert report.status == CheckpointStatus.BLOCKED
    assert any(v.checkpoint == "M2" for v in report.violations)


def test_governance_policy_max_depth():
    governance = GovernanceCompliance()
    governance.publish_policy("policy-1", "1.0", {"max_depth": 3})
    projection = {
        "projection_id": "proj-1",
        "bindings": [{"port": "in", "value": "data"}],
        "policy_id": "policy-1",
        "depth": 5,
    }
    report = governance.validate(projection)
    assert report.status == CheckpointStatus.BLOCKED
    assert any(v.rule == "max-depth-exceeded" for v in report.violations)


def test_governance_policy_required_fields():
    governance = GovernanceCompliance()
    governance.publish_policy(
        "policy-1", "1.0", {"required_fields": ["owner", "scope"]}
    )
    projection = {
        "projection_id": "proj-1",
        "bindings": [{"port": "in", "value": "data"}],
        "policy_id": "policy-1",
    }
    report = governance.validate(projection)
    assert report.status == CheckpointStatus.BLOCKED
    assert any(v.rule == "required-field-missing" for v in report.violations)


def test_governance_policy_passes_when_compliant():
    governance = GovernanceCompliance()
    governance.publish_policy(
        "policy-1", "1.0", {"max_depth": 5, "required_fields": ["owner"]}
    )
    projection = {
        "projection_id": "proj-1",
        "bindings": [{"port": "in", "value": "data"}],
        "policy_id": "policy-1",
        "depth": 3,
        "owner": "team-a",
    }
    report = governance.validate(projection)
    assert report.status == CheckpointStatus.PASS


def test_governance_policy_versioning():
    governance = GovernanceCompliance()
    governance.publish_policy("policy-1", "1.0", {"max_depth": 3})
    governance.publish_policy("policy-1", "1.1", {"max_depth": 5})

    policy = governance.get_policy("policy-1")
    assert policy["version"] == "1.1"

    history = governance.get_history("policy-1")
    assert len(history) == 2
    assert history[0]["version"] == "1.0"
