"""Parse ANCHORUM extraction outputs and original files into domain artifacts.

The migrated `self_rep_extracted.jsonl` contains rich metadata but does not include
full text bodies. This parser therefore supplements JSONL records with direct
reads of text-parseable originals (.eml, .txt, .csv, .md, .html, .json).
"""

from __future__ import annotations

import email
import re
from datetime import UTC, datetime
from email.policy import default as email_policy
from typing import TYPE_CHECKING, Any

from egregore.domain.self_rep_dossier.dossier_models import Artifact
from egregore.interface.domain_data_ports import DossierDataSource
from egregore.shared.canonical import canonical_loads

if TYPE_CHECKING:
    from egregore.interface.document_extraction_port import DocumentTextExtractorPort


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp, returning None on failure."""
    if not value:
        return None
    # Some timestamps use single-digit offsets; normalize them.
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _to_utc(dt: datetime | None) -> datetime | None:
    """Convert a datetime to UTC, returning None if input is None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _best_timestamp(record: dict[str, Any]) -> datetime | None:  # noqa: C901
    """Choose the most reliable timestamp for an artifact.

    Preference order:
    1. Email Date header
    2. Earliest plane_temporal event
    3. Container EXIF DateTimeOriginal
    4. Filesystem modification time
    5. Extraction time
    """
    container = record.get("plane_container") or {}
    temporal = record.get("plane_temporal") or {}
    fs = record.get("plane_fs") or {}

    if container.get("date"):
        dt = _parse_iso(container["date"])
        if dt:
            return _to_utc(dt)

    events = temporal.get("events") or []
    if events:
        earliest = None
        for ev in events:
            ts = ev.get("timestamp")
            if ts:
                dt = _parse_iso(ts)
                if dt and (earliest is None or dt < earliest):
                    earliest = dt
        if earliest:
            return _to_utc(earliest)

    exif = container.get("exif") or {}
    for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
        if exif.get(key):
            # EXIF format is "YYYY:MM:DD HH:MM:SS"
            try:
                dt = datetime.strptime(exif[key], "%Y:%m:%d %H:%M:%S")
                return _to_utc(dt)
            except ValueError:
                pass

    if fs.get("mod_time"):
        dt = _parse_iso(fs["mod_time"])
        if dt:
            return _to_utc(dt)

    if record.get("extraction_time"):
        dt = _parse_iso(record["extraction_time"])
        if dt:
            return _to_utc(dt)

    return None


def _extract_text_from_eml(
    path: str, dossier_source: DossierDataSource
) -> tuple[str, dict[str, Any]]:
    """Parse a .eml file and return (body_text, headers_dict)."""
    try:
        data = dossier_source.read_bytes(path)
        msg = email.message_from_bytes(data, policy=email_policy.default)
    except Exception:
        return "", {}

    headers = {
        "from": str(msg.get("From", "")),
        "to": [str(a) for a in msg.get("To", "").split(",") if a.strip()],
        "cc": [str(a) for a in msg.get("Cc", "").split(",") if a.strip()],
        "subject": str(msg.get("Subject", "")),
        "date": str(msg.get("Date", "")),
        "message_id": str(msg.get("Message-Id", "")),
        "in_reply_to": str(msg.get("In-Reply-To", "")),
        "references": msg.get("References", "").split(),
    }

    body_parts: list[str] = []
    for part in msg.walk():
        content_type = part.get_content_type()
        if content_type in ("text/plain", "text/html"):
            try:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="ignore")
                    if content_type == "text/html":
                        text = _strip_html(text)
                    body_parts.append(text)
            except (UnicodeDecodeError, LookupError):
                continue

    return "\n".join(body_parts), headers


def _strip_html(html: str) -> str:
    """Very small HTML-to-text stripper."""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&amp;|&lt;|&gt;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _read_text_file(path: str, dossier_source: DossierDataSource) -> str:
    """Read a text file with encoding fallback."""
    for encoding in ("utf-8", "latin-1", "cp1252"):
        try:
            return dossier_source.read_text(path, encoding=encoding)
        except UnicodeDecodeError:
            continue
        except Exception:
            break
    return ""


def _basename(path: str) -> str:
    """Return the final component of a POSIX/Windows path string."""
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _extract_original_text(
    record: dict[str, Any],
    dossier_root: str,
    dossier_source: DossierDataSource,
    document_extractor: DocumentTextExtractorPort | None,
) -> tuple[str, dict[str, Any]]:
    """Try to read original file content for text-parseable formats.

    Returns (content_text, extra_metadata).
    """
    source_path = record.get("source_path", "")
    if not source_path:
        return "", {}

    path = source_path
    if not path.startswith("/"):
        path = f"{dossier_root.rstrip('/')}/{path}"
    if not dossier_source.exists(path):
        # Try relative to dossier root using basename only
        path = f"{dossier_root.rstrip('/')}/{_basename(source_path)}"
    if not dossier_source.exists(path):
        return "", {}

    suffix = (
        _basename(path).rsplit(".", 1)[-1].lower() if "." in _basename(path) else ""
    )
    extra: dict[str, Any] = {}

    if suffix == "eml":
        text, headers = _extract_text_from_eml(path, dossier_source)
        extra["eml_headers"] = headers
        return text, extra

    if suffix in ("txt", "csv", "md", "html", "htm", "json", "xml", "yml", "yaml"):
        return _read_text_file(path, dossier_source), extra

    # 5. PDF / DOCX full-text extraction via available libraries.
    if document_extractor is not None and document_extractor.can_extract(path):
        result = document_extractor.extract(path)
        if result.get("text"):
            extra["document_pages"] = result.get("pages")
            return result["text"], extra
        if result.get("error"):
            extra["document_extraction_error"] = result["error"]
        return "", extra

    # For everything else we rely on the JSONL metadata only.
    return "", extra


def _anomalies_for_artifact(
    artifact_id: str, findings: dict[str, list[dict[str, Any]]]
) -> list[str]:
    """Collect anomaly descriptions that reference this artifact_id."""
    out: list[str] = []
    for severity in ("high_findings", "medium_findings", "low_findings"):
        for finding in findings.get(severity, []):
            affected = finding.get("affected_artifacts") or []
            if artifact_id in affected:
                out.append(f"[{severity}] {finding.get('description', '')}")
    return out


def parse_self_rep_evidence(
    extracted_path: str | None = None,
    report_path: str | None = None,
    dossier_root: str = "/opt/egregore/dossier",
    dossier_source: DossierDataSource | None = None,
    document_extractor: DocumentTextExtractorPort | None = None,
) -> tuple[list[Artifact], dict[str, Any]]:
    """Load all artifacts from the SelfRep extraction plus anomaly report.

    Returns (artifacts, report_metadata).
    """
    if dossier_source is None:
        raise TypeError(
            "dossier_source is required; domain code must not read files directly"
        )

    report: dict[str, Any] = {}
    if report_path and dossier_source.exists(report_path):
        report = canonical_loads(dossier_source.read_text(report_path))

    findings_by_severity: dict[str, list[dict[str, Any]]] = {
        "high_findings": report.get("high_findings", []),
        "medium_findings": report.get("medium_findings", []),
        "low_findings": report.get("low_findings", []),
    }

    artifacts: list[Artifact] = []
    if extracted_path and dossier_source.exists(extracted_path):
        extracted_text = dossier_source.read_text(extracted_path)
        for line in extracted_text.splitlines():
            line = line.strip()
            if not line:
                continue
            record = canonical_loads(line)
            artifact_id = record.get("artifact_id", "")
            source_path = record.get("source_path", "")
            filename = _basename(source_path) if source_path else artifact_id[:16]
            container_type = record.get("container_type", "unknown")

            modality = _infer_modality(container_type, filename)
            timestamp = _best_timestamp(record)
            anomalies = tuple(
                _anomalies_for_artifact(artifact_id, findings_by_severity)
            )
            extraction_errors = tuple(record.get("extraction_errors") or [])

            content_text, extra_meta = _extract_original_text(
                record, dossier_root, dossier_source, document_extractor
            )

            metadata: dict[str, Any] = {
                "mime_type": record.get("mime_type"),
                "extraction_time": record.get("extraction_time"),
            }
            metadata.update(extra_meta)

            # Add container metadata that helps actor/timeline reconstruction.
            container = record.get("plane_container") or {}
            for key in (
                "from_addr",
                "to_addrs",
                "cc_addrs",
                "bcc_addrs",
                "subject",
                "message_id",
                "in_reply_to",
                "references",
                "date",
                "received_chain",
            ):
                value = container.get(key)
                if value:
                    metadata[key] = value

            artifacts.append(
                Artifact(
                    artifact_id=artifact_id,
                    source_path=source_path,
                    filename=filename,
                    modality=modality,
                    timestamp=timestamp,
                    content_text=content_text,
                    metadata=metadata,
                    anomalies=anomalies,
                    extraction_errors=extraction_errors,
                )
            )

    return artifacts, report


def _infer_modality(container_type: str, filename: str) -> str:
    """Map container_type / filename to a coarse modality."""
    ct = (container_type or "").lower()
    fn = filename.lower()
    if ct == "email" or fn.endswith(".eml"):
        return "email"
    if ct in ("pdf", "document") or any(
        fn.endswith(ext) for ext in (".pdf", ".docx", ".doc", ".odt")
    ):
        return "document"
    if ct in ("image", "picture") or any(
        fn.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp")
    ):
        return "image"
    if ct in ("audio",) or fn.endswith(".mp3"):
        return "audio"
    if ct in ("video",) or fn.endswith(".mp4"):
        return "video"
    if any(
        fn.endswith(ext)
        for ext in (".txt", ".md", ".csv", ".json", ".xml", ".yml", ".yaml")
    ):
        return "text"
    return "unknown"
