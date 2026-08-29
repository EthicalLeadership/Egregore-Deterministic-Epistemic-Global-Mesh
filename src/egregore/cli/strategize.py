"""CLI entrypoint for `anchorum strategize`."""

from __future__ import annotations

import argparse
from pathlib import Path as FilePath

from egregore.application.strategy.engine import StrategyEngine
from egregore.application.strategy.memo_builder import render_memo
from egregore.application.strategy.models import StrategyScope
from egregore.interface.anchorum_adapter import AnchorumAdapter
from egregore.assurance.assurance_engine import AssuranceEngine
from egregore.application.inference_service import build_inference_service_from_env


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="anchorum strategize",
        description="Generate 360° elite strategy memo for a dossier.",
    )
    parser.add_argument("dossier_id", help="Dossier/case ID (e.g. MOLSON-2026)")
    parser.add_argument("--goals", required=True, help="Strategic goals")
    parser.add_argument("--jurisdiction", default="QC", help="Jurisdiction code")
    parser.add_argument("--constraints", nargs="*", default=[], help="Constraint labels")
    parser.add_argument("--stakeholders", nargs="*", default=[], help="Stakeholder labels")
    parser.add_argument("--evidence", nargs="*", default=[], help="Evidence IDs")
    args = parser.parse_args()

    from egregore.application.strategy.dossier_loader import load_evidence_from_report
    evidence_contents = load_evidence_from_report(args.dossier_id)

    scope = StrategyScope(
        matter_id=args.dossier_id,
        jurisdiction=args.jurisdiction,
        goals=args.goals,
        constraints=tuple(args.constraints),
        stakeholders=tuple(args.stakeholders),
        evidence_refs=tuple(evidence_contents.keys()),
        evidence_contents=evidence_contents,
    )

    # Build real adapter and assurance.
    from egregore.application.legal_reasoning_engine import LegalReasoningEngine
    from egregore.domain.legal_agent.rule_registry import StaticRuleRegistry
    from egregore.domain.legal_agent.legal_models import LegalAgentVersion

    # Use StaticRuleRegistry as deterministic fallback.
    # Quebec registry can be swapped in later without changing this script.
    rule_registry = StaticRuleRegistry()
    engine = LegalReasoningEngine(
        rule_registry=rule_registry,
        agent_version=LegalAgentVersion(
            rule_registry_version="static-v1",
            inference_engine_version="strategize-1.0",
        ),
    )

    adapter = AnchorumAdapter(
        engine=engine,
        raw_output_dir=FilePath("/tmp/anchorum_raw_outputs"),
    )
    assurance = AssuranceEngine()

    strategy_engine = StrategyEngine(adapter, assurance)
    memo = strategy_engine.run(scope)
    print(render_memo(memo))


if __name__ == "__main__":
    main()
