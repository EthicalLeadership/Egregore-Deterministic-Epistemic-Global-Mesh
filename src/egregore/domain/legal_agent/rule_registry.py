from __future__ import annotations

from dataclasses import dataclass

from egregore.domain.legal_agent.legal_models import LegalFact, RuleMatch


@dataclass(frozen=True)
class LegalRule:
    """A legal rule entry in the registry.

    triggers: keywords in fact content that activate this rule (substring match).
    base_confidence: [0.0, 1.0] prior confidence when rule is activated.
    """

    rule_id: str
    rule_text: str
    jurisdiction: str
    triggers: tuple[str, ...]
    base_confidence: float


class StaticRuleRegistry:
    """Phase 1 minimal in-code rule registry.

    Designed for clean substitution via IRuleRegistry Protocol — any backend
    (file-backed, database, ML-based) can replace this without modifying the pipeline.

    Trigger matching: substring scan over lowercased fact content.
    Sufficient for Phase 1 deterministic testing; extend triggers per domain need.
    """

    _RULES: tuple[LegalRule, ...] = (
        LegalRule(
            rule_id="rule_workplace_comms",
            rule_text=(
                "Electronic communications in workplace contexts may establish "
                "patterns of conduct relevant to employment claims."
            ),
            jurisdiction="general",
            triggers=("email", "message", "communication", "sent", "received"),
            base_confidence=0.75,
        ),
        LegalRule(
            rule_id="rule_document_retention",
            rule_text=(
                "Documents relevant to known or anticipated litigation must be "
                "preserved under document retention obligations."
            ),
            jurisdiction="general",
            triggers=(
                "document",
                "record",
                "retention",
                "preserve",
                "delete",
                "destroy",
            ),
            base_confidence=0.80,
        ),
        LegalRule(
            rule_id="rule_timeline_evidence",
            rule_text=(
                "Chronological sequence of events is admissible to establish "
                "pattern or course of conduct."
            ),
            jurisdiction="general",
            triggers=(
                "timeline",
                "date",
                "time",
                "sequence",
                "before",
                "after",
                "following",
            ),
            base_confidence=0.70,
        ),
        LegalRule(
            rule_id="rule_confidentiality_obligation",
            rule_text=(
                "Information marked or understood to be confidential carries "
                "corresponding non-disclosure obligations."
            ),
            jurisdiction="general",
            triggers=(
                "confidential",
                "proprietary",
                "private",
                "sensitive",
                "restricted",
            ),
            base_confidence=0.85,
        ),
        LegalRule(
            rule_id="rule_adverse_action_proximity",
            rule_text=(
                "Temporal proximity between protected activity and adverse action "
                "may be relevant to causation analysis."
            ),
            jurisdiction="general",
            triggers=(
                "termination",
                "demotion",
                "discipline",
                "complaint",
                "report",
                "retaliation",
            ),
            base_confidence=0.65,
        ),
    )

    def find_applicable(self, facts: list[LegalFact]) -> list[RuleMatch]:
        """Match rules to facts via trigger keyword presence in lowercased content.

        Returns empty list if no rules match — never raises.
        """
        matches: list[RuleMatch] = []
        for rule in self._RULES:
            matched_fact_ids: list[str] = []
            for fact in facts:
                lower = fact.content.lower()
                if any(trigger in lower for trigger in rule.triggers):
                    matched_fact_ids.append(fact.fact_id)
            if matched_fact_ids:
                matches.append(
                    RuleMatch(
                        rule_id=rule.rule_id,
                        rule_text=rule.rule_text,
                        jurisdiction=rule.jurisdiction,
                        matched_fact_ids=tuple(matched_fact_ids),
                        confidence=rule.base_confidence,
                    )
                )
        return matches
