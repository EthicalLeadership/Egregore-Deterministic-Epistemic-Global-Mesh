"""Tests for the constrained semantic engine (audit-named engine test)."""

from __future__ import annotations

import pytest

from egregore.application.constrained_semantic_engine import (
    ConstrainedSemanticEngine,
    SemanticCandidate,
)


def _candidate(text: str, confidence: float = 1.0) -> SemanticCandidate:
    return SemanticCandidate(
        raw_text=text,
        normalized_text=text,
        confidence=confidence,
        metadata={"source": "test"},
    )


def test_engine_collapse_is_deterministic() -> None:
    engine = ConstrainedSemanticEngine(fallback_mode="safe_fallback")
    candidates = [
        _candidate("May indicate: contract terms were not honored."),
        _candidate("May indicate: contract terms were not honored."),
    ]
    result = engine.collapse(candidates)
    assert result.admissible is True
    assert result.fallback_used is False
    assert result.classification == "semantic_projection"
    assert result.candidate_count == 2


def test_engine_drops_forbidden_legal_phrasing() -> None:
    engine = ConstrainedSemanticEngine(fallback_mode="safe_fallback")
    candidates = [
        _candidate("May indicate: this establishes liability."),
        _candidate("May indicate: observable facts support review."),
    ]
    result = engine.collapse(candidates)
    assert "establishes liability" not in result.canonical_text.lower()
    assert result.forbidden_dropped_count == 1
    assert result.admissible is True


def test_engine_strict_mode_raises_when_all_forbidden() -> None:
    engine = ConstrainedSemanticEngine(fallback_mode="strict")
    candidates = [
        _candidate("This proves wrongdoing beyond doubt."),
        _candidate("This establishes liability."),
    ]
    with pytest.raises(ValueError, match="No admissible semantic candidates"):
        engine.collapse(candidates)


def test_engine_safe_fallback_when_all_forbidden() -> None:
    engine = ConstrainedSemanticEngine(fallback_mode="safe_fallback")
    candidates = [
        _candidate("This proves wrongdoing beyond doubt."),
    ]
    result = engine.collapse(candidates)
    assert result.fallback_used is True
    assert result.classification == "semantic_projection_fallback"
    assert "legal conclusion" not in result.canonical_text.lower()


def test_engine_invalid_fallback_mode_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown fallback_mode"):
        ConstrainedSemanticEngine(fallback_mode="invalid")  # type: ignore[arg-type]
