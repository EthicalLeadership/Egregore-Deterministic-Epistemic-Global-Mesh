"""Tests for CUSTOM-003 Cross-Document Timeline Fusion."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from anchorum.forensic.core.provenance import clear_events, emitted_events
from anchorum.forensic.core.timeline_fusion import (
    EmailHeaderSource,
    FileMetadataSource,
    JsonFileRevisionSource,
    OfficeRevisionSource,
    PdfMetadataSource,
    RawEventSource,
    TimelineEvent,
    _parse_pdf_date,
    fuse_timelines,
)


def test_parse_pdf_date_variants() -> None:
    assert _parse_pdf_date("D:20210101") == datetime(2021, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert _parse_pdf_date("D:202101011200") == datetime(
        2021, 1, 1, 12, 0, 0, tzinfo=UTC
    )
    assert _parse_pdf_date("D:20210101120000Z") == datetime(
        2021, 1, 1, 12, 0, 0, tzinfo=UTC
    )
    offset = timezone(timedelta(hours=-5))
    assert _parse_pdf_date("D:20210101120000-05'00'") == datetime(
        2021, 1, 1, 12, 0, 0, tzinfo=offset
    )
    assert _parse_pdf_date("not a date") is None


def test_email_header_source() -> None:
    headers = (
        "From: sender@example.com\r\n"
        "Date: Thu, 15 Jun 2023 10:30:00 +0000\r\n"
        "Received: from mail.example.com by mx.example.com; Thu, 15 Jun 2023 10:25:00 +0000\r\n"
    )
    events = list(EmailHeaderSource(headers).extract_events())
    assert len(events) == 2
    assert any(e.event_type == "email_sent" for e in events)
    assert any(e.event_type == "email_received" for e in events)


def test_pdf_metadata_source() -> None:
    meta = {"creation_date": "D:20220101120000Z", "mod_date": "D:20221231120000Z"}
    events = list(PdfMetadataSource(meta).extract_events())
    assert len(events) == 2
    assert any(e.event_type == "created" for e in events)
    assert any(e.event_type == "modified" for e in events)


def test_office_revision_source() -> None:
    revisions = [
        {
            "timestamp": "2023-01-01T12:00:00Z",
            "revision_type": "insertion",
            "author": "alice",
        }
    ]
    comments = [
        {"timestamp": "2023-01-02T10:00:00Z", "author": "bob", "resolved": True}
    ]
    events = list(OfficeRevisionSource(revisions, comments).extract_events())
    assert len(events) == 2
    assert any(e.event_type == "revision" for e in events)
    assert any(e.event_type == "comment" for e in events)


def test_json_file_revision_source(tmp_path: Path) -> None:
    report = {
        "revision_history": [
            {
                "timestamp": "2023-03-01T08:00:00Z",
                "revision_type": "deletion",
                "author": "carol",
            }
        ],
        "comments": [],
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    events = list(JsonFileRevisionSource(path).extract_events())
    assert len(events) == 1
    assert events[0].event_type == "revision"


def test_file_metadata_source() -> None:
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(b"test")
        tmp_path = tmp.name
    events = list(FileMetadataSource(tmp_path).extract_events())
    types = {e.event_type for e in events}
    assert "created" in types
    assert "modified" in types
    assert "accessed" in types
    assert "metadata_changed" in types
    Path(tmp_path).unlink()


def test_raw_event_source_rejects_naive() -> None:
    with pytest.raises(ValueError, match="timezone‑aware"):
        RawEventSource(
            [TimelineEvent(timestamp=datetime.now(), source="x", event_type="y")]
        )


def test_fuse_timelines_detects_anomalies() -> None:
    now = datetime.now(UTC)
    sources = [
        RawEventSource(
            [
                TimelineEvent(
                    timestamp=now + timedelta(hours=1),
                    source="manual",
                    event_type="created",
                ),
                TimelineEvent(
                    timestamp=now - timedelta(hours=1),
                    source="manual",
                    event_type="modified",
                ),
            ]
        )
    ]
    ref = fuse_timelines(*sources, case_id="CASE-FUSE", operator="tester")
    assert ref.audit_path.exists()
    report = json.loads(ref.audit_path.read_text())
    payload = report["payload"]
    assert payload["case_id"] == "CASE-FUSE"
    assert len(payload["events"]) == 2
    assert any(a["anomaly_type"] == "future_date" for a in payload["anomalies"])
    assert any(
        a["anomaly_type"] == "creation_after_modification" for a in payload["anomalies"]
    )
    assert len(emitted_events()) == 1


@pytest.fixture(autouse=True)
def _clear_events() -> None:
    clear_events()
