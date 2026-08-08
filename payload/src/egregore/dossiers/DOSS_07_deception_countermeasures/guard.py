"""DOSS-07: Deception & Countermeasures — Adversarial detection and reasoning guard."""

from __future__ import annotations

import re
from dataclasses import dataclass

_FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bestablishes\s+liability\b", re.IGNORECASE),
    re.compile(r"\bliability\b.*\bestablished\b", re.IGNORECASE),
    re.compile(r"\bproves\s+wrongdoing\b", re.IGNORECASE),
    re.compile(r"\bwrongdoing\b.*\bproven\b", re.IGNORECASE),
    re.compile(r"\blegally\s+sufficient\s+evidence\b", re.IGNORECASE),
    re.compile(r"\bignore\s+previous\s+instructions\b", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+now\s+DAN\b", re.IGNORECASE),
)


@dataclass
class GuardResult:
    blocked: bool
    reason: str = ""
    confidence: float = 0.0


class DeceptionCountermeasures:
    """Adversarial input detection and countermeasures for Egregore."""

    def __init__(self) -> None:
        self._patterns = _FORBIDDEN_PATTERNS

    def check(self, text: str) -> GuardResult:
        for pattern in self._patterns:
            if pattern.search(text):
                return GuardResult(
                    blocked=True,
                    reason=f"Pattern matched: {pattern.pattern}",
                    confidence=1.0,
                )
        return GuardResult(blocked=False, confidence=0.0)

    def normalize(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().lower()
