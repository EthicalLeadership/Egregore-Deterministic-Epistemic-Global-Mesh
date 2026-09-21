from __future__ import annotations

from egregore.domain.legal_agent.execution_authority import ExecutionAuthority
from egregore.domain.legal_agent.legal_models import (
    InferenceNode,
    LegalAgentVersion,
    LegalAnalysisOutput,
    LegalFact,
    RuleMatch,
)
from egregore.domain.semantics.canonical_ir import (
    CanonicalSemanticIR,
    EvidenceInterpretationStatement,
    FactStatement,
    HypothesisStatement,
    SemanticStatementType,
)
from egregore.interface.legal_agent_ports import (
    IRuleRegistry,
    validate_legal_analysis_output,
)


class LegalReasoningError(Exception):
    """Raised when legal reasoning fails. Fail-closed: no partial output is ever returned."""


class LegalReasoningEngine:
    """4-stage legal reasoning pipeline.

    Morphism: CanonicalSemanticIR → LegalAnalysisOutput

    Properties:
    - Pure: no side effects
    - Deterministic: same IR + same agent_version → same output
    - Fail-closed: raises LegalReasoningError; never returns partial output
    - Terminal: outputs do not re-enter BIOK boundary without explicit re-validation

    Stage sequence:
    1. _bind_facts     — project IR statements to legal primitives
    2. _map_rules      — match applicable rules to facts
    3. _build_inference_graph — apply rules to facts; propagate confidence; flag uncertainty
    4. _compose_output — synthesize inference graph into structured LegalAnalysisOutput
    """

    def __init__(
        self,
        *,
        rule_registry: IRuleRegistry,
        agent_version: LegalAgentVersion,
    ) -> None:
        self._registry = rule_registry
        self._version = agent_version

    def analyze(
        self,
        ir: CanonicalSemanticIR,
        case_id: str,
    ) -> LegalAnalysisOutput:
        """Entry point. Orchestrates all 4 stages. Fail-closed.

        Raises LegalReasoningError on any failure — never returns partial output.
        """
        # Execution sovereignty gate: ungoverned execution must fail-closed.
        ExecutionAuthority.assert_governed()

        try:
            facts = self._bind_facts(ir)
            rules = self._map_rules(facts)
            nodes = self._build_inference_graph(rules, facts)
            output = self._compose_output(case_id, nodes, rules, facts)
            validate_legal_analysis_output(output)
            return output
        except LegalReasoningError:
            raise
        except Exception as exc:
            raise LegalReasoningError(
                f"Legal reasoning pipeline failed: {exc}"
            ) from exc

    def _bind_facts(self, ir: CanonicalSemanticIR) -> list[LegalFact]:
        """Stage 1: Project IR semantic statements to typed legal facts.

        Projection rules:
        - FactStatement         → LegalFact (confidence_weight=1.0)
        - EvidenceInterpretation → LegalFact (confidence_weight=0.7; bounded interpretation)
        - HypothesisStatement   → LegalFact (confidence_weight=0.4; speculative)
        - ClassificationStatement → excluded (routing metadata, not a legal fact)
        """
        facts: list[LegalFact] = []
        for i, stmt in enumerate(ir.statements):
            if isinstance(stmt, FactStatement):
                facts.append(
                    LegalFact(
                        fact_id=stmt.source_id or f"fact_{i}",
                        content=stmt.content,
                        source_statement_type=SemanticStatementType.FACT.value,
                        source_id=stmt.source_id,
                        confidence_weight=1.0,
                    )
                )
            elif isinstance(stmt, EvidenceInterpretationStatement):
                facts.append(
                    LegalFact(
                        fact_id=stmt.evidence_reference or f"interp_{i}",
                        content=stmt.interpretation,
                        source_statement_type=SemanticStatementType.EVIDENCE_INTERPRETATION.value,
                        source_id=stmt.evidence_reference,
                        confidence_weight=0.7,
                    )
                )
            elif isinstance(stmt, HypothesisStatement):
                facts.append(
                    LegalFact(
                        fact_id=f"hyp_{i}",
                        content=stmt.claim,
                        source_statement_type=SemanticStatementType.HYPOTHESIS.value,
                        source_id="",
                        confidence_weight=0.4,
                    )
                )
            # ClassificationStatement: routing metadata — not projected to legal domain
        return facts

    def _map_rules(self, facts: list[LegalFact]) -> list[RuleMatch]:
        """Stage 2: Find applicable rules for extracted facts via the rule registry.

        Delegates entirely to the IRuleRegistry implementation.
        Returns empty list if no rules match — never raises.
        """
        return self._registry.find_applicable(facts)

    def _build_inference_graph(
        self,
        rules: list[RuleMatch],
        facts: list[LegalFact],
    ) -> list[InferenceNode]:
        """Stage 3: Construct inference graph from matched rules and facts.

        Confidence propagation: min(rule.confidence, avg(matched_fact.confidence_weight))
        Uncertainty detection:
        - low_confidence       : computed confidence < 0.5
        - no_matched_facts     : fact IDs in RuleMatch not found in fact index
        - conflict_with_<id>   : rule shares matched facts with another active rule
        """
        facts_by_id = {f.fact_id: f for f in facts}
        nodes: list[InferenceNode] = []

        for i, rule in enumerate(rules):
            matched_weights = [
                facts_by_id[fid].confidence_weight
                for fid in rule.matched_fact_ids
                if fid in facts_by_id
            ]

            if matched_weights:
                avg_weight = sum(matched_weights) / len(matched_weights)
                node_confidence = round(min(rule.confidence, avg_weight), 4)
            else:
                node_confidence = 0.0

            uncertainty_parts: list[str] = []
            if node_confidence < 0.5:
                uncertainty_parts.append("low_confidence")
            if not matched_weights:
                uncertainty_parts.append("no_matched_facts")

            # Conflict: another active rule shares any matched fact
            for j, other_rule in enumerate(rules):
                if j != i and set(rule.matched_fact_ids) & set(
                    other_rule.matched_fact_ids
                ):
                    uncertainty_parts.append(f"conflict_with_{other_rule.rule_id}")
                    break

            nodes.append(
                InferenceNode(
                    node_id=f"node_{i}",
                    premise_rule_ids=(rule.rule_id,),
                    premise_fact_ids=rule.matched_fact_ids,
                    conclusion=(
                        f"Rule '{rule.rule_id}' may apply based on "
                        f"facts {rule.matched_fact_ids}"
                    ),
                    confidence=node_confidence,
                    uncertainty_reason="; ".join(uncertainty_parts),
                )
            )

        return nodes

    def _compose_output(
        self,
        case_id: str,
        nodes: list[InferenceNode],
        rules: list[RuleMatch],
        facts: list[LegalFact],
    ) -> LegalAnalysisOutput:
        """Stage 4: Synthesize inference graph into structured LegalAnalysisOutput.

        Issues are derived from matched rules (not invented).
        Supporting evidence IDs are deduplicated from inference node premises.
        Uncertainty flags are deduplicated across all nodes.
        prohibited_conclusions is always () — structural invariant.
        """
        issues = tuple(
            f"Potential issue under {r.rule_id} ({r.jurisdiction})" for r in rules
        )
        supporting_ids = tuple(
            dict.fromkeys(fid for n in nodes for fid in n.premise_fact_ids)
        )
        confidence_scores: dict[str, float] = {
            node.node_id: node.confidence for node in nodes
        }
        uncertainty_flags = tuple(
            dict.fromkeys(
                flag
                for node in nodes
                if node.uncertainty_reason
                for flag in node.uncertainty_reason.split("; ")
            )
        )

        return LegalAnalysisOutput(
            case_id=case_id,
            issues_identified=issues,
            applicable_rules=tuple(rules),
            supporting_evidence_ids=supporting_ids,
            inference_chain=tuple(nodes),
            confidence_scores=confidence_scores,
            uncertainty_flags=uncertainty_flags,
            reasoning_version=(
                f"{self._version.rule_registry_version}:"
                f"{self._version.inference_engine_version}"
            ),
            agent_version=self._version,
            prohibited_conclusions=(),
        )
