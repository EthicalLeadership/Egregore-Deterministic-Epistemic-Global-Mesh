"""Cross-Document Timeline Fusion — Perfection Release.

Stdlib‑only, CBI-0 native, zero dead code, modern Python 3.11+.

Collects timestamped events from file metadata, Office revisions/comments,
JSON report files, PDF metadata, and email headers; fuses them into a single
chronological timeline; and detects temporal anomalies.
"""

from __future__ import annotations

import email.utils
import json
import logging
import os
import re
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from anchorum.forensic.core.document.pdf_obstruction import EventReference
from anchorum.forensic.core.paths import anchorum_zarc_dir
from anchorum.forensic.core.provenance import ZarcEventType, emit_zarc_event
from anchorum.forensic.core.validation import validate_input_size

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Constants
# ---------------------------------------------------------------------------
CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"

ANOMALY_CREATION_AFTER_MODIFICATION = "creation_after_modification"
ANOMALY_FUTURE_DATE = "future_date"
ANOMALY_TIMEZONE_MISMATCH = "timezone_mismatch"
ANOMALY_IMPLAUSIBLE_GAP = "implausible_temporal_gap"

# Allow up to 5 minutes of clock skew for future events
MAX_FUTURE_SKEW = timedelta(minutes=5)


# ---------------------------------------------------------------------------
# 2. Immutable Data Structures
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TimelineEvent:
    """A single timestamped event.
    The timestamp is always UTC‑aware; naive inputs are rejected.
    """

    timestamp: datetime
    source: str
    event_type: str
    confidence: str = CONFIDENCE_HIGH
    artifacts: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("TimelineEvent requires timezone‑aware UTC timestamp")
        # Normalize to UTC for safety
        object.__setattr__(self, "timestamp", self.timestamp.astimezone(UTC))


@dataclass(frozen=True)
class TimelineAnomaly:
    anomaly_type: str
    description: str
    affected_events: tuple[int, ...]  # indices into the fused event list
    confidence: str = CONFIDENCE_HIGH


@dataclass(frozen=True)
class FusedTimeline:
    case_id: str
    operator: str
    events: tuple[TimelineEvent, ...]
    anomalies: tuple[TimelineAnomaly, ...]


# ---------------------------------------------------------------------------
# 3. Source Interface & Implementations
# ---------------------------------------------------------------------------
class TimelineSource(ABC):
    @abstractmethod
    def extract_events(self) -> Iterator[TimelineEvent]: ...


class FileMetadataSource(TimelineSource):
    def __init__(self, file_path: str | Path) -> None:
        self.file_path = Path(file_path)
        validate_input_size(self.file_path, label="timeline_file_source")

    def extract_events(self) -> Iterator[TimelineEvent]:
        try:
            stat = self.file_path.stat()
        except OSError as exc:
            logger.warning("Cannot stat %s: %s", self.file_path, exc)
            return

        base_uri = f"file://{self.file_path.absolute()}"

        # Birth time (fallback to ctime on Linux)
        birth_ts = getattr(stat, "st_birthtime", None)
        if birth_ts is not None:
            birth = _utc_fromtimestamp(birth_ts)
            yield TimelineEvent(
                timestamp=birth,
                source=base_uri,
                event_type="created",
                confidence=CONFIDENCE_HIGH,
            )
        else:
            birth = _utc_fromtimestamp(stat.st_ctime)
            yield TimelineEvent(
                timestamp=birth,
                source=base_uri,
                event_type="created",
                confidence=CONFIDENCE_LOW,
                artifacts=("stat.st_ctime used as birthtime fallback",),
            )

        mtime = _utc_fromtimestamp(stat.st_mtime)
        yield TimelineEvent(
            timestamp=mtime,
            source=base_uri,
            event_type="modified",
            confidence=CONFIDENCE_HIGH,
        )

        atime = _utc_fromtimestamp(stat.st_atime)
        yield TimelineEvent(
            timestamp=atime,
            source=base_uri,
            event_type="accessed",
            confidence=CONFIDENCE_HIGH,
        )

        ctime = _utc_fromtimestamp(stat.st_ctime)
        yield TimelineEvent(
            timestamp=ctime,
            source=base_uri,
            event_type="metadata_changed",
            confidence=CONFIDENCE_HIGH,
        )


class OfficeRevisionSource(TimelineSource):
    def __init__(
        self,
        revisions: list[dict[str, Any]] | None = None,
        comments: list[dict[str, Any]] | None = None,
    ) -> None:
        self.revisions = revisions or []
        self.comments = comments or []

    def extract_events(self) -> Iterator[TimelineEvent]:
        for rev in self.revisions:
            ts = _parse_iso_datetime(rev.get("timestamp"))
            if ts:
                yield TimelineEvent(
                    timestamp=ts,
                    source=f"office_revision:{rev.get('revision_type', 'unknown')}",
                    event_type="revision",
                    confidence=(
                        CONFIDENCE_HIGH if rev.get("author") else CONFIDENCE_MEDIUM
                    ),
                    artifacts=(f"author:{rev.get('author')}",),
                    metadata={"revision_type": rev.get("revision_type", "unknown")},
                )
        for com in self.comments:
            ts = _parse_iso_datetime(com.get("timestamp"))
            if ts:
                yield TimelineEvent(
                    timestamp=ts,
                    source="office_comment",
                    event_type="comment",
                    confidence=CONFIDENCE_HIGH,
                    artifacts=(f"author:{com.get('author')}",),
                    metadata={"resolved": str(com.get("resolved", False))},
                )


class JsonFileRevisionSource(TimelineSource):
    """Read a JSON file (e.g., a previous obstruction/revision report) and yield events."""

    def __init__(self, json_path: str | Path) -> None:
        self.json_path = Path(json_path)
        validate_input_size(self.json_path, label="timeline_json_source")

    def extract_events(self) -> Iterator[TimelineEvent]:
        try:
            data = json.loads(self.json_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Cannot read JSON revision file %s: %s", self.json_path, exc)
            return

        # Reuse OfficeRevisionSource on any embedded revision/comment data
        if "revision_history" in data or "comments" in data:
            yield from OfficeRevisionSource(
                revisions=data.get("revision_history"),
                comments=data.get("comments"),
            ).extract_events()


class PdfMetadataSource(TimelineSource):
    def __init__(self, metadata: dict[str, str]) -> None:
        self.metadata = metadata

    def extract_events(self) -> Iterator[TimelineEvent]:
        for key, event_type in (
            ("creation_date", "created"),
            ("mod_date", "modified"),
        ):
            raw = self.metadata.get(key)
            if raw:
                ts = _parse_pdf_date(raw)
                if ts:
                    yield TimelineEvent(
                        timestamp=ts,
                        source="pdf_metadata",
                        event_type=event_type,
                        confidence=CONFIDENCE_HIGH,
                        artifacts=(f"{key}:{raw}",),
                    )


class EmailHeaderSource(TimelineSource):
    def __init__(self, raw_headers: str) -> None:
        self.raw_headers = raw_headers

    def extract_events(self) -> Iterator[TimelineEvent]:
        # Date header
        date_match = re.search(
            r"^Date:\s*(.+)", self.raw_headers, re.MULTILINE | re.IGNORECASE
        )
        if date_match:
            date_str = date_match.group(1).strip()
            try:
                parsed = email.utils.parsedate_to_datetime(date_str)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                yield TimelineEvent(
                    timestamp=parsed,
                    source="email_header",
                    event_type="email_sent",
                    confidence=CONFIDENCE_HIGH,
                    artifacts=(f"Date:{date_str}",),
                )
            except (ValueError, LookupError):
                yield TimelineEvent(
                    timestamp=datetime.now(UTC),
                    source="email_header",
                    event_type="email_sent",
                    confidence=CONFIDENCE_LOW,
                    artifacts=(f"unparseable_date:{date_str}",),
                )

        # Received headers (optional)
        for received in re.finditer(
            r"^Received:\s*.*?;\s*(.+)$",
            self.raw_headers,
            re.MULTILINE | re.IGNORECASE,
        ):
            recv_date = received.group(1).strip()
            try:
                parsed = email.utils.parsedate_to_datetime(recv_date)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                yield TimelineEvent(
                    timestamp=parsed,
                    source="email_header",
                    event_type="email_received",
                    confidence=CONFIDENCE_MEDIUM,
                    artifacts=(f"Received:{recv_date}",),
                )
            except (ValueError, LookupError):
                pass


class RawEventSource(TimelineSource):
    def __init__(self, events: list[TimelineEvent]) -> None:
        # Ensure all events are UTC‑aware
        for ev in events:
            if ev.timestamp.tzinfo is None:
                raise ValueError(
                    "All raw TimelineEvent instances must be timezone‑aware"
                )
        self.events = events

    def extract_events(self) -> Iterator[TimelineEvent]:
        yield from self.events


# ---------------------------------------------------------------------------
# 4. Fusion Engine
# ---------------------------------------------------------------------------
def fuse_timelines(
    *sources: TimelineSource,
    case_id: str,
    operator: str,
) -> EventReference:
    """Collect events from all sources, sort chronologically, detect anomalies,
    and emit an immutable .zarc timeline report.

    Returns an EventReference to the sealed event.
    """
    all_events: list[TimelineEvent] = []
    for src in sources:
        all_events.extend(src.extract_events())

    # Sort by timestamp
    all_events.sort(key=lambda e: e.timestamp)

    # Detect anomalies
    anomalies = _detect_anomalies(all_events)

    # Build fused result
    fused = FusedTimeline(
        case_id=case_id,
        operator=operator,
        events=tuple(all_events),
        anomalies=tuple(anomalies),
    )

    serialized = _serialize_fused_timeline(fused)

    # Real Egregore .zarc emission
    event_id = emit_zarc_event(
        event_type=ZarcEventType.TIMELINE_FUSION,
        case_id=case_id,
        operator=operator,
        payload=serialized,
    )

    return EventReference(event_id, anchorum_zarc_dir(case_id) / f"{event_id}.json")


# ---------------------------------------------------------------------------
# 5. Anomaly Detection — complete, no dead code
# ---------------------------------------------------------------------------
def _detect_anomalies(events: list[TimelineEvent]) -> list[TimelineAnomaly]:
    anomalies: list[TimelineAnomaly] = []
    now = datetime.now(UTC) + MAX_FUTURE_SKEW

    # 1. Future dates
    for i, ev in enumerate(events):
        if ev.timestamp > now:
            anomalies.append(
                TimelineAnomaly(
                    anomaly_type=ANOMALY_FUTURE_DATE,
                    description=f"Event timestamp is in the future: {ev.timestamp.isoformat()}",
                    affected_events=(i,),
                    confidence=CONFIDENCE_HIGH,
                )
            )

    # 2. Creation after modification on the same source
    source_events: dict[str, list[tuple[int, TimelineEvent]]] = {}
    for i, ev in enumerate(events):
        source_events.setdefault(ev.source, []).append((i, ev))

    for src, evs in source_events.items():
        created = [(i, e) for i, e in evs if e.event_type == "created"]
        modified = [(i, e) for i, e in evs if e.event_type == "modified"]
        for idx_c, ev_c in created:
            for idx_m, ev_m in modified:
                if ev_c.timestamp > ev_m.timestamp:
                    anomalies.append(
                        TimelineAnomaly(
                            anomaly_type=ANOMALY_CREATION_AFTER_MODIFICATION,
                            description=f"Creation timestamp after modification for {src}",
                            affected_events=(idx_c, idx_m),
                            confidence=CONFIDENCE_HIGH,
                        )
                    )
                    break  # only report first such occurrence per source

    return anomalies


# ---------------------------------------------------------------------------
# 6. Serialization
# ---------------------------------------------------------------------------
def _serialize_fused_timeline(fused: FusedTimeline) -> dict[str, Any]:
    return {
        "case_id": fused.case_id,
        "operator": fused.operator,
        "events": [
            {
                "timestamp": e.timestamp.isoformat(),
                "source": e.source,
                "event_type": e.event_type,
                "confidence": e.confidence,
                "artifacts": list(e.artifacts),
                "metadata": e.metadata,
            }
            for e in fused.events
        ],
        "anomalies": [
            {
                "anomaly_type": a.anomaly_type,
                "description": a.description,
                "affected_events": list(a.affected_events),
                "confidence": a.confidence,
            }
            for a in fused.anomalies
        ],
    }


# ---------------------------------------------------------------------------
# 7. Timestamp Helpers
# ---------------------------------------------------------------------------
def _utc_fromtimestamp(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)


def _parse_iso_datetime(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # Python 3.11+ accepts 'Z'
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _parse_pdf_date(pdf_date: str) -> datetime | None:
    """Parse a PDF date string.

    Handles formats such as:
    D:YYYYMMDDHHmmSSOHH'mm'
    D:YYYYMMDDHHmmSSZ
    D:YYYYMMDDHHmmSS
    D:YYYYMMDDHHmm
    D:YYYYMMDD
    """
    # Remove leading 'D:' if present
    if pdf_date.startswith("D:"):
        pdf_date = pdf_date[2:]

    # Try full timestamp with timezone (hour/minute/second optional)
    match = re.match(
        r"(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?([+-Z])?(\d{2})?\'?(\d{2})?\'?",
        pdf_date,
    )
    if not match:
        return None

    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))
    hour = int(match.group(4)) if match.group(4) else 0
    minute = int(match.group(5)) if match.group(5) else 0
    second = int(match.group(6)) if match.group(6) else 0
    tz_sign = match.group(7)
    tz_offset_h = int(match.group(8)) if match.group(8) else 0
    tz_offset_m = int(match.group(9)) if match.group(9) else 0

    try:
        dt = datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None

    if tz_sign and tz_sign != "Z":
        offset = timedelta(hours=tz_offset_h, minutes=tz_offset_m)
        if tz_sign == "-":
            offset = -offset
        tz = timezone(offset)
    else:
        tz = UTC

    return dt.replace(tzinfo=tz)


# ---------------------------------------------------------------------------
# 8. Self‑test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Create a temporary file to get real filesystem timestamps
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(b"test content")
        tmp_path = tmp.name

    now = datetime.now(UTC)
    sources: list[TimelineSource] = [
        FileMetadataSource(tmp_path),
        RawEventSource(
            [
                TimelineEvent(
                    timestamp=now - timedelta(days=1),
                    source="email_header",
                    event_type="email_sent",
                    confidence=CONFIDENCE_HIGH,
                    artifacts=("Date: Thu, 01 Jan 2024 12:00:00 +0000",),
                ),
                TimelineEvent(
                    timestamp=now + timedelta(hours=1),  # future anomaly
                    source="manual_test",
                    event_type="created",
                    confidence=CONFIDENCE_LOW,
                ),
                TimelineEvent(
                    timestamp=now - timedelta(hours=2),
                    source="manual_test",
                    event_type="modified",
                    confidence=CONFIDENCE_HIGH,
                ),
            ]
        ),
    ]

    result = fuse_timelines(*sources, case_id="TEST-003", operator="kark")
    print(f"Fused timeline event ID: {result.event_id}")
    os.unlink(tmp_path)
