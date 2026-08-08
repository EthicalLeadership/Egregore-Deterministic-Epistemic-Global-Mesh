"""
ANCHORUM Pipeline — CLI Entry Point
File: anchorum/pipeline/narrative_trace/__main__.py

Usage:
    python -m anchorum.pipeline.narrative_trace \\
        --communications "/data/molson/source/*" \\
        --evidence "/data/molson/evidence/*" \\
        --output "/data/molson/demolition_timeline.md" \\
        --case-id molson-001

The tool sees files, auto-detects format, auto-classifies type, and builds the timeline.
No manual configuration needed per file.
"""

import argparse
import sys
from pathlib import Path

from .analysis.tracer import NarrativeTracer


def main():
    parser = argparse.ArgumentParser(
        description="ANCHORUM Narrative Trace & Demolition Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect all files in source directories
  python -m anchorum.pipeline.narrative_trace \\
      --communications "./molson/emails/*.eml" \\
      --evidence "./molson/medical/*.pdf" \\
      --output ./molson_timeline.md

  # Mixed formats (emails, PDFs, Word docs)
  python -m anchorum.pipeline.narrative_trace \\
      --communications "./molson/comms/**/*" \\
      --evidence "./molson/evidence/**/*" \\
      --output ./molson_timeline.md \\
      --case-id molson-2026
        """
    )
    parser.add_argument(
        "--communications", "-c", required=True,
        help="Glob pattern for counterparty communications (emails, letters, etc.)"
    )
    parser.add_argument(
        "--evidence", "-e", required=True,
        help="Glob pattern for documentary evidence (medical records, insurer docs, etc.)"
    )
    parser.add_argument(
        "--output", "-o", default="./demolition_timeline.md",
        help="Output path for the markdown report (default: ./demolition_timeline.md)"
    )
    parser.add_argument(
        "--case-id", default="unnamed-case",
        help="Case identifier for the report header"
    )
    parser.add_argument(
        "--vault", default=None,
        help="Optional vault path for caching ingested records"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print detailed processing logs"
    )

    args = parser.parse_args()

    tracer = NarrativeTracer(vault_path=args.vault)
    timeline = tracer.trace(
        communications_glob=args.communications,
        evidence_glob=args.evidence,
        output_path=args.output,
        case_id=args.case_id,
    )

    if args.verbose:
        print(f"\\n[SUMMARY] Case: {timeline.case_id}")
        print(f"  Entries: {len(timeline.entries)}")
        print(f"  Unverified: {len(timeline.unverified_claims)}")
        print(f"  Gaps: {len(timeline.evidence_gaps)}")
        print(f"  Report: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

