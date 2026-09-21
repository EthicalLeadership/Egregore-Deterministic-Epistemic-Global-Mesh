from __future__ import annotations

from egregore.domain.semantics.observability import (
    EQUIV_O,
    EvaluatorMetadata,
    ObservableEnvelope,
    PolicyRoutingOutcomeClass,
    SemanticRelevanceClass,
    admissible_extension,
    classify_semantics,
    enforce_admissible_step,
    ensure_relevance_closed_basis,
    evaluate_policy_routing,
    evaluator_metadata_rename_transform,
    is_admissible_step,
    semantically_equivalent,
    validate_envelope_contract,
)


def _envelope(
    *, engine_version: str = "engine-A", policy_version: str = "policy-A"
) -> ObservableEnvelope:
    return ObservableEnvelope(
        execution={
            "admissibility_classification": "ADMISSIBLE",
            "snapshot_hash": "h1",
            "event_ids": ["e1", "e2"],
            "event_seq": [0, 1],
            "outbox_ids": ["o1"],
        },
        case={
            "organization_id": "org_1",
            "case_id": "case_1",
            "archive_stability_classification": "NON_TERMINAL",
            "causality_attribution_class": "CAUSALITY_TRACEABLE",
        },
        policy={
            "routing_outcome_class": "ALLOW",
            "policy_level": "strict",
            "policy_version": "policy_v1",
        },
        evaluator=EvaluatorMetadata(
            engine_version=engine_version,
            policy_version=policy_version,
            projection_version="pcl-v1",
        ),
    )


def test_semantic_equivalence_is_evaluator_invariant() -> None:
    lhs = _envelope(engine_version="engine-A", policy_version="policy-A")
    rhs = _envelope(engine_version="engine-B", policy_version="policy-B")

    assert semantically_equivalent(lhs, rhs)


def test_relevance_basis_is_closed() -> None:
    ensure_relevance_closed_basis(
        [
            SemanticRelevanceClass.ADMISSIBILITY_CLASSIFICATION.value,
            SemanticRelevanceClass.ARCHIVE_STABILITY_CLASSIFICATION.value,
            SemanticRelevanceClass.POLICY_ROUTING_OUTCOME_CLASS.value,
            SemanticRelevanceClass.CAUSALITY_ATTRIBUTION_CLASS.value,
        ]
    )

    try:
        ensure_relevance_closed_basis(["unknown_property"])
        raise AssertionError("unknown relevance property must fail")
    except ValueError as exc:
        assert "unknown_property" in str(exc)


def test_policy_routing_is_observable_only() -> None:
    route = evaluate_policy_routing(policy_observable_prefix={"policy_level": "strict"})
    assert route == PolicyRoutingOutcomeClass.ALLOW

    route = evaluate_policy_routing(policy_observable_prefix={"policy_level": "review"})
    assert route == PolicyRoutingOutcomeClass.QUARANTINE

    route = evaluate_policy_routing(policy_observable_prefix={"policy_level": "deny"})
    assert route == PolicyRoutingOutcomeClass.FAIL


def test_admissible_extension_requires_observable_preservation() -> None:
    base = _envelope()
    same = _envelope(engine_version="engine-C", policy_version="policy-C")
    changed = ObservableEnvelope(
        execution={
            "admissibility_classification": "REJECTED",
            "snapshot_hash": "h2",
            "event_ids": ["e1", "e2"],
            "event_seq": [0, 1],
            "outbox_ids": ["o1"],
        },
        case=base.case,
        policy=base.policy,
        evaluator=base.evaluator,
    )

    assert admissible_extension(base=base, extended=same)
    assert not admissible_extension(base=base, extended=changed)


def test_semantic_classification_surface_is_explicit() -> None:
    classification = classify_semantics(_envelope())
    assert classification.admissibility_classification == "ADMISSIBLE"
    assert classification.archive_stability_classification == "NON_TERMINAL"
    assert (
        classification.policy_routing_outcome_class == PolicyRoutingOutcomeClass.ALLOW
    )
    assert classification.causality_attribution_class == "CAUSALITY_TRACEABLE"


def test_admissible_step_allows_semantic_neutral_variation() -> None:
    base = _envelope()
    variant = evaluator_metadata_rename_transform(base)
    assert is_admissible_step(current=base, next_envelope=variant, relation=EQUIV_O)
    enforce_admissible_step(
        current=base,
        next_envelope=variant,
        relation=EQUIV_O,
        reason="semantic-neutral evaluator metadata",
    )


def test_projection_operator_is_single_semantic_source() -> None:
    env = _envelope()
    # Relation object must agree with direct semantic equivalence function.
    assert EQUIV_O.equivalent(env, env)


def test_hidden_field_injection_is_rejected() -> None:
    base = _envelope()
    injected = ObservableEnvelope(
        execution={**base.execution, "hidden_state": "illegal"},
        case=base.case,
        policy=base.policy,
        evaluator=base.evaluator,
    )
    try:
        validate_envelope_contract(injected)
        raise AssertionError("hidden field injection must fail")
    except ValueError as exc:
        assert "Forbidden keys in envelope" in str(exc)


def test_admissible_step_detects_divergence() -> None:
    base = _envelope()
    changed = ObservableEnvelope(
        execution={**base.execution, "admissibility_classification": "REJECTED"},
        case=base.case,
        policy=base.policy,
        evaluator=base.evaluator,
    )

    try:
        enforce_admissible_step(
            current=base,
            next_envelope=changed,
            relation=EQUIV_O,
            reason="classification changed",
        )
        raise AssertionError("divergent admissibility step must fail")
    except ValueError as exc:
        assert "failed" in str(exc).lower()
