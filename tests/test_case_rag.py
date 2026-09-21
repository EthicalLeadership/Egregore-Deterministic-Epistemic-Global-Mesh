"""Tests for the per-case RAG index (case_rag) and its API routes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault(
    "EGREGORE_API_KEYS",
    "2c7e17e74e15b30c6813a7bde6ad0be898ef2fdcc1def66046eee6f179d3e7a7:test_tenant:test_user:admin",
)

from fastapi.testclient import TestClient

from egregore.interface import anchorum_router, case_rag
from egregore.interface.bootstrap import create_app


@pytest.fixture
def case_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated report dir + isolated per-case vector store root."""
    monkeypatch.setattr(anchorum_router, "DEFAULT_REPORT_DIR", tmp_path)
    monkeypatch.setattr(anchorum_router, "READ_ONLY_REPORT_DIRS", [])
    monkeypatch.setenv("EGREGORE_CASE_RAG_ROOT", str(tmp_path / "rag_cases"))
    return tmp_path


def _make_case(
    report_dir: Path,
    case_id: str = "RAG-001",
    transcript_text: str = (
        "The caller discussed the missed insurance payment of March 3rd "
        "and asked when the file would be reopened."
    ),
) -> None:
    anchorum_router._report_dir().mkdir(parents=True, exist_ok=True)
    artifact_id = "a" * 64
    report = {
        "case_id": case_id,
        "report_id": f"r-{case_id}",
        "artifact_count": 1,
        "entity_count": 0,
        "anomaly_count": 1,
        "critical_findings": [],
        "high_findings": [
            {"anomaly_type": "METADATA_SCRUB", "description": "Metadata scrubbing detected in invoice.pdf"}
        ],
        "medium_findings": [],
        "low_findings": [],
        "info_findings": [],
        "master_timeline": [],
        "entity_directory": [],
        "audio_transcripts": [
            {"artifact_id": artifact_id, "original_filename": "Call 811_test.m4a"}
        ],
    }
    import json

    (report_dir / f"{case_id}_report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )
    tdir = report_dir / f"{case_id}_work" / "anchorum_output" / "transcripts"
    tdir.mkdir(parents=True)
    (tdir / f"{artifact_id}.txt").write_text(transcript_text, encoding="utf-8")


def test_index_and_query_case(case_env: Path) -> None:
    _make_case(case_env)
    stats = case_rag.index_case("RAG-001")
    assert stats["documents"] == 2  # report + transcript
    assert stats["chunks"] >= 2

    hits = case_rag.query_case("RAG-001", "missed insurance payment", top_k=3)
    assert hits
    assert any("insurance payment" in h["document"] for h in hits)
    assert any("Call 811_test.m4a" in h["source"] for h in hits)
    assert all(h.get("source_type") in {"report", "transcript"} for h in hits)


def test_query_without_index_returns_empty(case_env: Path) -> None:
    assert case_rag.query_case("RAG-002", "anything") == []


def test_delete_case_index(case_env: Path) -> None:
    _make_case(case_env)
    case_rag.index_case("RAG-001")
    assert case_rag.case_index_dir("RAG-001").exists()
    assert case_rag.delete_case_index("RAG-001") is True
    assert not case_rag.case_index_dir("RAG-001").exists()
    assert case_rag.delete_case_index("RAG-001") is False


def test_rag_api_routes(case_env: Path) -> None:
    _make_case(case_env)
    client = TestClient(create_app(), base_url="http://localhost")
    key = os.environ["EGREGORE_API_KEYS"].split(":")[0]

    resp = client.post("/api/v1/anchorum/cases/RAG-001/rag/index", headers={"X-API-Key": key})
    assert resp.status_code == 200
    assert resp.json()["chunks"] >= 2

    resp = client.post(
        "/api/v1/anchorum/cases/RAG-001/rag/query",
        json={"query": "what did the caller ask about", "top_k": 2},
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 200
    chunks = resp.json()["chunks"]
    assert chunks and "file would be reopened" in chunks[0]["document"]

    resp = client.post("/api/v1/anchorum/cases/NOPE/rag/index", headers={"X-API-Key": key})
    assert resp.status_code == 404

    # Case deletion removes the vector store too.
    resp = client.delete("/api/v1/anchorum/cases/RAG-001", headers={"X-API-Key": key})
    assert resp.status_code == 200
    assert resp.json()["index_removed"] is True
