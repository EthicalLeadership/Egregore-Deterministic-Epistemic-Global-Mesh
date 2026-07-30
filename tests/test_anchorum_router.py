from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

# Ensure the API-key middleware has a key available before it is imported.
os.environ.setdefault(
    "EGREGORE_API_KEYS",
    "2c7e17e74e15b30c6813a7bde6ad0be898ef2fdcc1def66046eee6f179d3e7a7:test_tenant:test_user:admin",
)

import canonicaljson
from fastapi.testclient import TestClient

from egregore.interface import anchorum_router
from egregore.interface.bootstrap import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Return a TestClient with an isolated ANCHORUM report directory."""
    monkeypatch.setattr(anchorum_router, "DEFAULT_REPORT_DIR", tmp_path)
    monkeypatch.setattr(anchorum_router, "READ_ONLY_REPORT_DIRS", [])
    anchorum_router._ingest_limiter._buckets.clear()
    return TestClient(create_app(), base_url="http://localhost")


def _write_report(report_dir: Path, case_id: str, payload: dict[str, Any]) -> Path:
    path = report_dir / f"{case_id}_report.json"
    import canonicaljson

    path.write_text(
        canonicaljson.encode_canonical_json(payload).decode(), encoding="utf-8"
    )
    return path


def test_ingest_event_is_public_and_returns_receipt(client: TestClient) -> None:
    resp = client.post(
        "/ingest", json={"source": "test", "entity_value": "x@example.com"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] is True
    assert data["epistemic_state"] == "accepted"
    assert "receipt_id" in data
    assert "event_id" in data


def test_ingest_event_enforces_rate_limit(client: TestClient) -> None:
    # The per-IP bucket has capacity 5; the 6th request should be rejected.
    for _ in range(5):
        resp = client.post("/ingest", json={"source": "test", "entity_value": "a"})
        assert resp.status_code == 200
    resp = client.post("/ingest", json={"source": "test", "entity_value": "a"})
    assert resp.status_code == 429


def test_list_cases_requires_api_key(client: TestClient) -> None:
    resp = client.get("/api/v1/anchorum/cases")
    assert resp.status_code == 401


def test_list_cases_returns_case_ids(client: TestClient, tmp_path: Path) -> None:
    _write_report(tmp_path, "TEST-001", {"case_id": "TEST-001", "artifact_count": 1})
    resp = client.get(
        "/api/v1/anchorum/cases",
        headers={"X-API-Key": os.environ["EGREGORE_API_KEYS"].split(":")[0]},
    )
    assert resp.status_code == 200
    assert resp.json() == ["TEST-001"]


def test_get_case_summary(client: TestClient, tmp_path: Path) -> None:
    _write_report(
        tmp_path,
        "TEST-002",
        {
            "case_id": "TEST-002",
            "artifact_count": 10,
            "entity_count": 3,
            "anomaly_count": 2,
            "critical_findings": [],
            "high_findings": [{"id": "h1"}],
            "medium_findings": [{"id": "m1"}, {"id": "m2"}],
            "low_findings": [],
            "info_findings": [],
            "master_timeline": [],
        },
    )
    resp = client.get(
        "/api/v1/anchorum/cases/TEST-002/summary",
        headers={"X-API-Key": os.environ["EGREGORE_API_KEYS"].split(":")[0]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["case_id"] == "TEST-002"
    assert data["artifact_count"] == 10
    assert data["high_count"] == 1
    assert data["medium_count"] == 2


def test_trigger_batch_sync_copies_report(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Batch/sync should copy the executor's output report to the canonical path."""
    from egregore.cells.executor import CellResult

    work_dir = tmp_path / "BATCH-001_work"
    work_dir.mkdir()
    source_report = work_dir / "anchorum_output" / "BATCH-001_report.json"
    source_report.parent.mkdir(parents=True, exist_ok=True)
    report_payload = {
        "case_id": "BATCH-001",
        "artifact_count": 7,
        "entity_count": 4,
        "anomaly_count": 1,
        "critical_findings": [],
        "high_findings": [],
        "medium_findings": [],
        "low_findings": [],
        "info_findings": [],
        "master_timeline": [],
    }
    source_report.write_text(
        canonicaljson.encode_canonical_json(report_payload).decode(), encoding="utf-8"
    )

    def _fake_run(self: Any, cell_id: str, inputs: dict[str, Any]) -> CellResult:
        return CellResult(
            cell_id=cell_id,
            cell_type="investigation",
            tier=1,
            taxonomy="investigation/forensic/document_analysis",
            request=inputs,
            stages={},
            final_output={"output_path": str(source_report), "highest_severity": "low"},
            verdict="PASS",
            confidence=1.0,
            elapsed_ms=1.0,
            provenance_hash="",
        )

    monkeypatch.setattr(anchorum_router.CellExecutor, "run", _fake_run)

    resp = client.post(
        "/api/v1/anchorum/batch/sync",
        json={"input_path": str(tmp_path), "case_id": "BATCH-001", "operator": "test"},
        headers={"X-API-Key": os.environ["EGREGORE_API_KEYS"].split(":")[0]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["case_id"] == "BATCH-001"
    assert (tmp_path / "BATCH-001_report.json").exists()


def test_trigger_batch_rejects_duplicate_case(
    client: TestClient, tmp_path: Path
) -> None:
    _write_report(tmp_path, "DUP-001", {"case_id": "DUP-001", "artifact_count": 1})
    resp = client.post(
        "/api/v1/anchorum/batch",
        json={"input_path": str(tmp_path), "case_id": "DUP-001", "operator": "test"},
        headers={"X-API-Key": os.environ["EGREGORE_API_KEYS"].split(":")[0]},
    )
    assert resp.status_code == 409
