"""
ANCHORUM Ingestion-Extraction Bridge
=====================================
Dispatches an ingested Artifact to the correct format-specific extractor
and returns a unified ExtractedMetadata object.

CBI-0 governed: read-only source access, immutable output,
optional .zarc audit emission.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anchorum.forensic.core.provenance import ZarcEventType, emit_zarc_event
from anchorum.forensic.core.types import (
    Artifact,
    ContainerType,
    ExtractedMetadata,
)

logger = logging.getLogger(__name__)


def _empty_metadata(artifact: Artifact, error: str | None = None) -> ExtractedMetadata:
    errors = (error,) if error else ()
    return ExtractedMetadata(
        artifact_id=artifact.artifact_id,
        extraction_time=datetime.now(UTC),
        plane_fs=artifact.filesystem_metadata,
        extraction_errors=errors,
    )


def extract_from_artifact(
    artifact: Artifact,
    case_id: str | None = None,
    operator: str | None = None,
) -> ExtractedMetadata:
    """
    Route an Artifact to the correct extractor based on ``container_type``.

    Args:
        artifact: The ingested artifact to analyze.
        case_id: Optional case identifier; required for .zarc audit emission.
        operator: Optional operator identifier; required for .zarc audit emission.

    Returns:
        ExtractedMetadata containing all 5 planes (best effort).

    """
    source_path = Path(artifact.source_path)

    try:
        data = source_path.read_bytes()
    except OSError as exc:
        logger.error("Cannot read artifact %s: %s", artifact.artifact_id, exc)
        return _empty_metadata(artifact, f"read error: {exc}")

    extracted: ExtractedMetadata

    try:
        match artifact.container_type:
            case ContainerType.PDF:
                from anchorum.forensic.core.document.pdf_obstruction import PdfDocument
                from anchorum.forensic.core.extraction.pdf import extract_pdf_metadata

                doc = PdfDocument(data)
                extracted = extract_pdf_metadata(doc, artifact.artifact_id)

            case ContainerType.OOXML:
                from anchorum.forensic.core.ooxml_extractor import (
                    extract_ooxml_metadata,
                )

                extracted = extract_ooxml_metadata(artifact)

            case ContainerType.EMAIL:
                from anchorum.forensic.core.extraction.email import (
                    extract_email_metadata,
                )

                extracted = extract_email_metadata(data, artifact.artifact_id)

            case (
                ContainerType.JPEG
                | ContainerType.PNG
                | ContainerType.TIFF
                | ContainerType.GIF
                | ContainerType.BMP
            ):
                from anchorum.forensic.core.extraction.image import (
                    extract_image_metadata,
                )

                extracted = extract_image_metadata(data, artifact.artifact_id)

            case _:
                return _empty_metadata(
                    artifact,
                    f"unsupported container type: {artifact.container_type.value}",
                )
    except Exception as exc:
        logger.exception("Extraction failed for %s", artifact.artifact_id)
        return _empty_metadata(artifact, f"extraction error: {exc}")

    # Merge filesystem metadata plane if it was not already populated
    if extracted.plane_fs is None and artifact.filesystem_metadata is not None:
        object.__setattr__(extracted, "plane_fs", artifact.filesystem_metadata)

    # Emit .zarc audit event when case_id and operator are provided
    if case_id and operator:
        try:
            payload = _audit_payload(artifact, extracted)
            emit_zarc_event(
                event_type=ZarcEventType.METADATA_EXTRACTION,
                case_id=case_id,
                operator=operator,
                payload=payload,
            )
        except Exception as exc:
            logger.warning("Failed to emit extraction audit event: %s", exc)

    return extracted


def _audit_payload(artifact: Artifact, extracted: ExtractedMetadata) -> dict[str, Any]:
    """Build a concise, serializable payload for the extraction audit event."""
    return {
        "artifact_id": artifact.artifact_id,
        "source_path": str(artifact.source_path),
        "container_type": artifact.container_type.value,
        "mime_type": artifact.mime_type,
        "size_bytes": artifact.size_bytes,
        "has_container": extracted.plane_container is not None,
        "has_application": extracted.plane_application is not None,
        "has_content": extracted.plane_content is not None,
        "has_temporal": extracted.plane_temporal is not None,
        "errors": list(extracted.extraction_errors),
    }
