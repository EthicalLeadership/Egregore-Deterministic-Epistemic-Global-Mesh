"""CLI entrypoint for `anchorum intel`."""

from __future__ import annotations

import argparse
from pathlib import Path as FilePath

from egregore.application.intelligence.unit import IntelligenceUnit
from egregore.domain.artifact.store import ArtifactStore
from egregore.domain.artifact.access import AgentArtifactAccessor


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="anchorum intel",
        description="Run Intelligence & Counter-Intelligence Unit on a dossier.",
    )
    parser.add_argument("dossier_id", help="Dossier/case ID (e.g. MOLSON-2026)")
    parser.add_argument("--artifact", action="append", help="Artifact ID to analyze (repeatable)")
    parser.add_argument("--requirement", action="append", help="Information requirement (repeatable)")
    args = parser.parse_args()

    # Build a minimal in-memory artifact store for read-only access
    store = ArtifactStore(FilePath("/tmp/anchorum_intel_store"))
    # In a real integration, load from dossier store; for now, we just demonstrate.
    # We'll create some dummy artifacts if none provided.
    accessor = AgentArtifactAccessor(store)

    unit = IntelligenceUnit(accessor)
    raw_inputs = {
        "artifact_ids": args.artifact or [],
        "requirements": args.requirement or ["Understand case posture"],
        "external_sources": {},
        "evidence_metadata": {},  # this would be populated from actual dossier in real use
    }
    collection_result, counter_result = unit.run(args.dossier_id, raw_inputs)

    print("=== INTELLIGENCE REPORT ===")
    report = collection_result.findings.get("intel_report")
    print(report.model_dump_json(indent=2))

    print("
=== COUNTER-INTELLIGENCE ===")
    print(counter_result.findings)


if __name__ == "__main__":
    main()
