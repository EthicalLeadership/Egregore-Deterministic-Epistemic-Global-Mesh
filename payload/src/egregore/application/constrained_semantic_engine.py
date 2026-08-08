from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from egregore.shared.canonical import canonical_json, sha256_hex

_FORBIDDEN_LEGAL_PHRASES: frozenset[str] = frozenset(
    {
        "establishes liability",
        "proves wrongdoing",
        "legal conclusion",
        "legally sufficient",
        "confirmed retaliation",
        "confirmed violation",
    }
)


def _normalize_whitespace(text: str) -> str:
    # Deterministic normalization only.
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def _contains_forbidden_legal_phrasing(text: str) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in _FORBIDDEN_LEGAL_PHRASES)


@dataclass(frozen=True)
class SemanticCandidate:
    raw_text: str
    normalized_text: str
    confidence: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class CanonicalSemanticResult:
    canonical_text: str
    semantic_hash: str
    classification: str
    admissible: bool

    # Observability hooks (deterministic, governance-relevant for debugging/backpressure)
    candidate_count: int
    forbidden_dropped_count: int
    fallback_used: bool


CollapseFallbackMode = Literal["strict", "safe_fallback"]


class ConstrainedSemanticEngine:
    """
    Deterministic semantic collapse layer (CSE).

    Deterministic operations:
    - normalization
    - forbidden-language rejection (drop-only)
    - canonical ordering + deterministic selection

    Failure semantics:
    - strict: raise if nothing survives forbidden filtering
    - safe_fallback: if nothing survives, use a deterministic evidence-bounded fallback
      that avoids forbidden phrases (so Layer-0 admission remains safe and non-retryable).
    """

    _SAFE_FALLBACK_TEXT = "May indicate: additional evidence review is required; no legal determination is expressed."

    def __init__(
        self, *, fallback_mode: CollapseFallbackMode = "safe_fallback"
    ) -> None:
        if fallback_mode not in {"strict", "safe_fallback"}:
            raise ValueError(f"Unknown fallback_mode={fallback_mode!r}")
        self._fallback_mode = fallback_mode

    def collapse(
        self, candidates: Iterable[SemanticCandidate]
    ) -> CanonicalSemanticResult:
        candidate_list = list(candidates)
        if not candidate_list:
            raise ValueError("No semantic candidates provided")

        normalized: list[str] = []
        forbidden_dropped_count = 0

        for c in candidate_list:
            candidate_norm = _normalize_whitespace(c.normalized_text)
            if not candidate_norm:
                continue

            if _contains_forbidden_legal_phrasing(candidate_norm):
                forbidden_dropped_count += 1
                continue

            normalized.append(candidate_norm)

        if normalized:
            unique_sorted = sorted(set(normalized))
            selected = unique_sorted[0]
            classification = "semantic_projection"
            fallback_used = False
        else:
            if self._fallback_mode == "strict":
                raise ValueError(
                    "No admissible semantic candidates after normalization"
                )

            selected = _normalize_whitespace(self._SAFE_FALLBACK_TEXT)
            classification = "semantic_projection_fallback"
            fallback_used = True

        semantic_hash = sha256_hex(canonical_json({"v": selected}).encode("utf-8"))

        return CanonicalSemanticResult(
            canonical_text=selected,
            semantic_hash=semantic_hash,
            classification=classification,
            admissible=True,
            candidate_count=len(candidate_list),
            forbidden_dropped_count=forbidden_dropped_count,
            fallback_used=fallback_used,
        )
