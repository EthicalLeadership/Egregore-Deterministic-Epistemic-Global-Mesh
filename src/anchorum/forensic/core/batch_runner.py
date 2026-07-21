"""
ANCHORUM Forensic Batch Runner — Runtime-Integrated Edition
============================================================

A forensic engine that writes to the Egregore runtime's `.zarc` provenance
stream instead of owning persistence, signing, or archival.

Runtime integration (optional):
- Provenance  -> Ed25519-signed `.zarc` JSONL
- ZarcJournal -> case-scoped snapshot persistence
- DagSigner   -> signed final report
- SedimentArchive -> immutable investigation report stratum
- LitigationHoldTrigger -> pre-batch hold
- AnchorumBridge -> vault sync of forensic `.zarc` entries
- AnchorumIntegrityGate -> pre-flight health check

If the runtime is unavailable or no signing key is supplied, the runner falls
back to standalone JSON-only mode.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anchorum.forensic.core import (
    extract_from_artifact,
    ingest_artifact,
    to_canonical_json,
)
from anchorum.forensic.core.egregore_client import EgregoreModelClient
from anchorum.forensic.core.canonicalization import EntityExtractor, merge_entities
from anchorum.forensic.core.ingestion import IngestionError, detect_container
from anchorum.forensic.core.types import (
    AnomalyFinding,
    AnomalyType,
    Artifact,
    ContainerType,
    ContentMetadata,
    ExtractedMetadata,
    InvestigationReport,
    TimelineEntry,
)
from anchorum.forensic.core.validation import validate_case_id, validate_operator

# Optional deep-recovery engines (Option B)
try:
    from anchorum.forensic.core.document.hidden_layer_detection import (
        HiddenLayerDetector,
    )
    from anchorum.forensic.core.document.metadata_extraction import MetadataExtractor
    from anchorum.forensic.core.document.office_deep_revision import recover_revisions
    from anchorum.forensic.core.document.pdf_pharos_engine import PdfPharosEngine

    _DEEP_IMPORTS_OK = True
except Exception as _deep_exc:
    _DEEP_IMPORTS_OK = False
    recover_revisions = None  # type: ignore[misc,assignment]
    HiddenLayerDetector = None  # type: ignore[misc,assignment]
    PdfPharosEngine = None  # type: ignore[misc,assignment]
    MetadataExtractor = None  # type: ignore[misc,assignment]
    logging.getLogger(__name__).debug(
        "Deep-recovery engines unavailable: %s", _deep_exc
    )

logger = logging.getLogger("anchorum.forensic.batch_runner")

ENGINE = "anchorum_forensic"
METHODOLOGY_VERSION = "anchorum-forensic-1.0.0"

SUPPORTED_TYPES = {
    ContainerType.PDF,
    ContainerType.OOXML,
    ContainerType.ODT,
    ContainerType.EMAIL,
    ContainerType.JPEG,
    ContainerType.PNG,
    ContainerType.TIFF,
    ContainerType.GIF,
    ContainerType.BMP,
    ContainerType.TEXT,
}

# Plain-text evidence extensions (Plane 4 content extraction)
_TEXT_EXTENSIONS = {
    ".txt",
    ".csv",
    ".json",
    ".html",
    ".htm",
    ".md",
    ".db",
    ".sqlite",
    ".sqlite3",
}
_TEXT_SCAN_MAX_BYTES = 10 * 1024 * 1024

# Content pattern regexes (anchored loosely; used for entity extraction)
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_URL_RE = re.compile(r"https?://[^\s<>\"{}|\\^`\[\]]+", re.IGNORECASE)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_MAC_RE = re.compile(r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_SSN_RE = re.compile(r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b")

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

_MAX_FILE_BYTES = 100 * 1024 * 1024


# ---------------------------------------------------------------------------
# Runtime imports (best-effort)
# ---------------------------------------------------------------------------
def _try_import_runtime() -> dict[str, Any] | None:
    try:
        from egregore.domain.agency_taxonomy import (
            AgencyId,
            AgencyState,
            Biome,
            Lobe,
            Species,
        )
        from egregore.domain.semantics_models import (
            AuditEvent,
            CaseState,
            GenerateDossierCommand,
            OutboxEntry,
        )
        from egregore.governance.anchorum_bridge import AnchorumBridge
        from egregore.governance.anchorum_integrity_gate import (
            AnchorumIntegrityFailure,
            run_anchorum_check,
        )
        from egregore.governance.dag_signer import (
            DagSigner,
            merge_payload_for_signature,
        )
        from egregore.governance.litigation_hold import LitigationHoldTrigger
        from egregore.infrastructure.sediment_archive import SedimentArchive
        from egregore.infrastructure.zarc_journal import ZarcJournal
        from egregore.kernel.provenance import Provenance

        return {
            "Provenance": Provenance,
            "ZarcJournal": ZarcJournal,
            "AnchorumBridge": AnchorumBridge,
            "SedimentArchive": SedimentArchive,
            "DagSigner": DagSigner,
            "merge_payload_for_signature": merge_payload_for_signature,
            "LitigationHoldTrigger": LitigationHoldTrigger,
            "AgencyId": AgencyId,
            "AgencyState": AgencyState,
            "Species": Species,
            "Biome": Biome,
            "Lobe": Lobe,
            "GenerateDossierCommand": GenerateDossierCommand,
            "AuditEvent": AuditEvent,
            "OutboxEntry": OutboxEntry,
            "CaseState": CaseState,
            "run_anchorum_check": run_anchorum_check,
            "AnchorumIntegrityFailure": AnchorumIntegrityFailure,
        }
    except Exception as exc:
        logger.debug("Runtime imports unavailable: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Injected boundary callables
# ---------------------------------------------------------------------------
def _default_hold_api(*, case_id: str, scope: list[str], reason: str) -> str:
    hold_id = f"HOLD-{case_id}-{uuid.uuid4().hex[:8]}"
    logger.info("Litigation hold requested: %s (scope=%s)", hold_id, scope)
    return hold_id


def _default_vault_ingest(batch: list[dict[str, Any]]) -> dict[str, Any]:
    logger.info("Vault ingest received %d .zarc records", len(batch))
    return {"ingested": len(batch), "status": "ack"}


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------
def _iter_evidence_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for item in root.rglob("*"):
        try:
            rel_parts = item.relative_to(root).parts
        except ValueError:
            continue
        if any(part in _SKIP_DIRS for part in rel_parts):
            continue
        if item.is_symlink() or not item.is_file():
            continue
        if item.name.startswith("."):
            continue
        files.append(item)
    return sorted(files)


def _peek_container_type(path: Path) -> ContainerType | None:
    ext = path.suffix.lower()
    if ext in _TEXT_EXTENSIONS:
        return ContainerType.TEXT
    try:
        with open(path, "rb") as f:
            header = f.read(8192)
        ctype = detect_container(header)
        return ctype if ctype in SUPPORTED_TYPES else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------
def _extract_entities(extracted: ExtractedMetadata) -> list[Any]:
    extractor = EntityExtractor()
    found: list[Any] = []
    if extracted.plane_container is not None:
        found.extend(
            extractor.extract_from_container(
                extracted.plane_container, extracted.artifact_id
            )
        )
    if extracted.plane_application is not None:
        found.extend(
            extractor.extract_from_application(
                extracted.plane_application, extracted.artifact_id
            )
        )
    if extracted.plane_content is not None:
        found.extend(
            extractor.extract_from_content(
                extracted.plane_content, extracted.artifact_id
            )
        )
    return found


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------
def _detect_anomalies(  # noqa: C901
    artifact: Artifact, extracted: ExtractedMetadata
) -> list[AnomalyFinding]:
    findings: list[AnomalyFinding] = []
    now = datetime.now(UTC)
    temporal = extracted.plane_temporal
    fs_meta = extracted.plane_fs or artifact.filesystem_metadata

    if temporal is None or not temporal.events:
        # Metadata-scrubbed heuristic: supported container but no temporal events
        if artifact.container_type in (ContainerType.PDF, ContainerType.OOXML):
            findings.append(
                AnomalyFinding(
                    anomaly_id=_anomaly_id(artifact.artifact_id, "scrubbed"),
                    anomaly_type=AnomalyType.METADATA_SCRUBBED,
                    severity="high",
                    confidence=0.6,
                    description=f"No internal timestamps found in {artifact.original_filename}; possible metadata scrubbing.",
                    affected_artifacts=(artifact.artifact_id,),
                    timestamp_detected=now,
                )
            )
        return findings

    creation_events = [e for e in temporal.events if e.event_type == "creation"]
    modification_events = [e for e in temporal.events if e.event_type == "modification"]
    tz_names = {e.timezone for e in temporal.events if e.timezone}

    # Impossible sequence: modification before creation on same artifact
    for mod_ev in modification_events:
        for cre_ev in creation_events:
            if mod_ev.timestamp < cre_ev.timestamp:
                findings.append(
                    AnomalyFinding(
                        anomaly_id=_anomaly_id(
                            artifact.artifact_id, "impossible_sequence"
                        ),
                        anomaly_type=AnomalyType.IMPOSSIBLE_SEQUENCE,
                        severity="critical",
                        confidence=0.9,
                        description=(
                            f"Modification timestamp ({mod_ev.timestamp.isoformat()}) precedes "
                            f"creation timestamp ({cre_ev.timestamp.isoformat()}) for {artifact.original_filename}."
                        ),
                        affected_artifacts=(artifact.artifact_id,),
                        timestamp_detected=now,
                    )
                )
                break

    for cre_ev in creation_events:
        ts = cre_ev.timestamp

        # Future-dated
        if ts > now:
            findings.append(
                AnomalyFinding(
                    anomaly_id=_anomaly_id(artifact.artifact_id, "future_dated"),
                    anomaly_type=AnomalyType.FUTURE_DATED,
                    severity="critical",
                    confidence=0.95,
                    description=f"Creation timestamp {ts.isoformat()} is in the future for {artifact.original_filename}.",
                    affected_artifacts=(artifact.artifact_id,),
                    timestamp_detected=now,
                )
            )

        # Backdated: document claims creation before filesystem birth
        if fs_meta is not None and fs_meta.birth_time is not None:
            delta = (fs_meta.birth_time - ts).total_seconds()
            if delta > 86400:
                findings.append(
                    AnomalyFinding(
                        anomaly_id=_anomaly_id(artifact.artifact_id, "backdated"),
                        anomaly_type=AnomalyType.BACKDATED,
                        severity="critical",
                        confidence=0.8,
                        description=(
                            f"Document creation ({ts.isoformat()}) is more than 1 day before "
                            f"filesystem birth time ({fs_meta.birth_time.isoformat()}) for {artifact.original_filename}."
                        ),
                        affected_artifacts=(artifact.artifact_id,),
                        timestamp_detected=now,
                    )
                )

        # Weekend creation
        if ts.weekday() >= 5:
            findings.append(
                AnomalyFinding(
                    anomaly_id=_anomaly_id(artifact.artifact_id, "weekend"),
                    anomaly_type=AnomalyType.WEEKEND_CREATION,
                    severity="medium",
                    confidence=0.7,
                    description=f"Document created on weekend ({ts.strftime('%A')}) for {artifact.original_filename}.",
                    affected_artifacts=(artifact.artifact_id,),
                    timestamp_detected=now,
                )
            )

        # After-hours creation
        if ts.hour < 7 or ts.hour >= 18:
            findings.append(
                AnomalyFinding(
                    anomaly_id=_anomaly_id(artifact.artifact_id, "after_hours"),
                    anomaly_type=AnomalyType.AFTER_HOURS_CREATION,
                    severity="medium",
                    confidence=0.6,
                    description=f"Document created outside business hours ({ts.hour:02d}:{ts.minute:02d}) for {artifact.original_filename}.",
                    affected_artifacts=(artifact.artifact_id,),
                    timestamp_detected=now,
                )
            )

    # Timezone inconsistency
    if len(tz_names) > 1:
        findings.append(
            AnomalyFinding(
                anomaly_id=_anomaly_id(artifact.artifact_id, "timezone"),
                anomaly_type=AnomalyType.TIMEZONE_INCONSISTENCY,
                severity="medium",
                confidence=0.65,
                description=f"Multiple timezones detected ({', '.join(sorted(tz_names))}) in {artifact.original_filename}.",
                affected_artifacts=(artifact.artifact_id,),
                timestamp_detected=now,
            )
        )

    return findings


def _anomaly_id(artifact_id: str, kind: str) -> str:
    return hashlib.sha256(f"{artifact_id}:{kind}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Timeline construction
# ---------------------------------------------------------------------------
def _build_timeline(extracted: ExtractedMetadata) -> list[TimelineEntry]:
    entries: list[TimelineEntry] = []
    temporal = extracted.plane_temporal
    if temporal is None:
        return entries
    for idx, event in enumerate(temporal.events):
        entries.append(
            TimelineEntry(
                entry_id=f"{extracted.artifact_id}-{idx:04d}",
                timestamp=event.timestamp,
                event_type=event.event_type,
                artifact_id=extracted.artifact_id,
                description=f"{event.source_plane}.{event.source_field}: {event.raw_value}",
                confidence=event.confidence,
                sources=(event.source_plane,),
            )
        )
    return entries


# ---------------------------------------------------------------------------
# Plain-text evidence extraction (.txt/.csv/.json/.html/.md/.db)
# ---------------------------------------------------------------------------
def _is_text_artifact(artifact: Artifact) -> bool:
    return (
        Path(artifact.source_path).suffix.lower() in _TEXT_EXTENSIONS
        or artifact.container_type == ContainerType.TEXT
    )


def _read_text_sample(path: Path, max_bytes: int = _TEXT_SCAN_MAX_BYTES) -> str:
    try:
        data = path.read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("Cannot read text sample from %s: %s", path, exc)
        return ""


def _extract_patterns(text: str) -> dict[str, tuple[str, ...]]:
    return {
        "email_addresses": tuple(sorted(set(_EMAIL_RE.findall(text)))),
        "embedded_urls": tuple(sorted(set(_URL_RE.findall(text)))),
        "ip_addresses": tuple(sorted(set(_IPV4_RE.findall(text)))),
        "mac_addresses": tuple(sorted(set(_MAC_RE.findall(text)))),
        "phone_numbers": tuple(sorted(set(_PHONE_RE.findall(text)))),
        "social_security_numbers": tuple(sorted(set(_SSN_RE.findall(text)))),
    }


def _extract_text_entities(artifact: Artifact, text: str) -> list[Any]:
    extractor = EntityExtractor()
    patterns = _extract_patterns(text)
    content = ContentMetadata(
        email_addresses=patterns["email_addresses"],
        embedded_urls=patterns["embedded_urls"],
        ip_addresses=patterns["ip_addresses"],
        mac_addresses=patterns["mac_addresses"],
        phone_numbers=patterns["phone_numbers"],
        social_security_numbers=patterns["social_security_numbers"],
        character_count=len(text),
        word_count=len(text.split()),
        line_count=text.count("\n"),
    )
    return extractor.extract_from_content(content, artifact.artifact_id)


def _extract_csv_rows(path: Path) -> list[str]:
    rows: list[str] = []
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            for idx, row in enumerate(csv.reader(f)):
                if idx >= 5000:
                    break
                rows.extend(row)
    except Exception as exc:
        logger.debug("CSV parse failed for %s: %s", path, exc)
    return rows


def _extract_db_text(path: Path) -> str:
    fragments: list[str] = []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.text_factory = str
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall() if r[0]]
        for table in tables[:20]:
            try:
                cursor.execute(f'SELECT * FROM "{table}" LIMIT 100')  # noqa: S608
                for row in cursor.fetchall():
                    for cell in row:
                        if isinstance(cell, str):
                            fragments.append(cell)
                        elif isinstance(cell, bytes):
                            fragments.append(cell.decode("utf-8", errors="replace"))
            except Exception as exc:
                logger.debug("SQLite sampling failed for %s.%s: %s", path, table, exc)
        conn.close()
    except Exception as exc:
        logger.debug("SQLite open failed for %s: %s", path, exc)
    return "\n".join(fragments)


def _extract_plain_text_evidence(
    artifact: Artifact,
) -> tuple[list[Any], list[AnomalyFinding], list[TimelineEntry]]:
    """Extract entities and anomalies from plain-text / SQLite evidence files."""
    path = Path(artifact.source_path)
    ext = path.suffix.lower()

    if ext == ".csv":
        text = "\n".join(_extract_csv_rows(path))
    elif ext in (".db", ".sqlite", ".sqlite3"):
        text = _extract_db_text(path)
    else:
        text = _read_text_sample(path)

    if not text:
        return [], [], []

    entities = _extract_text_entities(artifact, text)

    findings: list[AnomalyFinding] = []
    now = datetime.now(UTC)
    patterns = _extract_patterns(text)
    total_hits = sum(len(v) for v in patterns.values())

    if total_hits:
        # Not an anomaly per se, but worth surfacing as a low-severity intelligence hit
        # so the report shows these files were not ignored.
        hits = ", ".join(f"{k}={len(v)}" for k, v in patterns.items() if v)
        findings.append(
            AnomalyFinding(
                anomaly_id=_anomaly_id(artifact.artifact_id, "text_intelligence"),
                anomaly_type=AnomalyType.PLAINTEXT_EVIDENCE,
                severity="low",
                confidence=0.5,
                description=f"Text evidence {artifact.original_filename} contains: {hits}.",
                affected_artifacts=(artifact.artifact_id,),
                timestamp_detected=now,
            )
        )

    # SQLite anomaly: database evidence file present
    if ext in (".db", ".sqlite", ".sqlite3"):
        findings.append(
            AnomalyFinding(
                anomaly_id=_anomaly_id(artifact.artifact_id, "sqlite_evidence"),
                anomaly_type=AnomalyType.EMBEDDED_FILE_CONCEALMENT,
                severity="medium",
                confidence=0.7,
                description=f"SQLite database evidence file: {artifact.original_filename} ({artifact.size_bytes} bytes).",
                affected_artifacts=(artifact.artifact_id,),
                timestamp_detected=now,
            )
        )

    return entities, findings, []


# ---------------------------------------------------------------------------
# Deep recovery / Option B detection
# ---------------------------------------------------------------------------
def _detect_deep_content(  # noqa: C901
    artifact: Artifact,
    extracted: ExtractedMetadata,
    case_id: str,
    operator: str,
) -> list[AnomalyFinding]:
    """Run Option B deep recovery on OOXML and PDF artifacts."""
    findings: list[AnomalyFinding] = []
    now = datetime.now(UTC)
    path = Path(artifact.source_path)

    if artifact.container_type == ContainerType.OOXML and _DEEP_IMPORTS_OK:
        try:
            ref = recover_revisions(source=path, case_id=case_id, operator=operator)
            if ref.audit_path.exists():
                report = json.loads(ref.audit_path.read_text(encoding="utf-8"))
                revisions = report.get("revision_history", [])
                comments = report.get("comments", [])
                previous = report.get("previous_versions", [])

                deletions = [
                    r
                    for r in revisions
                    if r.get("revision_type") == "deletion" and r.get("text_before")
                ]
                if deletions:
                    snippets = [d["text_before"][:120] for d in deletions[:3]]
                    findings.append(
                        AnomalyFinding(
                            anomaly_id=_anomaly_id(
                                artifact.artifact_id, "deleted_content"
                            ),
                            anomaly_type=AnomalyType.DELETED_CONTENT_RECOVERED,
                            severity="critical",
                            confidence=0.9,
                            description=(
                                f"Recovered {len(deletions)} deletions in {artifact.original_filename}. "
                                f"Snippets: {' | '.join(snippets)}"
                            ),
                            affected_artifacts=(artifact.artifact_id,),
                            timestamp_detected=now,
                        )
                    )

                accepted = [r for r in revisions if r.get("is_accepted")]
                if accepted:
                    findings.append(
                        AnomalyFinding(
                            anomaly_id=_anomaly_id(
                                artifact.artifact_id, "accepted_revisions"
                            ),
                            anomaly_type=AnomalyType.HIDDEN_REVISIONS,
                            severity="high",
                            confidence=0.85,
                            description=(
                                f"{len(accepted)} accepted/hidden revisions in {artifact.original_filename}."
                            ),
                            affected_artifacts=(artifact.artifact_id,),
                            timestamp_detected=now,
                        )
                    )

                if comments:
                    authors = sorted({c.get("author") or "unknown" for c in comments})
                    findings.append(
                        AnomalyFinding(
                            anomaly_id=_anomaly_id(artifact.artifact_id, "comments"),
                            anomaly_type=AnomalyType.COMMENTS_DETECTED,
                            severity="low",
                            confidence=0.8,
                            description=(
                                f"{len(comments)} comments in {artifact.original_filename}; authors: {', '.join(authors)}."
                            ),
                            affected_artifacts=(artifact.artifact_id,),
                            timestamp_detected=now,
                        )
                    )

                if previous:
                    findings.append(
                        AnomalyFinding(
                            anomaly_id=_anomaly_id(
                                artifact.artifact_id, "previous_versions"
                            ),
                            anomaly_type=AnomalyType.PREVIOUS_VERSIONS,
                            severity="medium",
                            confidence=0.7,
                            description=(
                                f"{len(previous)} previous version entries in metadata of {artifact.original_filename}."
                            ),
                            affected_artifacts=(artifact.artifact_id,),
                            timestamp_detected=now,
                        )
                    )
        except Exception as exc:
            logger.debug("Deep revision recovery failed for %s: %s", path, exc)

    if artifact.container_type == ContainerType.PDF and _DEEP_IMPORTS_OK:
        try:
            pharos = PdfPharosEngine().classify(path)
            hidden = HiddenLayerDetector().inspect(path)
            meta = MetadataExtractor().extract(path)

            if pharos.is_redacted:
                findings.append(
                    AnomalyFinding(
                        anomaly_id=_anomaly_id(artifact.artifact_id, "redacted"),
                        anomaly_type=AnomalyType.REDACTION_ANNOTATIONS,
                        severity="high",
                        confidence=0.9,
                        description=f"Redaction annotations detected in {artifact.original_filename}.",
                        affected_artifacts=(artifact.artifact_id,),
                        timestamp_detected=now,
                    )
                )

            total_hidden = hidden.total_hidden_layers
            if total_hidden:
                details = []
                if hidden.optional_content_layers:
                    details.append(
                        f"{hidden.optional_content_layers} optional-content layers"
                    )
                if hidden.embedded_files:
                    details.append(f"{hidden.embedded_files} embedded files")
                if hidden.javascript_actions:
                    details.append(f"{hidden.javascript_actions} JavaScript actions")
                if hidden.annotation_count:
                    details.append(f"{hidden.annotation_count} annotations")
                findings.append(
                    AnomalyFinding(
                        anomaly_id=_anomaly_id(artifact.artifact_id, "hidden_layers"),
                        anomaly_type=AnomalyType.HIDDEN_LAYERS,
                        severity=(
                            "high"
                            if hidden.embedded_files or hidden.javascript_actions
                            else "medium"
                        ),
                        confidence=0.8,
                        description=(
                            f"Hidden/covert content in {artifact.original_filename}: "
                            + "; ".join(details)
                        ),
                        affected_artifacts=(artifact.artifact_id,),
                        timestamp_detected=now,
                    )
                )

            if meta.encrypted:
                findings.append(
                    AnomalyFinding(
                        anomaly_id=_anomaly_id(artifact.artifact_id, "encrypted"),
                        anomaly_type=AnomalyType.ENCRYPTION_INTERMITTENT,
                        severity="high",
                        confidence=0.95,
                        description=f"PDF is encrypted: {artifact.original_filename}.",
                        affected_artifacts=(artifact.artifact_id,),
                        timestamp_detected=now,
                    )
                )

            # Parse PDF Info dates for temporal anomalies
            for date_field, event_name in (
                ("created", "creation"),
                ("modified", "modification"),
            ):
                raw = getattr(meta, date_field, None)
                if raw:
                    ts = _parse_pdf_info_date(raw)
                    if ts:
                        findings.extend(
                            _temporal_anomalies_for_timestamp(
                                artifact,
                                ts,
                                event_name,
                                source=f"pdf_info.{date_field}",
                            )
                        )
        except Exception as exc:
            logger.debug("PDF deep inspection failed for %s: %s", path, exc)

    return findings


def _parse_pdf_info_date(raw: str) -> datetime | None:
    """Best-effort parse of PDF Info dictionary date strings."""
    from anchorum.forensic.core.extraction.pdf import parse_pdf_date

    try:
        return parse_pdf_date(raw)
    except Exception:
        return None


def _temporal_anomalies_for_timestamp(
    artifact: Artifact,
    ts: datetime,
    event_name: str,
    source: str,
) -> list[AnomalyFinding]:
    findings: list[AnomalyFinding] = []
    now = datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    if ts > now:
        findings.append(
            AnomalyFinding(
                anomaly_id=_anomaly_id(artifact.artifact_id, f"future_{source}"),
                anomaly_type=AnomalyType.FUTURE_DATED,
                severity="critical",
                confidence=0.95,
                description=f"Future {event_name} timestamp ({ts.isoformat()}) from {source} in {artifact.original_filename}.",
                affected_artifacts=(artifact.artifact_id,),
                timestamp_detected=now,
            )
        )
    if ts.weekday() >= 5:
        findings.append(
            AnomalyFinding(
                anomaly_id=_anomaly_id(artifact.artifact_id, f"weekend_{source}"),
                anomaly_type=AnomalyType.WEEKEND_CREATION,
                severity="medium",
                confidence=0.7,
                description=f"{event_name.capitalize()} on weekend ({ts.strftime('%A')}) from {source} in {artifact.original_filename}.",
                affected_artifacts=(artifact.artifact_id,),
                timestamp_detected=now,
            )
        )
    if ts.hour < 7 or ts.hour >= 18:
        findings.append(
            AnomalyFinding(
                anomaly_id=_anomaly_id(artifact.artifact_id, f"afterhours_{source}"),
                anomaly_type=AnomalyType.AFTER_HOURS_CREATION,
                severity="medium",
                confidence=0.6,
                description=f"{event_name.capitalize()} outside business hours ({ts.hour:02d}:{ts.minute:02d}) from {source} in {artifact.original_filename}.",
                affected_artifacts=(artifact.artifact_id,),
                timestamp_detected=now,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------
def _severity_rank(severity: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(severity, 5)


def _build_report(
    *,
    case_id: str,
    operator: str,
    artifacts: list[Artifact],
    extracted_list: list[ExtractedMetadata],
    entities: list[Any],
    anomalies: list[AnomalyFinding],
    timeline: list[TimelineEntry],
    runtime_ok: bool,
    hold_id: str | None,
    integrity_report: dict[str, Any] | None,
) -> InvestigationReport:
    by_severity: dict[str, list[AnomalyFinding]] = {
        "critical": [],
        "high": [],
        "medium": [],
        "low": [],
        "info": [],
    }
    for a in anomalies:
        by_severity.setdefault(a.severity, []).append(a)

    report_id = hashlib.sha256(
        json.dumps(
            {
                "case_id": case_id,
                "operator": operator,
                "artifact_ids": sorted(a.artifact_id for a in artifacts),
                "generated_at": datetime.now(UTC).isoformat(),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:32]

    return InvestigationReport(
        report_id=report_id,
        case_id=case_id,
        generated_at=datetime.now(UTC),
        operator=operator,
        artifact_count=len(artifacts),
        entity_count=len(entities),
        anomaly_count=len(anomalies),
        critical_findings=tuple(
            sorted(by_severity["critical"], key=lambda x: x.anomaly_id)
        ),
        high_findings=tuple(sorted(by_severity["high"], key=lambda x: x.anomaly_id)),
        medium_findings=tuple(
            sorted(by_severity["medium"], key=lambda x: x.anomaly_id)
        ),
        low_findings=tuple(sorted(by_severity["low"], key=lambda x: x.anomaly_id)),
        info_findings=tuple(sorted(by_severity["info"], key=lambda x: x.anomaly_id)),
        master_timeline=tuple(sorted(timeline, key=lambda e: e.timestamp)),
        entity_directory=tuple(entities),
        methodology_version=METHODOLOGY_VERSION,
        limitations=(
            (
                "Standalone mode: no runtime provenance"
                if not runtime_ok
                else "Runtime-integrated provenance enabled"
            ),
            f"Litigation hold: {hold_id or 'none'}",
            f"Integrity gate: {integrity_report['status'] if integrity_report else 'not run'}",
        ),
    )


# ---------------------------------------------------------------------------
# Runtime persistence helpers
# ---------------------------------------------------------------------------
def _commit_to_journal(
    runtime: dict[str, Any],
    journal: Any,
    report: InvestigationReport,
    report_dict: dict[str, Any],
    artifacts: list[Artifact],
    organization_id: str,
    operator: str,
) -> None:
    command = runtime["GenerateDossierCommand"](
        organization_id=organization_id,
        case_id=report.case_id,
        actor_id=operator,
        input_fingerprint=report.report_id,
        engine_version=METHODOLOGY_VERSION,
        policy_version="1.0.0",
        input_payload={
            "case_id": report.case_id,
            "artifact_count": report.artifact_count,
            "artifact_ids": sorted(a.artifact_id for a in artifacts),
        },
        causality_id=report.report_id,
        request_id=None,
    )
    timestamp_ns = int(time.time() * 1e9)
    audit_event = runtime["AuditEvent"](
        organization_id=organization_id,
        case_id=report.case_id,
        version_id=report.report_id,
        event_type="anchorum_forensic_report_generated",
        event_id=f"{report.report_id}-evt-0",
        timestamp_ns=timestamp_ns,
        event_schema_version="1.0.0",
        event_seq=0,
        causality_id=report.report_id,
        payload={
            "artifact_count": report.artifact_count,
            "entity_count": report.entity_count,
            "anomaly_count": report.anomaly_count,
            "critical_count": len(report.critical_findings),
            "high_count": len(report.high_findings),
        },
    )
    journal.commit_generate_t2(
        command=command,
        computed_data=report_dict,
        version_number=1,
        version_id=report.report_id,
        case_next_state=runtime["CaseState"].archived.value,
        events=(audit_event,),
        outbox_entries=(),
        idempotency_fingerprint=report.report_id,
        usage_deltas=(),
        timestamp_ns=timestamp_ns,
    )


def _fossilize_report(
    runtime: dict[str, Any],
    archive: Any,
    report: InvestigationReport,
    elapsed_seconds: float,
) -> str:
    agency_id = runtime["AgencyId"](
        species=runtime["Species"].INTELLIGENCE,
        biome=runtime["Biome"].WILDERNESS,
        lobe=runtime["Lobe"].MEMORY,
        instance_tag=report.case_id,
    )
    agency = runtime["AgencyState"](
        agency_id=agency_id,
        alive=False,
        energy_consumed_j=elapsed_seconds * 10.0,
        work_units_processed=report.artifact_count,
        work_units_quarantined=report.anomaly_count,
        birth_timestamp_ns=int(time.time() * 1e9 - elapsed_seconds * 1e9),
        death_timestamp_ns=int(time.time() * 1e9),
    )
    return archive.fossilize(agency)


# ---------------------------------------------------------------------------
# Main batch runner
# ---------------------------------------------------------------------------
def run_batch(  # noqa: C901
    input_dir: Path,
    output_path: Path,
    case_id: str,
    operator: str,
    *,
    signing_key_hex: str | None = None,
    zarc_path: Path | None = None,
    organization_id: str = "anchorum",
    enforce_readonly: bool = False,
    max_file_bytes: int = _MAX_FILE_BYTES,
    hold_api: Callable[..., str] | None = None,
    vault_ingest: Callable[..., Any] | None = None,
    deep_revision: bool = False,
    verbose: bool = False,
    llm_model_id: str | None = None,
    llm_temperature: float | None = None,
    llm_top_p: float | None = None,
    llm_seed: int | None = None,
) -> dict[str, Any]:
    validate_case_id(case_id)
    validate_operator(operator)

    input_dir = input_dir.resolve()
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    runtime = _try_import_runtime()
    key = (signing_key_hex or os.environ.get("ANCHORUM_SIGNING_KEY", "")).strip()
    runtime_ok = (
        runtime is not None
        and len(key) == 64
        and all(c in "0123456789abcdefABCDEF" for c in key)
    )
    if runtime and not runtime_ok:
        logger.warning(
            "Runtime present but no valid 64-char hex ANCHORUM_SIGNING_KEY; falling back to standalone mode."
        )

    hold_id: str | None = None
    integrity_report: dict[str, Any] | None = None
    provenance: Any | None = None
    journal: Any | None = None
    signer: Any | None = None
    archive: Any | None = None
    bridge: Any | None = None

    if runtime_ok:
        # Integrity gate (graceful)
        try:
            integrity_report = runtime["run_anchorum_check"]()
            logger.info("ANCHORUM integrity gate: %s", integrity_report["status"])
        except Exception as exc:
            logger.warning("ANCHORUM integrity gate failed (continuing): %s", exc)
            integrity_report = {"status": "FAIL", "error": str(exc)}

        # Litigation hold
        try:
            trigger = runtime["LitigationHoldTrigger"](
                anchorum_hold_api=hold_api or _default_hold_api
            )
            hold_id = trigger.trigger(
                case_id=case_id,
                scope=[str(input_dir)],
                reason=f"ANCHORUM forensic batch run by {operator}",
            )
        except Exception as exc:
            logger.warning("Litigation hold failed (continuing): %s", exc)

        # Provenance / journal / signer / archive / bridge
        zarc_path = (
            zarc_path
            or Path(
                os.environ.get(
                    "ANCHORUM_ZARC_PATH", output_path.parent / f"{case_id}.zarc"
                )
            )
        ).resolve()
        provenance = runtime["Provenance"](zarc_path, signing_key_hex=key)
        journal_path = zarc_path.parent / f"{case_id}.journal.zarc"
        journal = runtime["ZarcJournal"](zarc_path=journal_path, signing_key_hex=key)
        signer = runtime["DagSigner"](signing_key_hex=key)
        archive = runtime["SedimentArchive"](node_id="anchorum")
        bridge = runtime["AnchorumBridge"](
            zarc_path=zarc_path, vault_ingest=vault_ingest or _default_vault_ingest
        )
        logger.info("Runtime integration enabled; zarc=%s", zarc_path)

    start_time = time.monotonic()

    files = _iter_evidence_files(input_dir)
    logger.info("Found %d candidate files under %s", len(files), input_dir)

    artifacts: list[Artifact] = []
    extracted_list: list[ExtractedMetadata] = []
    raw_entities: list[Any] = []
    anomalies: list[AnomalyFinding] = []
    timeline: list[TimelineEntry] = []
    skipped = {"unsupported": 0, "too_large": 0, "ingestion_error": 0}

    for idx, path in enumerate(files, 1):
        try:
            if path.stat().st_size > max_file_bytes:
                skipped["too_large"] += 1
                continue

            ctype = _peek_container_type(path)
            if ctype is None:
                skipped["unsupported"] += 1
                continue

            artifact = ingest_artifact(
                path, case_id, operator, enforce_readonly=enforce_readonly
            )
            artifacts.append(artifact)

            ext = path.suffix.lower()
            if ext in _TEXT_EXTENSIONS:
                # Route plain-text evidence through our own scanner instead of the
                # binary-format extractors.
                artifact = dataclasses.replace(
                    artifact, container_type=ContainerType.TEXT
                )
                extracted = ExtractedMetadata(
                    artifact_id=artifact.artifact_id,
                    extraction_time=datetime.now(UTC),
                    plane_fs=artifact.filesystem_metadata,
                )
                extracted_list.append(extracted)
                text_entities, text_findings, _ = _extract_plain_text_evidence(artifact)
                raw_entities.extend(text_entities)
                anomalies.extend(text_findings)
            else:
                extracted = extract_from_artifact(
                    artifact, case_id=case_id, operator=operator
                )
                extracted_list.append(extracted)

                raw_entities.extend(_extract_entities(extracted))
                anomalies.extend(_detect_anomalies(artifact, extracted))
                if deep_revision:
                    anomalies.extend(
                        _detect_deep_content(artifact, extracted, case_id, operator)
                    )
                timeline.extend(_build_timeline(extracted))

            if provenance is not None:
                provenance.append(
                    engine=ENGINE,
                    event="artifact_extracted",
                    payload={
                        "case_id": case_id,
                        "operator": operator,
                        "artifact_id": artifact.artifact_id,
                        "source_path": str(artifact.source_path),
                        "container_type": artifact.container_type.value,
                        "mime_type": artifact.mime_type,
                        "size_bytes": artifact.size_bytes,
                        "extraction_errors": list(extracted.extraction_errors),
                    },
                )

            if idx % 100 == 0:
                logger.info(
                    "Processed %d supported artifacts (%d skipped)",
                    idx,
                    sum(skipped.values()),
                )
        except IngestionError as exc:
            skipped["ingestion_error"] += 1
            logger.warning("Skipping %s: %s", path, exc)
        except Exception as exc:
            skipped["ingestion_error"] += 1
            logger.warning("Unexpected error processing %s: %s", path, exc)

    entities = merge_entities(raw_entities)

    # Deduplicate surface anomalies per (artifact, type) — multiple internal
    # timestamps on the same image were creating duplicate findings.
    deduped_anomalies: dict[tuple[str, str], AnomalyFinding] = {}
    for finding in anomalies:
        key = (",".join(finding.affected_artifacts), finding.anomaly_type.value)
        if key not in deduped_anomalies:
            deduped_anomalies[key] = finding
    anomalies = list(deduped_anomalies.values())

    report = _build_report(
        case_id=case_id,
        operator=operator,
        artifacts=artifacts,
        extracted_list=extracted_list,
        entities=entities,
        anomalies=anomalies,
        timeline=timeline,
        runtime_ok=runtime_ok,
        hold_id=hold_id,
        integrity_report=integrity_report,
    )

    # Build the canonical report dict. We will sign only the deterministic
    # subset (excluding any LLM enrichment) so that model-generated claims are
    # never part of the cryptographic artifact.
    report_dict = to_canonical_json(report)
    deterministic_report_dict = {
        k: v
        for k, v in report_dict.items()
        if k not in {"llm_summary", "llm_model_id", "unverified_enrichment"}
    }
    signed_report = dict(deterministic_report_dict)
    signature_info: dict[str, Any] | None = None

    if signer is not None:
        dag_sig = signer.sign(deterministic_report_dict)
        signed_report = runtime["merge_payload_for_signature"](
            deterministic_report_dict,
            digest_hex=dag_sig.digest_hex,
            sig_hex=dag_sig.sig_hex,
        )
        signature_info = {
            "verify_key_hex": dag_sig.key_hex,
            "digest_hex": dag_sig.digest_hex,
            "sig_hex": dag_sig.sig_hex,
        }
        if provenance is not None:
            provenance.append(
                engine=ENGINE,
                event="report_signed",
                payload={
                    "case_id": case_id,
                    "operator": operator,
                    "report_id": report.report_id,
                    "digest_hex": dag_sig.digest_hex,
                    "sig_hex": dag_sig.sig_hex,
                    "verify_key_hex": dag_sig.key_hex,
                },
            )

    # Write the deterministic, signed report first. LLM enrichment (if any)
    # is applied afterwards and clearly marked as unverified.
    output_path.write_text(
        json.dumps(signed_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Optional Egregore-model enrichment: case narrative / summary.
    llm_result_dict: dict[str, Any] | None = None
    llm_enrichment_path: Path | None = None
    effective_llm_model_id = llm_model_id or os.environ.get("ANCHORUM_LLM_MODEL_ID")
    if effective_llm_model_id:
        try:
            client = EgregoreModelClient(
                model_id=effective_llm_model_id,
                temperature=llm_temperature,
                top_p=llm_top_p,
                seed=llm_seed,
            )
            report_text = json.dumps(
                deterministic_report_dict, indent=2, ensure_ascii=False
            )
            llm_result = client.summarize_findings(report_text)
            llm_result_dict = llm_result.to_dict()
            if llm_result.ok:
                enriched_report = dict(signed_report)
                enriched_report["llm_summary"] = llm_result.narrative
                enriched_report["llm_model_id"] = (
                    llm_result.resolved_model_id or llm_result.model_id
                )
                enriched_report["unverified_enrichment"] = True
                output_path.write_text(
                    json.dumps(enriched_report, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                report = dataclasses.replace(
                    report,
                    llm_summary=llm_result.narrative,
                    llm_model_id=llm_result.resolved_model_id or llm_result.model_id,
                    unverified_enrichment=True,
                )
                logger.info(
                    "LLM summary generated by %s in %.1f ms (%d tokens, schema_valid=%s)",
                    llm_result.resolved_model_id or llm_result.model_id,
                    llm_result.latency_ms,
                    llm_result.tokens_generated,
                    llm_result.schema_valid,
                )
            else:
                logger.warning("LLM summary unavailable: %s", llm_result.error)
            llm_enrichment_path = output_path.with_suffix(".llm_enrichment.json")
            llm_enrichment_path.write_text(
                json.dumps(llm_result_dict, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM summary generation failed: %s", exc)
            llm_result_dict = {"ok": False, "error": str(exc)}

    sediment_id: str | None = None
    if runtime_ok and journal is not None and archive is not None:
        try:
            _commit_to_journal(
                runtime,
                journal,
                report,
                report_dict,
                artifacts,
                organization_id,
                operator,
            )
            logger.info("Case snapshot committed to ZarcJournal")
        except Exception as exc:
            logger.warning("ZarcJournal commit failed (continuing): %s", exc)

        try:
            elapsed = time.monotonic() - start_time
            sediment_id = _fossilize_report(runtime, archive, report, elapsed)
            logger.info("Investigation report fossilized: %s", sediment_id)
        except Exception as exc:
            logger.warning("SedimentArchive fossilization failed (continuing): %s", exc)

        try:
            bridge.sync(last_n=max(100, len(artifacts) + 10))
            logger.info("AnchorumBridge vault sync complete")
        except Exception as exc:
            logger.warning("AnchorumBridge sync failed (continuing): %s", exc)

    elapsed = time.monotonic() - start_time
    summary = {
        "runtime_mode": "integrated" if runtime_ok else "standalone",
        "deep_revision": deep_revision,
        "case_id": case_id,
        "operator": operator,
        "input_dir": str(input_dir),
        "output_path": str(output_path),
        "zarc_path": str(zarc_path) if zarc_path else None,
        "hold_id": hold_id,
        "integrity_gate": integrity_report,
        "artifact_count": report.artifact_count,
        "entity_count": report.entity_count,
        "anomaly_count": report.anomaly_count,
        "critical_count": len(report.critical_findings),
        "high_count": len(report.high_findings),
        "medium_count": len(report.medium_findings),
        "low_count": len(report.low_findings),
        "info_count": len(report.info_findings),
        "sediment_id": sediment_id,
        "signature": signature_info,
        "skipped": skipped,
        "elapsed_seconds": round(elapsed, 2),
        "report_id": report.report_id,
        "llm_summary": llm_result_dict,
        "llm_enrichment_path": (
            str(llm_enrichment_path) if llm_enrichment_path else None
        ),
    }

    logger.info(
        "Batch complete: artifacts=%d entities=%d anomalies=%d (critical=%d high=%d) -> %s",
        report.artifact_count,
        report.entity_count,
        report.anomaly_count,
        summary["critical_count"],
        summary["high_count"],
        output_path,
    )
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ANCHORUM forensic batch runner (runtime-integrated)"
    )
    parser.add_argument("--input", type=Path, required=True, help="Evidence directory")
    parser.add_argument(
        "--output", type=Path, required=True, help="JSON report output path"
    )
    parser.add_argument("--case-id", required=True, help="Case identifier")
    parser.add_argument("--operator", required=True, help="Operator identifier")
    parser.add_argument(
        "--signing-key",
        default=None,
        help="Ed25519 signing key hex (or ANCHORUM_SIGNING_KEY env)",
    )
    parser.add_argument(
        "--zarc-path",
        type=Path,
        default=None,
        help=".zarc provenance path (or ANCHORUM_ZARC_PATH env)",
    )
    parser.add_argument(
        "--organization-id", default="anchorum", help="Organization id for ZarcJournal"
    )
    parser.add_argument(
        "--enforce-readonly",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Require read-only source files",
    )
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=_MAX_FILE_BYTES,
        help="Skip files larger than N bytes",
    )
    parser.add_argument(
        "--deep-revision",
        action="store_true",
        help="Run Option B deep revision recovery and hidden-content inspection",
    )
    parser.add_argument(
        "--llm-model-id",
        default=None,
        help="Egregore model ID for case narrative summary (or ANCHORUM_LLM_MODEL_ID env)",
    )
    parser.add_argument(
        "--llm-temperature",
        type=float,
        default=None,
        help="LLM sampling temperature (default 0.0)",
    )
    parser.add_argument(
        "--llm-top-p",
        type=float,
        default=None,
        help="LLM nucleus sampling parameter (default 0.95)",
    )
    parser.add_argument(
        "--llm-seed", type=int, default=None, help="LLM random seed (default 42)"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.input.exists():
        logger.error("Input directory does not exist: %s", args.input)
        return 1

    summary = run_batch(
        input_dir=args.input,
        output_path=args.output,
        case_id=args.case_id,
        operator=args.operator,
        signing_key_hex=args.signing_key,
        zarc_path=args.zarc_path,
        organization_id=args.organization_id,
        enforce_readonly=args.enforce_readonly,
        max_file_bytes=args.max_file_bytes,
        deep_revision=args.deep_revision,
        verbose=args.verbose,
        llm_model_id=args.llm_model_id,
        llm_temperature=args.llm_temperature,
        llm_top_p=args.llm_top_p,
        llm_seed=args.llm_seed,
    )

    # Print a compact summary line for callers
    print(
        f"ANCHORUM_BATCH_DONE: artifacts={summary['artifact_count']} "
        f"entities={summary['entity_count']} anomalies={summary['anomaly_count']} "
        f"critical={summary['critical_count']} high={summary['high_count']} "
        f"mode={summary['runtime_mode']} report={summary['output_path']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
