"""Bytes-based chronology builder for ANCHORUM.

Builds a fused timeline from Evidence records without direct filesystem access.
Filesystem metadata comes from Evidence.filesystem_metadata; content-derived
timestamps come from parsing the bytes provided by the port.
"""

from __future__ import annotations

import email.utils
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from asds.domain.models import Evidence
from asds.domain.ports import IAnchorumIngestionPort
from modules.anchorum.ingestion.filetypes import ContainerType, detect_container


@dataclass(frozen=True)
class TimelineEvent:
    timestamp: datetime
    source: str
    event_type: str
    confidence: str = "HIGH"
    artifacts: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)


def _utc_fromtimestamp(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)


def _parse_pdf_date(pdf_date: str) -> datetime | None:
    """Parse a PDF date string (best effort)."""
    if pdf_date.startswith("D:"):
        pdf_date = pdf_date[2:]
    match = re.match(
        r"(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?([+-Z])?(\d{2})?\'?(\d{2})?\'?",
        pdf_date,
    )
    if not match:
        return None
    try:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))
        hour = int(match.group(4)) if match.group(4) else 0
        minute = int(match.group(5)) if match.group(5) else 0
        second = int(match.group(6)) if match.group(6) else 0
        dt = datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None
    tz_sign = match.group(7)
    if tz_sign and tz_sign != "Z":
        from datetime import timedelta, timezone

        offset_h = int(match.group(8)) if match.group(8) else 0
        offset_m = int(match.group(9)) if match.group(9) else 0
        offset = timedelta(hours=offset_h, minutes=offset_m)
        if tz_sign == "-":
            offset = -offset
        dt = dt.replace(tzinfo=timezone(offset))
    else:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _file_metadata_events(evidence: Evidence) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    fs = evidence.filesystem_metadata or {}
    source = evidence.content_ref

    for key, event_type in (
        ("birth_time", "created"),
        ("mod_time", "modified"),
        ("access_time", "accessed"),
        ("ctime", "metadata_changed"),
    ):
        raw = fs.get(key)
        if raw:
            try:
                ts = datetime.fromisoformat(raw)
                events.append(
                    TimelineEvent(
                        timestamp=ts,
                        source=source,
                        event_type=event_type,
                        confidence="HIGH",
                        artifacts=(f"{key}:{raw}",),
                    )
                )
            except ValueError:
                pass

    return events


def _email_events(evidence: Evidence, data: bytes) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    text = data[:262144].decode("utf-8", errors="ignore")

    date_match = re.search(r"^Date:\s*(.+)", text, re.MULTILINE | re.IGNORECASE)
    if date_match:
        date_str = date_match.group(1).strip()
        try:
            parsed = email.utils.parsedate_to_datetime(date_str)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            events.append(
                TimelineEvent(
                    timestamp=parsed,
                    source=evidence.content_ref,
                    event_type="email_sent",
                    confidence="HIGH",
                    artifacts=(f"Date:{date_str}",),
                )
            )
        except (ValueError, LookupError):
            pass

    return events


def _pdf_events(evidence: Evidence, data: bytes) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    text = data[:524288].decode("latin-1", errors="ignore")

    for key, event_type in (("creation_date", "created"), ("mod_date", "modified")):
        pattern = rf"/{key}\s*\(([^)]+)\)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            raw = match.group(1)
            ts = _parse_pdf_date(raw)
            if ts:
                events.append(
                    TimelineEvent(
                        timestamp=ts,
                        source=evidence.content_ref,
                        event_type=event_type,
                        confidence="HIGH",
                        artifacts=(f"{key}:{raw}",),
                    )
                )

    return events


def _events_for_evidence(
    evidence: Evidence, data: bytes
) -> list[TimelineEvent]:
    container = detect_container(data, filename_hint=evidence.original_filename)
    events: list[TimelineEvent] = []
    events.extend(_file_metadata_events(evidence))

    if container == ContainerType.EMAIL:
        events.extend(_email_events(evidence, data))
    elif container == ContainerType.PDF:
        events.extend(_pdf_events(evidence, data))

    return events


def build_chronology(
    evidence_list: list[Evidence],
    port: IAnchorumIngestionPort,
) -> dict[str, Any]:
    """Build a fused timeline for all Evidence in a case."""
    all_events: list[TimelineEvent] = []

    for evidence in evidence_list:
        try:
            data = port.get_evidence_content(evidence.id)
        except Exception as exc:  # noqa: BLE001
            all_events.append(
                TimelineEvent(
                    timestamp=datetime.now(UTC),
                    source=evidence.content_ref,
                    event_type="content_unavailable",
                    confidence="LOW",
                    artifacts=(f"error:{exc}",),
                )
            )
            continue
        all_events.extend(_events_for_evidence(evidence, data))

    all_events.sort(key=lambda e: e.timestamp)

    return {
        "event_count": len(all_events),
        "events": [
            {
                "timestamp": e.timestamp.isoformat(),
                "source": e.source,
                "event_type": e.event_type,
                "confidence": e.confidence,
                "artifacts": list(e.artifacts),
                "metadata": e.metadata,
            }
            for e in all_events
        ],
    }
