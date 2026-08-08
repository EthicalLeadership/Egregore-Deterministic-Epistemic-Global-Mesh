"""Map a SelfRep dossier to applicable Quebec civil procedure/evidence rules."""

from __future__ import annotations

from egregore.domain.legal_agent.legal_models import LegalFact
from egregore.domain.legal_agent.quebec_rule_registry import (
    QuebecCivilProcedureRuleRegistry,
)
from egregore.domain.self_rep_dossier.dossier_models import Dossier
from egregore.interface.domain_data_ports import RuleRegistrySource


def map_dossier_to_procedure_rules(
    dossier: Dossier,
    rules: RuleRegistrySource | list[tuple[str, str]] | None = None,
) -> list[dict]:
    """Return Quebec procedure/evidence rules triggered by dossier claims.

    ``rules`` may be a ratified ``RuleRegistrySource`` port or a list of
    ``(source_label, raw_yaml_string)`` tuples supplied by the caller. Either
    form keeps filesystem I/O out of the domain layer.

    This is intentionally limited to civil procedure and evidence rules; it does
    not render substantive legal conclusions about the case.
    """
    if isinstance(rules, RuleRegistrySource):
        registry = QuebecCivilProcedureRuleRegistry.from_source(rules)
    else:
        registry = QuebecCivilProcedureRuleRegistry(rules)

    # Convert claims to legal facts for the rule registry.
    facts = [
        LegalFact(
            fact_id=claim.claim_id,
            content=claim.text,
            source_statement_type=claim.modality,
            source_id=claim.source_artifact_ids[0] if claim.source_artifact_ids else "",
            confidence_weight=claim.confidence,
        )
        for claim in dossier.claims
        if claim.modality != "system" and len(claim.text) >= 20
    ]

    matches = registry.find_applicable(facts)

    # Deduplicate by rule_id and include sample triggering claims.
    seen: set[str] = set()
    out: list[dict] = []
    for match in matches:
        if match.rule_id in seen:
            continue
        seen.add(match.rule_id)
        triggering = [
            {"claim_id": fact.fact_id, "text": fact.content[:200]}
            for fact in facts
            if fact.fact_id in match.matched_fact_ids
        ][:3]
        out.append(
            {
                "rule_id": match.rule_id,
                "rule_text": match.rule_text,
                "jurisdiction": match.jurisdiction,
                "confidence": match.confidence,
                "triggering_claims": triggering,
            }
        )
    return out
