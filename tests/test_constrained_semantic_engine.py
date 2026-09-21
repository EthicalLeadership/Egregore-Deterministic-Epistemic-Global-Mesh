from __future__ import annotations

import pytest

from egregore.application.constrained_semantic_engine import (
    ConstrainedSemanticEngine,
    SemanticCandidate,
)


def _cand(text: str) -> SemanticCandidate:
    return SemanticCandidate(
        raw_text=text,
        normalized_text=text,
        confidence=1.0,
        metadata={},
    )


def test_cse_idempotent_same_candidates() -> None:
    cse = ConstrainedSemanticEngine(fallback_mode="safe_fallback")
    candidates = [
        _cand("May indicate: The notes suggest a contract was breached."),
        _cand("May indicate: the notes suggest a contract was breached."),
    ]

    r1 = cse.collapse(candidates)
    r2 = cse.collapse(list(reversed(candidates)))

    assert r1.canonical_text == r2.canonical_text
    assert r1.semantic_hash == r2.semantic_hash
    assert r1.admissible is True
    assert r1.classification == "semantic_projection"
    assert r1.fallback_used is False


def test_cse_variance_invariant_whitespace_and_case() -> None:
    cse = ConstrainedSemanticEngine(fallback_mode="safe_fallback")
    candidates = [
        _cand("May   indicate:   Paris   is  the  capital ."),
        _cand("  may indicate: paris is the capital.  "),
    ]

    r = cse.collapse(candidates)

    assert r.canonical_text == "may indicate: paris is the capital .".lower()
    assert r.admissible is True
    assert r.semantic_hash == cse.collapse(candidates).semantic_hash
    assert r.fallback_used is False


def test_cse_strict_reject_path_when_all_candidates_contain_forbidden_legal_phrasing() -> (
    None
):
    cse = ConstrainedSemanticEngine(fallback_mode="strict")
    candidates = [
        _cand("May indicate: This establishes liability based on the notes."),
        _cand("May indicate: The evidence proves wrongdoing."),
    ]

    with pytest.raises(
        ValueError, match="No admissible semantic candidates after normalization"
    ):
        cse.collapse(candidates)


def test_cse_safe_fallback_when_all_candidates_contain_forbidden_legal_phrasing() -> (
    None
):
    cse = ConstrainedSemanticEngine(fallback_mode="safe_fallback")
    candidates = [
        _cand("May indicate: This establishes liability based on the notes."),
        _cand("May indicate: The evidence proves wrongdoing."),
    ]

    r = cse.collapse(candidates)
    assert r.admissible is True
    assert r.fallback_used is True
    assert r.forbidden_dropped_count == 2
    assert r.candidate_count == 2
    assert "legal conclusion" not in r.canonical_text.lower()
    assert r.classification == "semantic_projection_fallback"


def test_cse_strict_reject_only_drops_forbidden_candidates_if_any_safe_candidate_remains() -> (
    None
):
    cse = ConstrainedSemanticEngine(fallback_mode="strict")
    candidates = [
        _cand("May indicate: This establishes liability based on the notes."),
        _cand(
            "May indicate: The notes describe relevant facts supporting further review."
        ),
    ]

    r = cse.collapse(candidates)
    assert "establishes liability" not in r.canonical_text.lower()
    assert r.admissible is True
    assert r.fallback_used is False
