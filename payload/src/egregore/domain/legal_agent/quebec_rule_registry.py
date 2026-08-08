"""In-memory Quebec civil procedure and evidence rule registry.

The registry is populated from raw YAML strings supplied by an adapter in the
calling Plane-2 layer. Domain never reads files directly.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from egregore.domain.legal_agent.legal_models import LegalFact, RuleMatch
from egregore.interface.domain_data_ports import RuleRegistrySource


@dataclass(frozen=True)
class QuebecLegalRule:
    """Single rule entry loaded from YAML."""

    rule_id: str
    domain: str
    source: str
    title: str
    rule_text: str
    obligation_holder: str
    triggers: tuple[str, ...]
    elements_to_prove: tuple[str, ...]
    consequences: tuple[str, ...]
    confidence: float
    verification_required: bool


class QuebecCivilProcedureRuleRegistry:
    """Quebec civil procedure and evidence rules supplied as raw YAML.

    Implements the IRuleRegistry Protocol. Trigger matching is substring-based
    over lowercased fact content.
    """

    def __init__(self, rules: list[tuple[str, str]] | None = None) -> None:
        """Initialize from a list of (source_label, raw_yaml_string) tuples."""
        self._rules: tuple[QuebecLegalRule, ...] = self._load_rules(rules or [])

    @classmethod
    def from_source(
        cls, source: RuleRegistrySource
    ) -> QuebecCivilProcedureRuleRegistry:
        """Build the registry from a ratified ``RuleRegistrySource`` port.

        This is the governable entrypoint: the domain depends on the formal
        port, not on any concrete storage adapter.
        """
        instance = cls.__new__(cls)
        raw = source.load().decode("utf-8")
        # Label the source generically; callers that need provenance can wrap
        # the source in an adapter that supplies a custom label.
        instance._rules = instance._load_rules([("rule-registry", raw)])
        return instance

    def _load_rules(self, rules: list[tuple[str, str]]) -> tuple[QuebecLegalRule, ...]:
        loaded: list[QuebecLegalRule] = []
        for _source, raw_yaml in rules:
            data = yaml.safe_load(raw_yaml)
            if not isinstance(data, dict):
                continue
            for entry in data.get("rules", []):
                loaded.append(
                    QuebecLegalRule(
                        rule_id=entry["rule_id"],
                        domain=entry["domain"],
                        source=entry["source"],
                        title=entry["title"],
                        rule_text=entry["rule_text"].strip(),
                        obligation_holder=entry.get("obligation_holder", "unknown"),
                        triggers=tuple(t.lower() for t in entry.get("triggers", [])),
                        elements_to_prove=tuple(entry.get("elements_to_prove", [])),
                        consequences=tuple(entry.get("consequences", [])),
                        confidence=float(entry.get("confidence", 0.8)),
                        verification_required=bool(
                            entry.get("verification_required", False)
                        ),
                    )
                )
        return tuple(loaded)

    def find_applicable(self, facts: list[LegalFact]) -> list[RuleMatch]:
        """Match rules to facts via trigger keyword presence.

        Returns empty list if no rules match.
        """
        matches: list[RuleMatch] = []
        for rule in self._rules:
            matched_fact_ids: list[str] = []
            for fact in facts:
                lower = fact.content.lower()
                if any(trigger in lower for trigger in rule.triggers):
                    matched_fact_ids.append(fact.fact_id)
            if matched_fact_ids:
                rule_text = rule.rule_text
                if rule.verification_required:
                    rule_text = (
                        f"[VERIFY CURRENT CONSOLIDATION] {rule_text} "
                        f"(Source: {rule.source})"
                    )
                matches.append(
                    RuleMatch(
                        rule_id=rule.rule_id,
                        rule_text=rule_text,
                        jurisdiction="Quebec",
                        matched_fact_ids=tuple(matched_fact_ids),
                        confidence=rule.confidence,
                    )
                )
        return matches

    def list_rules(self) -> tuple[QuebecLegalRule, ...]:
        """Return all loaded rules (useful for inspection)."""
        return self._rules


# Structural type assertion: this class satisfies IRuleRegistry.
# Runtime checkers can verify isinstance(obj, IRuleRegistry) if desired.
