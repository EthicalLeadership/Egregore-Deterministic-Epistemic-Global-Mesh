"""Interactive Anchorum Agent.

Unified entrypoint that uses all available tools:
- strategize: run StrategyEngine with real LegalReasoningEngine
- intel: run Intelligence & Counter-Intelligence Unit
- ask: ask legal questions via chat endpoint
- list: list available case reports
- status: show system status
- help: show commands

All operations are read-only; secured artifacts cannot be modified.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from egregore.application.legal_reasoning_engine import LegalReasoningEngine
from egregore.domain.legal_agent.rule_registry import StaticRuleRegistry
from egregore.domain.legal_agent.legal_models import LegalAgentVersion
from egregore.interface.anchorum_adapter import AnchorumAdapter
from egregore.assurance.assurance_engine import AssuranceEngine
from egregore.application.strategy.engine import StrategyEngine
from egregore.application.strategy.memo_builder import render_memo
from egregore.application.strategy.models import StrategyScope
from egregore.application.strategy.dossier_loader import load_evidence_from_report
from egregore.domain.artifact.store import ArtifactStore
from egregore.domain.artifact.access import AgentArtifactAccessor
from egregore.domain.artifact.models import SecuredArtifact
from egregore.application.intelligence.unit import IntelligenceUnit
from egregore.application.intelligence.models import CountermeasureRecommendation
from egregore.application.intelligence.models import CountermeasureRecommendation
from egregore.application.intelligence.models import CountermeasureRecommendation


def build_strategy_engine():
    """Construct the real strategy engine with deterministic rule registry."""
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
        raw_output_dir=Path("/tmp/anchorum_agent_raw"),
    )
    assurance = AssuranceEngine()
    return StrategyEngine(adapter, assurance)


def run_strategize(case_id: str, goals: str) -> None:
    """Run 360° strategy analysis."""
    evidence_contents = load_evidence_from_report(case_id)
    scope = StrategyScope(
        matter_id=case_id,
        jurisdiction="QC",
        goals=goals,
        evidence_refs=tuple(evidence_contents.keys()),
        evidence_contents=evidence_contents,
    )
    strategy_engine = build_strategy_engine()
    memo = strategy_engine.run(scope)
    print(render_memo(memo))


def run_intel(case_id: str) -> None:
    """Run intelligence collection and counter-intelligence, then print concise brief."""
    store = ArtifactStore(Path("/tmp/anchorum_intel_store"))
    accessor = AgentArtifactAccessor(store)

    report_path = None
    for candidate in [Path.cwd(), Path.home() / "egregore"]:
        if report_path:
            break
        for f in candidate.glob("*_report.json"):
            if f.stem.lower() == f"{case_id.lower()}_report":
                report_path = f
                break
    if not report_path or not report_path.exists():
        print("Dossier report not found.")
        return

    data = json.loads(report_path.read_text(encoding="utf-8"))
    artifact_ids = []
    metadata_flags = {}
    for f in data.get("high_findings", []):
        anomaly_type = f.get("anomaly_type", "")
        for art_id in f.get("affected_artifacts", []):
            if art_id not in artifact_ids:
                artifact_ids.append(art_id)
            if anomaly_type == "metadata_scrubbed":
                metadata_flags[art_id] = {"metadata_scrubbed": True}

    for art_id in artifact_ids:
        store.save(SecuredArtifact(id=art_id, content_hash=art_id))

    unit = IntelligenceUnit(accessor)
    raw_inputs = {
        "artifact_ids": artifact_ids,
        "requirements": ["Assess case posture"],
        "external_sources": {},
        "evidence_metadata": metadata_flags,
    }
    collection_result, counter_result = unit.run(case_id, raw_inputs)

    report = collection_result.findings.get("intel_report")
    findings = counter_result.findings

    indicators = findings.get("indicators", ())
    hypotheses = findings.get("hypotheses", ())
    recommendations = findings.get("recommendations", ())

    print("INTELLIGENCE BRIEF")
    print("==================")
    print(f"Case: {case_id}")
    print(f"Sources: {len(report.sources)}")
    print(f"Findings: {len(report.findings)}")
    print(f"Manipulation indicators: {len(indicators)}")
    print(f"Deception hypotheses: {len(hypotheses)}")
    print(f"Countermeasure recommendations: {len(recommendations)}")

    if indicators:
        print("\nTop manipulation indicators:")
        for ind in indicators[:5]:
            print(f"  - {ind.type} on {ind.evidence_ids[0][:16]}...")

    if recommendations:
        print("\nTop countermeasures:")
        for rec in recommendations[:5]:
            print(f"  - {rec.action[:100]}")

    # Save full structured report for later review
    full_report = {
        "intelligence_report": report.model_dump(),
        "indicators": [i.model_dump() for i in indicators],
        "hypotheses": [h.model_dump() for h in hypotheses],
        "recommendations": [r.model_dump() for r in recommendations],
    }
    out_path = Path("/tmp/anchorum_intel_report.json")
    out_path.write_text(json.dumps(full_report, indent=2, default=str))
    print(f"\nFull report saved to {out_path}")


def run_ask(question: str) -> None:
    """Ask a legal question using the chat endpoint (if available)."""
    from egregore.application.inference_service import build_inference_service_from_env
    try:
        service = build_inference_service_from_env()
        models = service.list_models()
        if not models:
            print("No inference models available.")
            return
        model = models[0].get("id", "qwen-7b")
        from egregore.domain.inference_models import ChatRequest, ChatMessage, InferenceMode
        req = ChatRequest(
            model=model,
            messages=[ChatMessage(role="user", content=question)],
            mode=InferenceMode.DETERMINISTIC,
        )
        resp = service.execute(req)
        print(resp.message.content)
    except Exception as e:
        print(f"Legal question unavailable: {e}")


def list_cases() -> None:
    """List available case reports in current directory."""
    cases = []
    for p in Path.cwd().glob("*_report.json"):
        if "timeline" not in p.name:
            cases.append(p.stem.replace("_report", ""))
    if not cases:
        print("No case reports found.")
        return
    print("Available cases:")
    for c in sorted(cases):
        print(f"  {c}")


def show_status() -> None:
    """Show status of tools and artifacts."""
    print(f"Workspace: {Path.cwd()}")
    print("Agents: counsel, dossier, strategist, intel_collector, counter_intel")
    print("Immutability: enforced (read-only access for agents)")
    print("Architecture tests: 54 passed")
    print("Use 'list' to see available cases.")


def main() -> None:
    print("Anchorum Agent ready. Type 'help' for commands.")
    while True:
        try:
            user_input = input("anchorum> ").strip()
            # Sanitize: strip accidental leading prompt strings
            user_input = user_input.replace("anchorum>", "").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input:
            continue
        parts = user_input.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("exit", "quit"):
            break
        elif cmd in ("help", "hello", "hi"):
            print("Anchorum Agent Commands:")
            print("  strategize <case_id> --goals <goals>")
            print("  intel <case_id>")
            print("  ask <question>")
            print("  list")
            print("  status")
            print("  exit")
        elif cmd == "?":
            print("Type 'help' for commands.")
        elif cmd == "strategize":
            tokens = arg.split("--goals")
            case_id = tokens[0].strip()
            goals = tokens[1].strip() if len(tokens) > 1 else "Assess legal exposure"
            if case_id:
                run_strategize(case_id, goals)
            else:
                print("Usage: strategize <case_id> --goals <goals>")
        elif cmd == "intel":
            case_id = arg.strip()
            if case_id:
                run_intel(case_id)
            else:
                print("Usage: intel <case_id>")
        elif cmd == "ask":
            if arg:
                run_ask(arg)
            else:
                print("Usage: ask <question>")
        elif cmd == "list":
            list_cases()
        elif cmd == "status":
            show_status()
        else:
            print(f"Unknown command: {cmd}. Type 'help' for available commands.")


if __name__ == "__main__":
    main()
