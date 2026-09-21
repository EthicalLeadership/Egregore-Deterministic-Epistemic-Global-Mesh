r"""
ANCHORUM Batch Extraction CLI
==============================
Ingest and extract metadata from every supported file in a directory tree.

Example:
    .venv/bin/python -m anchorum.forensic.core.cli.batch_extract \
        /var/lib/anchorum/evidence_snapshot \
        --case-id CASE-2026 \
        --operator kark \
        --output /var/lib/anchorum/reports/self_rep_extracted.jsonl

"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anchorum.forensic.core import (
    extract_from_artifact,
    ingest_artifact,
    to_canonical_json,
)
from anchorum.forensic.core.types import Artifact, ContainerType, ExtractedMetadata

logger = logging.getLogger(__name__)


# Directories to skip when walking evidence trees
_SKIP_DIRS = {
    ".venv",
    "venv",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "tmp",
    "temp",
    ".cache",
}

_SUPPORTED_TYPES = {
    ContainerType.PDF,
    ContainerType.OOXML,
    ContainerType.ODT,
    ContainerType.EMAIL,
    ContainerType.JPEG,
    ContainerType.PNG,
    ContainerType.TIFF,
    ContainerType.GIF,
    ContainerType.BMP,
}


def _should_process(path: Path) -> bool:
    """Return True if the file extension or content looks worth ingesting."""
    if path.is_symlink():
        return False
    if not path.is_file():
        return False
    # Skip hidden files and common noise
    return not path.name.startswith(".")


def _iter_evidence_files(root: Path) -> list[Path]:
    """Collect candidate files under root, skipping known non-evidence dirs."""
    files: list[Path] = []
    for item in root.rglob("*"):
        try:
            rel_parts = item.relative_to(root).parts
        except ValueError:
            continue
        if any(part in _SKIP_DIRS for part in rel_parts):
            continue
        if _should_process(item):
            files.append(item)
    return sorted(files)


def _summarize(pairs: list[tuple[Artifact, ExtractedMetadata]]) -> dict[str, Any]:
    """Build a summary dict from extraction results."""
    by_type: dict[str, int] = {}
    errors = 0
    total_urls = set()
    total_emails = set()
    total_events = 0
    earliest: datetime | None = None
    latest: datetime | None = None

    for artifact, r in pairs:
        if r.extraction_errors:
            errors += 1

        ctype = artifact.container_type.value
        by_type[ctype] = by_type.get(ctype, 0) + 1

        if r.plane_content is not None:
            total_urls.update(r.plane_content.embedded_urls)
            total_emails.update(r.plane_content.email_addresses)

        if r.plane_temporal is not None:
            total_events += len(r.plane_temporal.events)
            if r.plane_temporal.earliest is not None and (
                earliest is None or r.plane_temporal.earliest < earliest
            ):
                earliest = r.plane_temporal.earliest
            if r.plane_temporal.latest is not None and (
                latest is None or r.plane_temporal.latest > latest
            ):
                latest = r.plane_temporal.latest

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "artifact_count": len(pairs),
        "successful_count": len(pairs) - errors,
        "error_count": errors,
        "by_container_type": by_type,
        "unique_urls": len(total_urls),
        "unique_email_addresses": len(total_emails),
        "total_timeline_events": total_events,
        "temporal_earliest": earliest.isoformat() if earliest else None,
        "temporal_latest": latest.isoformat() if latest else None,
        "top_urls": sorted(total_urls)[:50],
        "top_emails": sorted(total_emails)[:50],
    }


def run_batch(
    root: Path,
    case_id: str,
    operator: str,
    output: Path,
    summary_path: Path | None = None,
    enforce_readonly: bool = True,
) -> int:
    """Run ingestion + extraction over a directory tree. Returns exit code."""
    output.parent.mkdir(parents=True, exist_ok=True)
    summary_path = summary_path or output.with_suffix(".summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    files = _iter_evidence_files(root)
    logger.info("Found %d candidate files under %s", len(files), root)

    pairs: list[tuple[Artifact, ExtractedMetadata]] = []

    with output.open("w", encoding="utf-8") as out:
        for idx, path in enumerate(files, 1):
            try:
                artifact = ingest_artifact(
                    path, case_id, operator, enforce_readonly=enforce_readonly
                )
            except Exception as exc:
                logger.warning("Ingestion failed for %s: %s", path, exc)
                continue

            if artifact.container_type not in _SUPPORTED_TYPES:
                logger.debug(
                    "Skipping unsupported type %s: %s", artifact.container_type, path
                )
                continue

            try:
                extracted = extract_from_artifact(
                    artifact, case_id=case_id, operator=operator
                )
            except Exception as exc:
                logger.exception("Extraction failed for %s", path)
                extracted = ExtractedMetadata(
                    artifact_id=artifact.artifact_id,
                    extraction_time=datetime.now(UTC),
                    plane_fs=artifact.filesystem_metadata,
                    extraction_errors=(str(exc),),
                )

            pairs.append((artifact, extracted))
            record = to_canonical_json(extracted)
            record["container_type"] = artifact.container_type.value
            record["source_path"] = str(artifact.source_path)
            record["mime_type"] = artifact.mime_type
            out.write(json.dumps(record, ensure_ascii=False))
            out.write("\n")

            if idx % 100 == 0:
                logger.info("Processed %d/%d files", idx, len(files))

    summary = _summarize(pairs)
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    logger.info(
        "Batch complete: %d artifacts written to %s, summary to %s",
        len(pairs),
        output,
        summary_path,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Batch ingest and extract metadata from an evidence directory."
    )
    parser.add_argument("root", type=Path, help="Directory tree to process")
    parser.add_argument("--case-id", required=True, help="Case identifier")
    parser.add_argument("--operator", required=True, help="Operator identifier")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("report/batch_extracted.jsonl"),
        help="JSONL output path",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="Summary JSON output path (default: <output>.summary.json)",
    )
    parser.add_argument(
        "--enforce-readonly",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require source files to be read-only before ingestion (default: True)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.root.exists():
        logger.error("Root directory does not exist: %s", args.root)
        return 1

    return run_batch(
        root=args.root,
        case_id=args.case_id,
        operator=args.operator,
        output=args.output,
        summary_path=args.summary,
        enforce_readonly=args.enforce_readonly,
    )


if __name__ == "__main__":
    sys.exit(main())
