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
    anchorum_router._jobs._jobs.clear()
    return TestClient(create_app(), base_url="http://localhost")


def _api_key() -> str:
    return os.environ["EGREGORE_API_KEYS"].split(":")[0]


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


def _create_completed_job(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    case_id: str = "JOB-001",
) -> str:
    """Run a fake sync batch and return its job_id."""
    from egregore.cells.executor import CellResult

    source_report = tmp_path / f"{case_id}_src" / "anchorum_output" / f"{case_id}_report.json"
    source_report.parent.mkdir(parents=True, exist_ok=True)
    source_report.write_text(
        canonicaljson.encode_canonical_json({"case_id": case_id}).decode(),
        encoding="utf-8",
    )

    def _fake_run(self: Any, cell_id: str, inputs: dict[str, Any]) -> CellResult:
        return CellResult(
            cell_id=cell_id,
            cell_type="investigation",
            tier=1,
            taxonomy="investigation/forensic/document_analysis",
            request=inputs,
            stages={},
            final_output={"output_path": str(source_report)},
            verdict="PASS",
            confidence=1.0,
            elapsed_ms=1.0,
            provenance_hash="",
        )

    monkeypatch.setattr(anchorum_router.CellExecutor, "run", _fake_run)
    resp = client.post(
        "/api/v1/anchorum/batch/sync",
        json={"input_path": str(tmp_path), "case_id": case_id, "operator": "test"},
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 200
    return resp.json()["job_id"]


def test_list_jobs_requires_api_key(client: TestClient) -> None:
    resp = client.get("/api/v1/anchorum/jobs")
    assert resp.status_code == 401


def test_jobs_crud_roundtrip(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _create_completed_job(client, tmp_path, monkeypatch)

    resp = client.get("/api/v1/anchorum/jobs", headers={"X-API-Key": _api_key()})
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert [j["job_id"] for j in jobs] == [job_id]
    assert jobs[0]["status"] == "completed"

    resp = client.get(
        f"/api/v1/anchorum/jobs/{job_id}", headers={"X-API-Key": _api_key()}
    )
    assert resp.status_code == 200
    assert resp.json()["case_id"] == "JOB-001"

    resp = client.delete(
        f"/api/v1/anchorum/jobs/{job_id}", headers={"X-API-Key": _api_key()}
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    resp = client.get(
        f"/api/v1/anchorum/jobs/{job_id}", headers={"X-API-Key": _api_key()}
    )
    assert resp.status_code == 404


def test_get_job_unknown_returns_404(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/anchorum/jobs/does-not-exist",
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 404


def test_delete_job_unknown_returns_404(client: TestClient) -> None:
    resp = client.delete(
        "/api/v1/anchorum/jobs/does-not-exist",
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 404


def test_delete_active_job_marks_cancelled(client: TestClient) -> None:
    job = anchorum_router._jobs.create(
        case_id="JOB-ACTIVE", operator="test", status="running"
    )
    resp = client.delete(
        f"/api/v1/anchorum/jobs/{job['job_id']}",
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"
    assert data["deleted"] is True
    assert "note" in data


# --------------------------------------------------------------- case CRUD
def test_case_crud_roundtrip(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/api/v1/anchorum/cases",
        json={"case_id": "CRUD-001", "operator": "test"},
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 201
    assert (tmp_path / "CRUD-001_report.json").exists()

    resp = client.get("/api/v1/anchorum/cases", headers={"X-API-Key": _api_key()})
    assert "CRUD-001" in resp.json()

    resp = client.get(
        "/api/v1/anchorum/cases/CRUD-001/summary", headers={"X-API-Key": _api_key()}
    )
    assert resp.status_code == 200
    assert resp.json()["artifact_count"] == 0

    # Duplicate create -> 409
    resp = client.post(
        "/api/v1/anchorum/cases",
        json={"case_id": "CRUD-001", "operator": "test"},
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 409

    resp = client.delete(
        "/api/v1/anchorum/cases/CRUD-001", headers={"X-API-Key": _api_key()}
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert not (tmp_path / "CRUD-001_report.json").exists()

    resp = client.get(
        "/api/v1/anchorum/cases/CRUD-001", headers={"X-API-Key": _api_key()}
    )
    assert resp.status_code == 404


def test_create_case_rejects_unsafe_id(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/anchorum/cases",
        json={"case_id": "../escape", "operator": "test"},
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 422


def test_delete_unknown_case_returns_404(client: TestClient) -> None:
    resp = client.delete(
        "/api/v1/anchorum/cases/NOPE-404", headers={"X-API-Key": _api_key()}
    )
    assert resp.status_code == 404


def test_delete_read_only_case_returns_409(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ro_dir = tmp_path / "ro_reports"
    ro_dir.mkdir()
    _write_report(ro_dir, "RO-001", {"case_id": "RO-001", "artifact_count": 1})
    monkeypatch.setattr(anchorum_router, "READ_ONLY_REPORT_DIRS", [ro_dir])
    resp = client.delete(
        "/api/v1/anchorum/cases/RO-001", headers={"X-API-Key": _api_key()}
    )
    assert resp.status_code == 409
    assert (ro_dir / "RO-001_report.json").exists()


def test_batch_allowed_over_empty_manual_case(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty skeleton from POST /cases must not block a batch run."""
    from egregore.cells.executor import CellResult

    resp = client.post(
        "/api/v1/anchorum/cases",
        json={"case_id": "SHELL-001", "operator": "test"},
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 201

    source_report = tmp_path / "w" / "anchorum_output" / "SHELL-001_report.json"
    source_report.parent.mkdir(parents=True, exist_ok=True)
    source_report.write_text(
        canonicaljson.encode_canonical_json({"case_id": "SHELL-001"}).decode(),
        encoding="utf-8",
    )

    def _fake_run(self: Any, cell_id: str, inputs: dict[str, Any]) -> CellResult:
        return CellResult(
            cell_id=cell_id,
            cell_type="investigation",
            tier=1,
            taxonomy="investigation/forensic/document_analysis",
            request=inputs,
            stages={},
            final_output={"output_path": str(source_report)},
            verdict="PASS",
            confidence=1.0,
            elapsed_ms=1.0,
            provenance_hash="",
        )

    monkeypatch.setattr(anchorum_router.CellExecutor, "run", _fake_run)
    # Use the async /batch route: its duplicate guard is the one that must
    # treat an empty manual skeleton as overwritable. TestClient runs
    # BackgroundTasks before returning, so the job has completed here.
    resp = client.post(
        "/api/v1/anchorum/batch",
        json={"input_path": str(tmp_path), "case_id": "SHELL-001", "operator": "test"},
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    resp = client.get(
        f"/api/v1/anchorum/jobs/{job_id}", headers={"X-API-Key": _api_key()}
    )
    assert resp.json()["status"] == "completed"


# --------------------------------------------------------------- case sources
def test_get_case_sources(client: TestClient, tmp_path: Path) -> None:
    _write_report(tmp_path, "SRC-001", {"case_id": "SRC-001", "artifact_count": 1})
    resp = client.get(
        "/api/v1/anchorum/cases/SRC-001/sources", headers={"X-API-Key": _api_key()}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["case_id"] == "SRC-001"
    assert any(s["source_type"] == "report" for s in data["sources"])
    report_source = next(s for s in data["sources"] if s["source_type"] == "report")
    assert report_source["sha256"]
    assert report_source["size"] > 0


def test_attach_case_sources(client: TestClient, tmp_path: Path) -> None:
    _write_report(tmp_path, "SRC-002", {"case_id": "SRC-002", "artifact_count": 1})
    extra = tmp_path / "extra_evidence"
    extra.mkdir()
    (extra / "note.txt").write_text("key fact", encoding="utf-8")

    resp = client.post(
        "/api/v1/anchorum/cases/SRC-002/sources/attach",
        json={"extra_dirs": [str(extra)]},
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert str(extra) in data["extra_dirs"]

    # Sources endpoint now includes the attached file.
    resp = client.get(
        "/api/v1/anchorum/cases/SRC-002/sources", headers={"X-API-Key": _api_key()}
    )
    assert resp.status_code == 200
    assert any(s["source_type"] == "evidence" for s in resp.json()["sources"])


def test_attach_case_sources_rejects_missing_dir(
    client: TestClient, tmp_path: Path
) -> None:
    _write_report(tmp_path, "SRC-003", {"case_id": "SRC-003", "artifact_count": 1})
    resp = client.post(
        "/api/v1/anchorum/cases/SRC-003/sources/attach",
        json={"extra_dirs": ["/does/not/exist"]},
        headers={"X-API-Key": _api_key()},
    )
    assert resp.status_code == 422


# --------------------------------------------------------------- tools registry
def test_list_tools_returns_registry(client: TestClient) -> None:
    resp = client.get("/api/v1/anchorum/tools", headers={"X-API-Key": _api_key()})
    assert resp.status_code == 200
    data = resp.json()
    assert "tools" in data
    ids = {t["id"] for t in data["tools"]}
    assert "red-dart" in ids
    assert "ocint" in ids
