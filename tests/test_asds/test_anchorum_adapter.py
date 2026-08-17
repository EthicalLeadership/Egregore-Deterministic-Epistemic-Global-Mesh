"""Tests for the ASDS -> ANCHORUM port adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from asds.application.anchorum_adapter import AnchorumAdapter


@pytest.fixture
def adapter(tmp_path: Path) -> AnchorumAdapter:
    return AnchorumAdapter(data_dir=tmp_path)


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    # Minimal valid-looking PDF header + catalog object
    path.write_bytes(
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n",
    )
    return path


def test_create_evidence_from_path(adapter: AnchorumAdapter, sample_pdf: Path) -> None:
    evidence = adapter.create_evidence(
        workspace_id="ws-test",
        content_ref=str(sample_pdf),
        case_id="CASE-001",
    )
    assert evidence.workspace_id == "ws-test"
    assert evidence.case_id == "CASE-001"
    assert evidence.container_type == "pdf"
    assert evidence.mime_type == "application/pdf"
    assert evidence.size_bytes == sample_pdf.stat().st_size

    # Record was persisted
    record_path = (
        Path(adapter.data_dir)
        / "workspaces"
        / "ws-test"
        / "evidence"
        / f"{evidence.id}.json"
    )
    assert record_path.exists()
    record = json.loads(record_path.read_text())
    assert record["content_ref"] == str(sample_pdf)


def test_get_evidence_content(adapter: AnchorumAdapter, sample_pdf: Path) -> None:
    evidence = adapter.create_evidence(
        workspace_id="ws-test", content_ref=str(sample_pdf)
    )
    content = adapter.get_evidence_content(evidence.id)
    assert content == sample_pdf.read_bytes()


def test_register_analysis_output(adapter: AnchorumAdapter, sample_pdf: Path) -> None:
    evidence = adapter.create_evidence(
        workspace_id="ws-test", content_ref=str(sample_pdf)
    )
    output = adapter.register_analysis_output(
        evidence_id=evidence.id,
        analysis_type="metadata",
        result_json={"foo": "bar"},
        content_hash="abc123",
    )
    assert output.evidence_id == evidence.id
    assert output.analysis_type == "metadata"
    assert output.version_number == 1

    output_path = Path(output.content_ref.replace("file://", ""))
    assert output_path.exists()
    assert json.loads(output_path.read_text()) == {"foo": "bar"}


def test_get_case_evidence(adapter: AnchorumAdapter, sample_pdf: Path) -> None:
    adapter.create_evidence(
        workspace_id="ws-test", content_ref=str(sample_pdf), case_id="CASE-002"
    )
    results = adapter.get_case_evidence("CASE-002")
    assert len(results) == 1
    assert results[0].case_id == "CASE-002"


def test_emit_bus_event(adapter: AnchorumAdapter) -> None:
    adapter.emit_bus_event("test.event", {"x": 1})
    journal = Path(adapter.data_dir) / "bus_events.jsonl"
    assert journal.exists()
    lines = journal.read_text().strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event_type"] == "test.event"
    assert event["payload"] == {"x": 1}
