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

    print("=== INTELLIGENCE REPORT ===")
    report = collection_result.findings.get("intel_report")
    print(report.model_dump_json(indent=2))

    print()
    print("=== COUNTER-INTELLIGENCE ===")
    print(counter_result.findings)


if __name__ == "__main__":
    main()
