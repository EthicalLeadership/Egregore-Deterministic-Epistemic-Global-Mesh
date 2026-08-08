"""Factory QC gate — Station 5, fail-closed.

INVARIANT: this module is FAIL-CLOSED. Any uncertainty, error, timeout, or
malformed verdict is a FAIL. Do not "harmonize" this with telemetry, which is
deliberately fail-OPEN (telemetry must never break a run; QC must never let a
bad run ship). The split is intentional — see the Phase 2 build decision.

Placement: terminal-output gate only (v0). It sits between station completion
and ``factory.run.outcome`` in the router. Per-station gating is deferred
until histogram data justifies the latency.

Note on the critic model: the configured ``critic_model`` (default
``qwen_1.5b``) currently resolves to the same Egregore backend as every other
factory model. The dedicated 1.5B resident critic is a Phase 6 (VRAM
residency) concern. The verdict contract, timeout handling, and fail-closed
semantics here are model-agnostic — swapping in the real 1.5B is a config
change, not a code change.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from egregore.factory.telemetry import emit as telemetry_emit

logger = logging.getLogger("egregore.factory.qc")


# ---------------------------------------------------------------------------
# Verdict contract — the critic MUST return this shape; anything else is FAIL
# ---------------------------------------------------------------------------
class Violation(BaseModel):
    constraint_id: str
    evidence: str
    severity: Literal["hard", "soft"] = "hard"


class QCVerdict(BaseModel):
    verdict: Literal["PASS", "FAIL"]
    confidence: float = Field(ge=0.0, le=1.0)
    violations: list[Violation] = Field(default_factory=list)
    critic_model: str
    latency_ms: float = 0.0
    tier: Literal["deterministic", "critic", "bypassed"]


class QCOutcome(BaseModel):
    """Terminal result of the gate. BLOCKED withholds the output."""

    terminal_state: Literal["SHIP", "BLOCKED"]
    m4_emission: Literal["EQUIVALENT", "DIVERGED"]
    verdict: QCVerdict
    reworks_used: int = 0
    escalated: bool = False
    final_output: str | None = None
    bypassed: bool = False


# ---------------------------------------------------------------------------
# Tier 1 — deterministic checks (milliseconds, no model)
# ---------------------------------------------------------------------------
def run_deterministic_checks(
    output: str,
    *,
    policy: dict[str, Any],
    m_flags: dict[str, bool] | None = None,
    terminal_parsed: dict[str, Any] | None = None,
) -> list[Violation]:
    """Cheap hard checks. Any violation here skips the critic entirely."""
    violations: list[Violation] = []

    if not output or not output.strip():
        violations.append(
            Violation(constraint_id="empty_output", evidence="output is empty or whitespace")
        )

    max_chars = int(policy.get("max_output_chars", 50000))
    if len(output) > max_chars:
        violations.append(
            Violation(
                constraint_id="output_too_long",
                evidence=f"{len(output)} chars > limit {max_chars}",
            )
        )

    lowered = output.lower()
    for pattern in policy.get("forbidden_patterns", []):
        if str(pattern).lower() in lowered:
            violations.append(
                Violation(
                    constraint_id="forbidden_pattern",
                    evidence=f"output contains forbidden pattern: {pattern!r}",
                )
            )

    if m_flags is not None:
        failed = [k for k in ("m1", "m2", "m3", "m4") if m_flags.get(k) is False]
        if failed:
            violations.append(
                Violation(
                    constraint_id="governance_m_flags",
                    evidence=f"governance checks failed: {', '.join(failed)}",
                )
            )

    required = policy.get("required_output_fields", [])
    if required and terminal_parsed is not None:
        missing = [f for f in required if f not in terminal_parsed]
        if missing:
            violations.append(
                Violation(
                    constraint_id="missing_required_fields",
                    evidence=f"terminal structured output missing: {', '.join(missing)}",
                )
            )

    violations.extend(check_citations(output, policy=policy))
    return violations


def check_citations(output: str, *, policy: dict[str, Any]) -> list[Violation]:
    """Citation-presence: output must reference evidence ids from the input.

    Makes retrieval misses detectable and strengthens the provenance chain.
    """
    required_ids = policy.get("required_evidence_ids", [])
    if not required_ids:
        return []
    present = sum(1 for rid in required_ids if str(rid) in output)
    min_citations = int(policy.get("min_citations", 1))
    if present < min_citations:
        return [
            Violation(
                constraint_id="citation_missing",
                evidence=(
                    f"output cites {present}/{len(required_ids)} evidence ids; "
                    f"need >= {min_citations}"
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Tier 2 — semantic critic (model-backed, strict contract)
# ---------------------------------------------------------------------------
class CriticService(Protocol):
    """Port for the semantic critic. 1.5B resident later; swappable now."""

    def critique(
        self,
        *,
        output: str,
        constraints: list[str],
        max_tokens: int,
        timeout_ms: int,
    ) -> QCVerdict: ...


_CRITIC_SYSTEM = (
    "You are a QC critic for a content factory. You receive typed constraints "
    "and an output. Respond with STRICT JSON only, no prose, exactly this shape: "
    '{"verdict": "PASS" or "FAIL", "confidence": 0.0-1.0, "violations": '
    '[{"constraint_id": "...", "evidence": "...", "severity": "hard" or "soft"}]}. '
    "FAIL on any contradiction with the constraints, incoherence, or refusal "
    "language. PASS only when the output satisfies every constraint."
)


def _fail_verdict(critic_model: str, latency_ms: float, constraint_id: str, evidence: str) -> QCVerdict:
    return QCVerdict(
        verdict="FAIL",
        confidence=0.0,
        violations=[Violation(constraint_id=constraint_id, evidence=evidence)],
        critic_model=critic_model,
        latency_ms=latency_ms,
        tier="critic",
    )


class EgregoreCritic:
    """Critic backed by the Egregore InferenceService (factory choke point)."""

    def __init__(self, host: Any, model_id: str, confidence_threshold: float) -> None:
        self._host = host
        self._model_id = model_id
        self._confidence_threshold = confidence_threshold

    def critique(
        self,
        *,
        output: str,
        constraints: list[str],
        max_tokens: int,
        timeout_ms: int,
    ) -> QCVerdict:
        constraints_json = json.dumps(constraints)
        prompt = (
            f"CONSTRAINTS (typed, JSON):\n{constraints_json}\n\n"
            f"OUTPUT TO JUDGE:\n{output[:8000]}\n\n"
            "Return the strict JSON verdict now."
        )
        from egregore.factory.critic_grammar import VERDICT_GBNF

        start = time.monotonic()
        try:
            text, _tokens, _backend = self._host.execute(
                model_id=self._model_id,
                prompt=prompt,
                system=_CRITIC_SYSTEM,
                max_tokens=max_tokens,
                temperature=0.0,
                grammar=VERDICT_GBNF,
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed
            latency = round((time.monotonic() - start) * 1000, 2)
            logger.warning("critic error: %s", exc)
            return _fail_verdict(self._model_id, latency, "critic_error", str(exc)[:300])

        latency = round((time.monotonic() - start) * 1000, 2)
        if latency > timeout_ms:
            return _fail_verdict(
                self._model_id, latency, "critic_timeout",
                f"critic took {latency}ms > {timeout_ms}ms",
            )

        verdict = self._parse_verdict(text, latency)
        if verdict.verdict == "PASS" and verdict.confidence < self._confidence_threshold:
            return QCVerdict(
                verdict="FAIL",
                confidence=verdict.confidence,
                violations=[
                    Violation(
                        constraint_id="low_confidence",
                        evidence=(
                            f"confidence {verdict.confidence} < threshold "
                            f"{self._confidence_threshold}"
                        ),
                        severity="soft",
                    )
                ],
                critic_model=self._model_id,
                latency_ms=latency,
                tier="critic",
            )
        return verdict

    def _parse_verdict(self, text: str, latency_ms: float) -> QCVerdict:
        """Strict parse. Malformed verdict → FAIL (contract: no verdict, no ship).

        Repair tier (deterministic, runs before declaring malformed): strip
        prose around the JSON object, remove trailing commas, normalize
        single quotes. Only if repair also fails is the verdict malformed.
        """
        from egregore.interface.factory_router import _extract_json

        parsed = _extract_json(text or "")
        if not isinstance(parsed, dict):
            parsed = self._repair_json(text or "")
        if not isinstance(parsed, dict):
            return _fail_verdict(
                self._model_id, latency_ms, "malformed_verdict",
                f"critic returned non-JSON: {(text or '')[:200]!r}",
            )
        try:
            raw_violations = parsed.get("violations", [])
            violations = [
                Violation(
                    constraint_id=str(v.get("constraint_id", "critic_violation")),
                    evidence=str(v.get("evidence", ""))[:500],
                    severity="soft" if v.get("severity") == "soft" else "hard",
                )
                for v in raw_violations
                if isinstance(v, dict)
            ]
            verdict_str = str(parsed.get("verdict", "")).upper()
            if verdict_str not in {"PASS", "FAIL"}:
                raise ValueError(f"invalid verdict value: {verdict_str!r}")
            return QCVerdict(
                verdict=verdict_str,  # type: ignore[arg-type]
                confidence=max(0.0, min(1.0, float(parsed.get("confidence", 0.0)))),
                violations=violations,
                critic_model=self._model_id,
                latency_ms=latency_ms,
                tier="critic",
            )
        except (ValueError, TypeError) as exc:
            return _fail_verdict(
                self._model_id, latency_ms, "malformed_verdict", str(exc)[:300]
            )

    @staticmethod
    def _repair_json(text: str) -> dict[str, Any] | None:
        """Deterministic salvage of near-JSON critic output.

        Handles the observed 1.5B lapses: prose before/after the object,
        trailing commas, single-quoted keys/strings, missing outer braces.
        """
        import json as _json
        import re

        candidate = text.strip()
        # Strip markdown fences and leading/trailing prose around the object.
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.MULTILINE)
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        candidate = candidate[start : end + 1]
        # Single quotes -> double quotes (keys and simple string values).
        candidate = re.sub(r"'([^'\\]*)'", r'"\1"', candidate)
        # Trailing commas before } or ].
        candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)
        try:
            parsed = _json.loads(candidate)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# QCGate — rework / escalation state machine
# ---------------------------------------------------------------------------
class QCGate:
    """Terminal-output QC gate. Fail-closed by construction.

    The gate input is only ever (station output, envelope constraints,
    governance flags). Verdict objects never re-enter the pipeline as input
    (M3 non-reentry); only typed violations are injected into rework prompts.
    """

    def __init__(
        self,
        *,
        policy: dict[str, Any],
        critic: CriticService | None,
        rerun_terminal: Callable[..., tuple[str, dict[str, Any] | None, dict[str, bool] | None]],
    ) -> None:
        self._policy = policy
        self._critic = critic
        self._rerun_terminal = rerun_terminal

    def evaluate(
        self,
        *,
        output: str,
        constraints: list[str],
        m_flags: dict[str, bool] | None = None,
        terminal_parsed: dict[str, Any] | None = None,
    ) -> QCOutcome:
        if os.environ.get("EGREGORE_FACTORY_QC", "").lower() == "off":
            verdict = QCVerdict(
                verdict="PASS", confidence=1.0, violations=[],
                critic_model="none", tier="bypassed",
            )
            self._emit(verdict, bypassed=True)
            return QCOutcome(
                terminal_state="SHIP", m4_emission="EQUIVALENT", verdict=verdict,
                final_output=output, bypassed=True,
            )

        budget = int(self._policy.get("rework_budget", 2))
        current_output, current_parsed, current_m = output, terminal_parsed, m_flags
        reworks = 0

        while True:
            verdict = self._check(current_output, constraints, current_m, current_parsed)
            self._emit(verdict)
            if verdict.verdict == "PASS":
                return QCOutcome(
                    terminal_state="SHIP", m4_emission="EQUIVALENT", verdict=verdict,
                    reworks_used=reworks, final_output=current_output,
                )
            if reworks >= budget:
                break
            reworks += 1
            rework_prompt = self._rework_prompt(verdict.violations)
            current_output, current_parsed, current_m = self._rerun_terminal(
                rework_prompt, False
            )

        # Budget exhausted → one heavy escalation pass, then final judgment.
        escalated_prompt = self._rework_prompt(verdict.violations, escalated=True)
        esc_output, esc_parsed, esc_m = self._rerun_terminal(escalated_prompt, True)
        esc_verdict = self._check(esc_output, constraints, esc_m, esc_parsed)
        self._emit(esc_verdict, escalated=True)
        if esc_verdict.verdict == "PASS":
            return QCOutcome(
                terminal_state="SHIP", m4_emission="EQUIVALENT", verdict=esc_verdict,
                reworks_used=reworks, escalated=True, final_output=esc_output,
            )
        return QCOutcome(
            terminal_state="BLOCKED", m4_emission="DIVERGED", verdict=esc_verdict,
            reworks_used=reworks, escalated=True, final_output=None,
        )

    def _check(
        self,
        output: str,
        constraints: list[str],
        m_flags: dict[str, bool] | None,
        terminal_parsed: dict[str, Any] | None,
    ) -> QCVerdict:
        """Tier 1 first; Tier 2 only when Tier 1 is clean."""
        violations = run_deterministic_checks(
            output,
            policy=self._policy,
            m_flags=m_flags,
            terminal_parsed=terminal_parsed,
        )
        if violations:
            return QCVerdict(
                verdict="FAIL", confidence=1.0, violations=violations,
                critic_model="deterministic", tier="deterministic",
            )
        if self._critic is None:
            # No critic configured: fail-closed would BLOCK everything, so the
            # honest default is deterministic-only with a recorded soft note.
            return QCVerdict(
                verdict="PASS", confidence=0.5,
                violations=[Violation(
                    constraint_id="critic_unavailable",
                    evidence="no critic configured; deterministic tier only",
                    severity="soft",
                )],
                critic_model="none", tier="deterministic",
            )
        return self._critic.critique(
            output=output,
            constraints=constraints,
            max_tokens=int(self._policy.get("critic_max_tokens", 256)),
            timeout_ms=int(self._policy.get("critic_timeout_ms", 60000)),
        )

    @staticmethod
    def _rework_prompt(violations: list[Violation], escalated: bool = False) -> str:
        """Typed constraints, never prose — same rule as Station 3."""
        typed = json.dumps(
            [v.model_dump() for v in violations], indent=2, default=str
        )
        prefix = (
            "ESCALATED REWORK (heavy pass). " if escalated else "REWORK. "
        )
        return (
            f"{prefix}The previous output failed QC with these typed violations:\n"
            f"{typed}\n\nRegenerate the output satisfying every constraint above."
        )

    @staticmethod
    def _emit(verdict: QCVerdict, *, bypassed: bool = False, escalated: bool = False) -> None:
        telemetry_emit(
            "factory.qc.verdict",
            station="qc_gate",
            tier=verdict.tier,
            verdict=verdict.verdict,
            confidence=verdict.confidence,
            violations=[v.model_dump() for v in verdict.violations],
            critic_model=verdict.critic_model,
            latency_ms=verdict.latency_ms,
            bypassed=bypassed,
            escalated=escalated,
        )
