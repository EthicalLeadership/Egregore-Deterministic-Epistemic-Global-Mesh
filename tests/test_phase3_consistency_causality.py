"""Tests for Phase 3: Consistency and Causality.

Validates per-(org, case) ordering, monotonic versioning,
causality_id presence, and causal reconstruction.
"""

import pytest

from egregore.application.consistency_and_causality import (
    CausalityContext,
    CausalityReconstructor,
    CausalityViolationError,
    ConsistencyAndCausalityEnforcer,
)


def test_causality_context_is_frozen():
    """CausalityContext must be immutable."""
    context = CausalityContext(
        organization_id="org-1",
        case_id="case-1",
        causality_id="cid-1",
        version_number=1,
        version_id="v1",
    )

    # Should not be able to modify
    with pytest.raises(AttributeError):
        context.version_number = 2


def test_enforcer_validates_monotonic_versioning():
    """Enforcer must enforce monotonic version increase per (org, case)."""
    enforcer = ConsistencyAndCausalityEnforcer()

    context1 = CausalityContext(
        organization_id="org-1",
        case_id="case-1",
        causality_id="cid-1",
        version_number=1,
        version_id="v1",
    )

    # First version should pass
    enforcer.validate_causality_context(context=context1)

    # Second version with same (org, case) must be higher
    context2 = CausalityContext(
        organization_id="org-1",
        case_id="case-1",
        causality_id="cid-2",
        version_number=2,
        version_id="v2",
    )

    enforcer.validate_causality_context(context=context2)

    # Third version with lower number should fail
    context3 = CausalityContext(
        organization_id="org-1",
        case_id="case-1",
        causality_id="cid-3",
        version_number=1,  # Lower than 2!
        version_id="v3",
    )

    with pytest.raises(CausalityViolationError, match="Version number must increase"):
        enforcer.validate_causality_context(context=context3)


def test_enforcer_independent_per_org_case():
    """Enforcer must track version watermarks independently per (org, case)."""
    enforcer = ConsistencyAndCausalityEnforcer()

    # Org-1, Case-1: version 1
    context1 = CausalityContext(
        organization_id="org-1",
        case_id="case-1",
        causality_id="cid-1",
        version_number=1,
        version_id="v1",
    )
    enforcer.validate_causality_context(context=context1)

    # Org-2, Case-1: version 1 (different org, should be allowed)
    context2 = CausalityContext(
        organization_id="org-2",
        case_id="case-1",
        causality_id="cid-2",
        version_number=1,
        version_id="v1",
    )
    enforcer.validate_causality_context(context=context2)

    # Org-1, Case-2: version 1 (different case, should be allowed)
    context3 = CausalityContext(
        organization_id="org-1",
        case_id="case-2",
        causality_id="cid-3",
        version_number=1,
        version_id="v1",
    )
    enforcer.validate_causality_context(context=context3)


def test_enforcer_rejects_duplicate_version():
    """Enforcer must reject equal version numbers (not just <=)."""
    enforcer = ConsistencyAndCausalityEnforcer()

    context1 = CausalityContext(
        organization_id="org-1",
        case_id="case-1",
        causality_id="cid-1",
        version_number=5,
        version_id="v1",
    )
    enforcer.validate_causality_context(context=context1)

    # Same version number should fail
    context2 = CausalityContext(
        organization_id="org-1",
        case_id="case-1",
        causality_id="cid-2",
        version_number=5,  # Same as context1
        version_id="v2",
    )

    with pytest.raises(CausalityViolationError):
        enforcer.validate_causality_context(context=context2)


def test_enforcer_validates_event_causality_id():
    """Enforcer must verify causality_id is present in events."""
    enforcer = ConsistencyAndCausalityEnforcer()

    # Event with causality_id
    event_good = {"causality_id": "cid-1", "data": "test"}
    enforcer.validate_event_causality(
        event=event_good,
        expected_causality_id="cid-1",
        expected_version_number=1,
    )

    # Event without causality_id
    event_bad = {"data": "test"}
    with pytest.raises(CausalityViolationError, match="missing required causality_id"):
        enforcer.validate_event_causality(
            event=event_bad,
            expected_causality_id="cid-1",
            expected_version_number=1,
        )


def test_enforcer_validates_causality_id_matches():
    """Enforcer must verify causality_id value matches expected."""
    enforcer = ConsistencyAndCausalityEnforcer()

    event = {"causality_id": "cid-wrong", "data": "test"}

    with pytest.raises(CausalityViolationError, match="causality_id mismatch"):
        enforcer.validate_event_causality(
            event=event,
            expected_causality_id="cid-expected",
            expected_version_number=1,
        )


def test_enforcer_validates_version_number_in_event():
    """Enforcer must verify version_number in events when present."""
    enforcer = ConsistencyAndCausalityEnforcer()

    event = {"causality_id": "cid-1", "version_number": 99, "data": "test"}

    with pytest.raises(CausalityViolationError, match="version_number mismatch"):
        enforcer.validate_event_causality(
            event=event,
            expected_causality_id="cid-1",
            expected_version_number=1,
        )


def test_enforcer_enforces_ordering_in_event_sequence():
    """Enforcer must validate monotonic ordering across event sequences."""
    enforcer = ConsistencyAndCausalityEnforcer()

    events = [
        {"version_number": 1, "causality_id": "cid-1", "event_id": "e1"},
        {"version_number": 2, "causality_id": "cid-1", "event_id": "e2"},
        {"version_number": 3, "causality_id": "cid-1", "event_id": "e3"},
    ]

    # Should pass for monotonic sequence
    enforcer.enforce_causality_ordering(
        organization_id="org-1",
        case_id="case-1",
        events=events,
    )


def test_enforcer_rejects_non_monotonic_sequence():
    """Enforcer must reject non-monotonic event sequences."""
    enforcer = ConsistencyAndCausalityEnforcer()

    events = [
        {"version_number": 1, "causality_id": "cid-1", "event_id": "e1"},
        {"version_number": 3, "causality_id": "cid-1", "event_id": "e2"},
        {
            "version_number": 2,
            "causality_id": "cid-1",
            "event_id": "e3",
        },  # Out of order!
    ]

    with pytest.raises(CausalityViolationError, match="version non-monotonic"):
        enforcer.enforce_causality_ordering(
            organization_id="org-1",
            case_id="case-1",
            events=events,
        )


def test_enforcer_rejects_missing_version_in_event():
    """Enforcer must reject events with missing version_number."""
    enforcer = ConsistencyAndCausalityEnforcer()

    events = [
        {"version_number": 1, "causality_id": "cid-1", "event_id": "e1"},
        {"causality_id": "cid-1", "event_id": "e2"},  # Missing version_number!
    ]

    with pytest.raises(CausalityViolationError, match="missing version_number"):
        enforcer.enforce_causality_ordering(
            organization_id="org-1",
            case_id="case-1",
            events=events,
        )


def test_enforcer_rejects_missing_causality_id_in_sequence():
    """Enforcer must reject events with missing causality_id in sequence."""
    enforcer = ConsistencyAndCausalityEnforcer()

    events = [
        {"version_number": 1, "causality_id": "cid-1", "event_id": "e1"},
        {"version_number": 2, "event_id": "e2"},  # Missing causality_id!
    ]

    with pytest.raises(CausalityViolationError, match="missing causality_id"):
        enforcer.enforce_causality_ordering(
            organization_id="org-1",
            case_id="case-1",
            events=events,
        )


def test_enforcer_empty_sequence_is_valid():
    """Enforcer must accept empty event sequences."""
    enforcer = ConsistencyAndCausalityEnforcer()

    enforcer.enforce_causality_ordering(
        organization_id="org-1",
        case_id="case-1",
        events=[],
    )


def test_enforcer_reset_watermarks():
    """Enforcer must support watermark reset (for replay scenarios)."""
    enforcer = ConsistencyAndCausalityEnforcer()

    # Set watermark
    context1 = CausalityContext(
        organization_id="org-1",
        case_id="case-1",
        causality_id="cid-1",
        version_number=10,
        version_id="v1",
    )
    enforcer.validate_causality_context(context=context1)

    # Reset
    enforcer.reset_watermarks()

    # Now version 1 should be allowed again
    context2 = CausalityContext(
        organization_id="org-1",
        case_id="case-1",
        causality_id="cid-2",
        version_number=1,
        version_id="v1",
    )
    enforcer.validate_causality_context(context=context2)


def test_causality_reconstructor_extracts_chains():
    """Reconstructor must extract causality chains from events."""
    events = [
        {"event_id": "e1", "causality_id": "cid-1", "data": "a"},
        {"event_id": "e2", "causality_id": "cid-1", "data": "b"},
        {"event_id": "e3", "causality_id": "cid-2", "data": "c"},
        {"event_id": "e4", "causality_id": "cid-1", "data": "d"},
    ]

    chains = CausalityReconstructor.reconstruct_from_events(events)

    assert chains["cid-1"] == ["e1", "e2", "e4"]
    assert chains["cid-2"] == ["e3"]


def test_causality_reconstructor_ignores_missing_ids():
    """Reconstructor must skip events with missing causality_id or event_id."""
    events = [
        {"event_id": "e1", "causality_id": "cid-1"},
        {"event_id": "e2"},  # Missing causality_id
        {"causality_id": "cid-1"},  # Missing event_id
        {"event_id": "e3", "causality_id": "cid-1"},
    ]

    chains = CausalityReconstructor.reconstruct_from_events(events)

    assert chains["cid-1"] == ["e1", "e3"]


def test_causality_reconstructor_verifies_chain_integrity():
    """Reconstructor must verify chain sequences match."""
    chain = ["e1", "e2", "e3"]
    expected = ["e1", "e2", "e3"]

    assert CausalityReconstructor.verify_causality_chain_integrity(chain, expected)


def test_causality_reconstructor_detects_chain_divergence():
    """Reconstructor must detect chain divergence."""
    chain = ["e1", "e2", "e3"]
    expected = ["e1", "e2", "e4"]  # Different final event

    assert not CausalityReconstructor.verify_causality_chain_integrity(chain, expected)
