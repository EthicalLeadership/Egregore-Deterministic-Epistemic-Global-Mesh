"""Tests for ANCHORUM module FastAPI routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from egregore.http_api.http.app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ASDS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "EGREGORE_API_KEYS",
        "0000000000000000000000000000000000000000000000000000000000000000:test:kark:operator",
    )
    app = create_app(build_container=False)
    test_key = "0000000000000000000000000000000000000000000000000000000000000000"
    return TestClient(app, headers={"X-API-Key": test_key})


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    path.write_bytes(
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Producer (Test) >>\nendobj\n",
    )
    return path


def test_ingest_route(client: TestClient, sample_pdf: Path) -> None:
    resp = client.post(
        "/modules/anchorum/ingest",
        json={
            "workspace_id": "ws-routes",
            "content_ref": str(sample_pdf),
            "case_id": "CASE-RT",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["evidence"]["container_type"] == "pdf"
    assert body["output"]["analysis_type"] == "metadata"


def test_analyze_route(client: TestClient, sample_pdf: Path) -> None:
    # First ingest
    ingest_resp = client.post(
        "/modules/anchorum/ingest",
        json={
            "workspace_id": "ws-analyze",
            "content_ref": str(sample_pdf),
        },
    )
    evidence_id = ingest_resp.json()["evidence"]["id"]

    resp = client.post(
        "/modules/anchorum/analyze",
        json={"evidence_id": evidence_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["evidence_id"] == evidence_id
    assert "report" in body


def test_chronology_route(client: TestClient, tmp_path: Path) -> None:
    email = tmp_path / "email.eml"
    email.write_bytes(
        b"From: a@example.com\n"
        b"Date: Mon, 01 Jan 2024 12:00:00 +0000\n\n"
        b"body"
    )
    client.post(
        "/modules/anchorum/ingest",
        json={
            "workspace_id": "ws-chrono",
            "content_ref": str(email),
            "case_id": "CASE-CHR",
        },
    )

    resp = client.get("/modules/anchorum/chronology/CASE-CHR")
    assert resp.status_code == 200
    body = resp.json()
    assert body["case_id"] == "CASE-CHR"
    assert "timeline" in body
