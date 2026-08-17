"""Tests for ANCHORUM chronology module."""

from __future__ import annotations

from pathlib import Path

import pytest

from asds.application.anchorum_adapter import AnchorumAdapter
from modules.anchorum.chronology import build_chronology


@pytest.fixture
def adapter(tmp_path: Path) -> AnchorumAdapter:
    return AnchorumAdapter(data_dir=tmp_path)


def test_build_chronology_from_email(adapter: AnchorumAdapter) -> None:
    email_bytes = (
        b"From: alice@example.com\n"
        b"To: bob@example.com\n"
        b"Date: Mon, 01 Jan 2024 12:00:00 +0000\n\n"
        b"Hello"
    )
    evidence = adapter.create_evidence(
        workspace_id="ws-test",
        content_ref="inline://email",
        case_id="CASE-CHR",
        content=email_bytes,
    )
    timeline = build_chronology([evidence], adapter)
    assert timeline["event_count"] >= 1
    event_types = {e["event_type"] for e in timeline["events"]}
    assert "email_sent" in event_types
