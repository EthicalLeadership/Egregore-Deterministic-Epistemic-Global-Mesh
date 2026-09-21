"""Unit tests for SelfRep contradiction detector."""

from __future__ import annotations

from datetime import UTC, datetime

from egregore.domain.self_rep_dossier.contradiction_detector import (
    detect_contradictions_and_corroborations,
)
from egregore.domain.self_rep_dossier.dossier_models import Claim


def _claim(text: str, actor: str, claim_type: str = "assertion") -> Claim:
    return Claim(
        claim_id=f"claim:{actor}:{hash(text) % 10000}",
        text=text,
        source_artifact_ids=(f"artifact:{actor}",),
        actor_id=actor,
        timestamp=datetime(2025, 5, 1, tzinfo=UTC),
        modality="email",
        claim_type=claim_type,
    )


def test_detects_approved_vs_denied():
    c1 = _claim(
        "The insurer approved the disability claim on 2025-05-01", "actor:insurer"
    )
    c2 = _claim(
        "The insurer denied the disability claim on 2025-05-01", "actor:employer"
    )
    contradictions, _ = detect_contradictions_and_corroborations([c1, c2])
    assert len(contradictions) == 1


def test_no_same_actor_contradiction():
    c1 = _claim("Employee is fit for work", "actor:employer")
    c2 = _claim("Employee is not fit for work", "actor:employer")
    contradictions, _ = detect_contradictions_and_corroborations([c1, c2])
    assert len(contradictions) == 0


def test_corroboration_between_independent_actors():
    c1 = _claim("Meeting occurred on 2025-05-01", "actor:employer", "assertion")
    c2 = _claim("Meeting occurred on 2025-05-01", "actor:union", "assertion")
    _, corroborations = detect_contradictions_and_corroborations([c1, c2])
    assert len(corroborations) == 1
