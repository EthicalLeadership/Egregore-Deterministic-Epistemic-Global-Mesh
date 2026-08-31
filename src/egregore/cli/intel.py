"""CLI entrypoint for `anchorum intel`."""

from __future__ import annotations

import argparse
import json
from pathlib import Path as FilePath

from egregore.application.intelligence.unit import IntelligenceUnit
from egregore.domain.artifact.store import ArtifactStore
from egregore.domain.artifact.access import AgentArtifactAccessor
from egregore.domain.artifact.models import SecuredArtifact


def load_artifacts_from_report(case_id: str, store: ArtifactStore):
    """Load artifact IDs and metadata flags from case report."""
    report_path = FilePath.cwd() / f"{case_id}_report.json"
    if not report_path.exists():
        report_path = FilePath.home() / "egregore" / f"{case_id}_report.json"
    if not report_path.exists():
        return [], {}

    data = json.loads(report_path.read_text(encoding="utf-8"))
    findings = data.get("high_findings", [])
    artifact_ids = []
    metadata_flags = {}

    for f in findings:
        anomaly_type = f.get("anomaly_type", "")
        affected = f.get("affected_artifacts", [])
        for art_id in affected:
            if art_id not in artifact_ids:
                artifact_ids.append(art_id)
            if anomaly_type == "metadata_scrubbed":
                metadata_flags[art_id] = {"metadata_scrubbed": True}

    for art_id in artifact_ids:
        store.save(SecuredArtifact(id=art_id, content_hash=art_id))

    return artifact_ids, metadata_flags


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="anchorum intel",
        description="Run Intelligence & Counter-Intelligence Unit on a dossier.",
    )
    parser.add_argument("dossier_id", help="Dossier/case ID (e.g. MOLSON-2026)")
    parser.add_argument("--requirement", action="append", help="Information requirement (repeatable)")
    args = parser.parse_args()

    store = ArtifactStore(FilePath("/tmp/anchorum_intel_store"))
    artifact_ids, evidence_metadata = load_artifacts_from_report(args.dossier_id, store)

    accessor = AgentArtifactAccessor(store)
    unit = IntelligenceUnit(accessor)

    raw_inputs = {
        "artifact_ids": artifact_ids,
        "requirements": args.requirement or ["Understand case posture"],
        "external_sources": {},
        "evidence_metadata": evidence_metadata,
    }

    collection_result, counter_result = unit.run(args.dossier_id, raw_inputs)

    report = collection_result.findings.get("intel_report")
    findings = counter_result.findings

    indicators = findings.get("indicators", ())
    hypotheses = findings.get("hypotheses", ())
    recommendations = findings.get("recommendations", ())

    print("INTELLIGENCE BRIEF")
    print("==================")
    print(f"Case: {args.dossier_id}")
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

    full_report = {
        "intelligence_report": report.model_dump(),
        "indicators": [i.model_dump() for i in indicators],
        "hypotheses": [h.model_dump() for h in hypotheses],
        "recommendations": [r.model_dump() for r in recommendations],
    }
    out_path = FilePath("/tmp/anchorum_intel_report.json")
    out_path.write_text(json.dumps(full_report, indent=2, default=str))
    print(f"\nFull report saved to {out_path}")


if __name__ == "__main__":
    main()
